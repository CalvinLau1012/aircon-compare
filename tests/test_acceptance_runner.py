# -*- coding: utf-8 -*-
"""acceptance runner 回歸：真實 rc、machine report、log hash、寫入失敗非零"""
import importlib.util
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'run_acceptance_mod', os.path.join(BASE, 'scripts', 'run_acceptance.py'))
ra = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ra)


def _gates(tmp_path):
    return [
        ('GOOD', [sys.executable, '-c', 'print("ok")']),
        ('BAD', [sys.executable, '-c', 'import sys; print("no"); sys.exit(3)']),
    ]


def test_runner_records_real_rc_and_logs(tmp_path):
    report = tmp_path / 'acceptance.json'
    rc = ra.run_gates(_gates(tmp_path), str(tmp_path / 'logs'), str(report))
    assert rc == 1, '有 gate 非零要整體非零'
    data = json.load(open(report, encoding='utf-8'))
    assert data['ok'] is False
    by_id = {g['id']: g for g in data['gates']}
    assert by_id['GOOD']['returncode'] == 0
    assert by_id['BAD']['returncode'] == 3, 'runner 要直接取 subprocess rc（唔經 pipe）'
    for g in data['gates']:
        log = tmp_path / g['log']
        assert log.is_file()
        assert ra._sha256_file(str(log)) == g['logSha256']
        assert g['argv'][0] == 'python', 'interpreter 要正規化為 python'
        assert g['startedAt'] and g['finishedAt']


def test_runner_all_pass_reports_ok(tmp_path):
    report = tmp_path / 'acceptance.json'
    rc = ra.run_gates([('GOOD', [sys.executable, '-c', 'print(1)'])],
                      str(tmp_path / 'logs'), str(report))
    assert rc == 0
    assert json.load(open(report, encoding='utf-8'))['ok'] is True


def test_runner_report_write_failure_nonzero(tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    rc = ra.run_gates([('GOOD', [sys.executable, '-c', 'print(1)'])],
                      str(tmp_path / 'logs'), str(blocker / 'acceptance.json'))
    assert rc == 1


def test_runner_timeout_is_failure(tmp_path):
    report = tmp_path / 'acceptance.json'
    rc = ra.run_gates([('SLOW', [sys.executable, '-c', 'import time; time.sleep(5)'])],
                      str(tmp_path / 'logs'), str(report), timeout=1)
    assert rc == 1
    data = json.load(open(report, encoding='utf-8'))
    assert data['gates'][0]['returncode'] == -2


# ---------------------------------------------------------------- R5 trust boundary

def test_runner_rejects_duplicate_and_unsafe_gate_ids(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        ra.run_gates([('GOOD', [sys.executable, '-c', 'print(1)']),
                      ('GOOD', [sys.executable, '-c', 'print(1)'])],
                     str(tmp_path / 'logs'), str(tmp_path / 'a.json'))
    with pytest.raises(ValueError):
        ra.run_gates([('../evil', [sys.executable, '-c', 'print(1)'])],
                     str(tmp_path / 'logs'), str(tmp_path / 'a.json'))
    with pytest.raises(ValueError):
        ra.run_gates([('lower', [sys.executable, '-c', 'print(1)'])],
                     str(tmp_path / 'logs'), str(tmp_path / 'a.json'))


def test_runner_cli_rejects_unknown_only(tmp_path):
    rc = ra.main(['--report', str(tmp_path / 'a.json'), '--log-dir', str(tmp_path / 'logs'),
                  '--only', 'PYTEST', 'UNKNOWN_GATE'])
    assert rc == 1


def test_runner_cli_has_no_arbitrary_spec(tmp_path):
    import pytest
    with pytest.raises(SystemExit) as exc:
        ra.main(['--report', str(tmp_path / 'a.json'), '--log-dir', str(tmp_path / 'logs'),
                 '--spec', str(tmp_path / 'spec.json')])
    assert exc.value.code == 2, '--spec 已移除（CLI 唔可以執行任意 argv）'


def test_runner_rejects_repo_paths_without_override():
    repo_report = os.path.join(ra.BASE, 'acceptance-danger.json')
    rc = ra.main(['--report', repo_report, '--log-dir', os.path.join(ra.BASE, 'logs-danger')])
    assert rc == 1, 'CI 唔應該喺 repo 內寫 machine evidence'
    assert not os.path.exists(repo_report)


def test_runner_allows_repo_paths_with_explicit_override(tmp_path):
    rc = ra.main(['--report', str(tmp_path / 'a.json'), '--log-dir', str(tmp_path / 'logs'),
                  '--allow-repo-paths', '--only', 'GOVERNANCE_EXTRACT'])
    assert rc == 0
