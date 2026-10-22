# -*- coding: utf-8 -*-
"""R5: shared workflow env, receive-only, and fixed source pin lifecycle."""
import json
import os
import shutil
import subprocess
import sys

import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import price_runtime  # noqa: E402
import verify_price_source_pin as pin  # noqa: E402


def _steps():
    wf = yaml.safe_load(open(os.path.join(BASE, '.github/workflows/daily-update.yml'),
                             encoding='utf-8'))
    return {s.get('name'): s for job in wf['jobs'].values()
            for s in job.get('steps', []) if s.get('name')}


def test_workflow_price_entry_and_runner_share_required_mode_identity():
    steps = _steps()
    entry = next(s for n, s in steps.items() if '價格需求' in n)
    runner = next(s for n, s in steps.items() if '價錢快照分批更新' in n)
    keys = {'AIRCON_PRICE_QUERY_MODE', 'AIRCON_PRICE_CYCLE_ENABLED',
            'AIRCON_PRICE_SHARED_CYCLE', 'AIRCON_PRICE_SOURCE_REVISION',
            'AIRCON_PRICE_WRITER'}
    assert keys <= set(entry['env'])
    assert keys <= set(runner['env'])
    for key in keys:
        assert entry['env'][key] == runner['env'][key], key
    assert any('verify_price_source_pin.py' in s.get('run', '')
               for s in steps.values())


def test_query_mode_defaults_receive_only_and_never_starts_or_calls_provider():
    assert price_runtime.query_mode({}) == 'receive-only'
    assert price_runtime.may_start({}) is False
    assert price_runtime.may_call_provider({}) is False
    assert price_runtime.may_start({'AIRCON_PRICE_QUERY_MODE': 'normal',
                                    'AIRCON_PRICE_CYCLE_ENABLED': '1',
                                    'AIRCON_PRICE_SHARED_CYCLE': '1'})
    assert price_runtime.may_call_provider({'AIRCON_PRICE_QUERY_MODE': 'normal',
                                            'AIRCON_PRICE_CYCLE_ENABLED': '1',
                                            'AIRCON_PRICE_SHARED_CYCLE': '1'})
    assert price_runtime.may_start({'AIRCON_PRICE_QUERY_MODE': 'fixture-local',
                                    'AIRCON_PRICE_FIXTURE': '1'})
    try:
        price_runtime.shared_flags({'AIRCON_PRICE_CYCLE_ENABLED': '1'})
    except price_runtime.PriceRuntimeError:
        pass
    else:
        raise AssertionError('mixed flags must fail closed')


def test_real_stage_runner_receive_only_has_zero_provider_calls(tmp_path):
    from scripts import biggo_stage_runner as runner

    class Fetch:
        calls = 0
        def __getattr__(self, name):
            def f(*a, **k):
                self.calls += 1
                raise AssertionError('provider request in receive-only')
            return f

    fetch = Fetch()
    out = []
    status = tmp_path / 'status.json'
    code = runner.run_stage(env={'AIRCON_PRICE_QUERY_MODE': 'receive-only',
                                 'AIRCON_BIGGO_FORCE_STAGE': 'true'},
                            fetch_mod=fetch, status_out=str(status),
                            print_fn=out.append,
                            apply_fn=lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError('apply in receive-only')),
                            repo_root=str(tmp_path))
    assert code == 0 and fetch.calls == 0
    doc = json.loads(status.read_text())
    assert doc['status'] == 'skip-receive-only'
    assert doc['requests'] == {'token': 0, 'search': 0}


def _git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True)


def test_source_pin_survives_data_child_commit_and_rejects_code_drift(tmp_path):
    repo = tmp_path / 'repo'
    for rel in pin.CODE_PATHS:
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = os.path.join(BASE, rel)
        if not os.path.exists(src):
            alt = os.path.join(BASE, 'scripts', os.path.basename(rel))
            src = alt if os.path.exists(alt) else src
        shutil.copy2(src, dst)
    (repo / 'new_models.json').write_text('{"models":[]}', encoding='utf-8')
    _git(repo, 'init', '-q'); _git(repo, 'config', 'user.email', 't@e.invalid')
    _git(repo, 'config', 'user.name', 't'); _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'code')
    rev = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    assert pin.verify_git(str(repo), rev)['ok'] is True
    (repo / 'new_models.json').write_text('{"models":[{"model":"X"}]}', encoding='utf-8')
    _git(repo, 'add', '-A'); _git(repo, 'commit', '-qm', 'data child')
    # The old ack revision remains valid across a pure data commit.
    assert pin.verify_git(str(repo), rev)['ok'] is True
    with open(repo / 'fetch_biggo.py', 'a', encoding='utf-8') as f:
        f.write('\n# code drift\n')
    try:
        pin.verify_git(str(repo), rev)
    except ValueError as e:
        assert 'source-closure-mismatch' in str(e)
    else:
        raise AssertionError('code drift must require a new ack revision')
    # Image mode compares /app to the immutable image source tree.
    source = tmp_path / 'source'
    shutil.copytree(repo, source, ignore=shutil.ignore_patterns('.git'))
    (source / '.release-commit').write_text(rev + '\n', encoding='utf-8')
    with open(repo / 'fetch_biggo.py', 'w', encoding='utf-8') as f:
        f.write((source / 'fetch_biggo.py').read_text())
    assert pin.verify_image(str(repo), str(source), rev)['ok'] is True
    with open(repo / 'fetch_biggo.py', 'a', encoding='utf-8') as f:
        f.write('\n# drift\n')
    try:
        pin.verify_image(str(repo), str(source), rev)
    except ValueError as e:
        assert 'image-source-mismatch' in str(e)
    else:
        raise AssertionError('image drift must fail')


def test_receive_only_status_is_legal_source_status_idle(tmp_path):
    import source_status
    snap = {'A': {'price': '$1起'}}
    from biggo_canonical import sha256_json
    h = sha256_json(snap)
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('a\n', encoding='utf-8')
    status = source_status.build_status(
        metadata={'datasetDate': '2026-10-22', 'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta={'last_run': '2026-10-21', 'last_price_month': '2026-10',
                     'appliedSnapshotHash': h, 'appliedBundleHash': 'sha256:' + 'b' * 64},
        biggo_status={'status': 'skip-receive-only', 'at': '2026-10-22T00:00:00Z'},
        biggo_snapshot_hash=h)
    assert status['biggo']['status'] == 'ok'
    assert status['biggo']['safeReason'] == 'no-fetch-needed'
