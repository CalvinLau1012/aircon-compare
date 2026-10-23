"""Daily policy loaded before precommit must not configure test fixtures."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'candidate_acceptance_isolation', ROOT / 'scripts/verify_candidate_acceptance.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)

POLICY = {
    'AIRCON_PRICE_QUERY_MODE': 'normal',
    'AIRCON_PRICE_CYCLE_ENABLED': '1',
    'AIRCON_PRICE_SHARED_CYCLE': '1',
    'AIRCON_PRICE_SOURCE_REVISION': 'a' * 40,
    'AIRCON_PRICE_WRITER': 'github',
}


def _parent_policy(monkeypatch):
    for key, value in POLICY.items():
        monkeypatch.setenv(key, value)


def test_environment_preserves_dependencies_without_parent_business_authority():
    original = {**POLICY, 'AIRCON_BIGGO_COORDINATOR_TOKEN': 'fake-coordinator',
                'BIGGO_CLIENT_SECRET': 'fake-provider', 'GH_TOKEN': 'fake-github',
                'GITHUB_ENV': '/fake/run-env', 'PATH': '/tools',
                'LD_LIBRARY_PATH': '/libs', 'GITHUB_ACTIONS': 'true',
                'AIRCON_RECOVERY_EVIDENCE': '/offline-evidence'}
    before = dict(original)
    clean = candidate.acceptance_environment(original)
    assert original == before
    assert clean == {'PATH': '/tools', 'LD_LIBRARY_PATH': '/libs',
                     'GITHUB_ACTIONS': 'true',
                     'AIRCON_RECOVERY_EVIDENCE': '/offline-evidence'}


def test_real_child_is_isolated_and_parent_policy_is_unchanged(tmp_path, monkeypatch):
    _parent_policy(monkeypatch)
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', 'fake-provider')
    log = tmp_path / 'child.log'
    script = ('import os; '
              'assert not any(k.startswith(("AIRCON_PRICE_", "BIGGO_")) '
              'for k in os.environ); print("isolated")')
    entry = candidate._run([sys.executable, '-c', script], str(ROOT), str(log), 30)
    assert entry['returncode'] == 0
    assert log.read_text().strip() == 'isolated'
    assert all(os.environ[key] == value for key, value in POLICY.items())
    assert os.environ['BIGGO_CLIENT_SECRET'] == 'fake-provider'


def test_actual_pending_chain_under_daily_parent_policy(tmp_path, monkeypatch):
    _parent_policy(monkeypatch)
    log = tmp_path / 'pending-chain.log'
    entry = candidate._run([
        sys.executable, '-B', '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
        'tests/test_official_pending_policy_20261021.py::'
        'test_positive_real_chain_trusted_pending_is_operations_healthy',
        '--basetemp', str(tmp_path / 'fixtures'),
    ], str(ROOT), str(log), 60)
    assert entry['returncode'] == 0, log.read_text()
    assert '3 passed' in log.read_text()
    assert all(os.environ[key] == value for key, value in POLICY.items())


def test_real_candidate_failure_keeps_nonzero_and_prints_gate_log(tmp_path, capsys):
    repo = tmp_path / 'repo'
    scripts = repo / 'scripts'
    scripts.mkdir(parents=True)
    (scripts / 'run_acceptance.py').write_text(
        'import argparse,json,pathlib,sys\n'
        'p=argparse.ArgumentParser();p.add_argument("--report");'
        'p.add_argument("--log-dir");a=p.parse_args()\n'
        'd=pathlib.Path(a.log_dir);d.mkdir(parents=True);'
        '(d/"PYTEST.log").write_text("AssertionError: deliberate gate failure")\n'
        'pathlib.Path(a.report).write_text(json.dumps({"ok":False,"gates":['
        '{"id":"PYTEST","returncode":3,"log":"acceptance-logs/PYTEST.log"}]}))\n'
        'sys.exit(3)\n')
    for argv in [['git', 'init', '-q'], ['git', 'add', '-A'],
                 ['git', '-c', 'user.name=t', '-c', 'user.email=t@invalid',
                  'commit', '-qm', 'fixture']]:
        subprocess.run(argv, cwd=repo, check=True)
    report = tmp_path / 'evidence/report.json'
    rc = candidate.main(['--repo', str(repo), '--report', str(report),
                         '--log-dir', str(report.parent / 'logs')])
    saved = json.loads(report.read_text())
    assert rc == 1 and saved['ok'] is False
    assert saved['commands'][0]['returncode'] == 3
    shown = capsys.readouterr().out
    assert 'candidate gate PYTEST: rc=3' in shown
    assert 'AssertionError: deliberate gate failure' in shown
    assert saved['acceptance']['gates'][0]['returncode'] == 3


def test_failure_log_cannot_escape_evidence_directory(tmp_path, capsys):
    outside = tmp_path / 'outside.log'
    outside.write_text('outside-private-content')
    logs = tmp_path / 'logs'
    logs.mkdir()
    candidate._print_failure_details({'gates': [
        {'id': 'PYTEST', 'returncode': 1, 'log': '../outside.log'}]}, str(logs))
    shown = capsys.readouterr().out
    assert 'outside-private-content' not in shown
    assert 'not displayed' in shown
