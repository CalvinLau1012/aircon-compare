# -*- coding: utf-8 -*-
"""Execute the actual daily workflow finalizer shell blocks with fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml

BASE = Path(__file__).resolve().parents[1]
RUN = '123456789'
HEAD = 'a' * 40


def _steps():
    wf = yaml.safe_load((BASE / '.github/workflows/daily-update.yml').read_text(encoding='utf-8'))
    update = wf['jobs']['update']
    return {s['name']: s for s in update['steps'] if 'name' in s}


def _run(script, env):
    full = {'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', ''),
            'RUNNER_TEMP': env['RUNNER_TEMP'],
            'GITHUB_RUN_ID': RUN, 'GITHUB_SHA': HEAD,
            'GITHUB_EVENT_NAME': 'schedule', **env}
    return subprocess.run(['bash', '-lc', script], cwd=BASE, env=full,
                          capture_output=True, text=True)


def _sync(tmp_path, **env):
    s = _steps()['綁定 completed sync report 來源 identity（always）']
    e = {'SYNC_RUN_ID': RUN, 'SYNC_HEAD_SHA': HEAD, 'SYNC_EVENT': 'schedule',
         'SYNC_OUTCOME': env.pop('SYNC_OUTCOME', 'skipped'),
         'SYNC_JOB_STATUS': env.pop('SYNC_JOB_STATUS', 'failure'),
         'RUNNER_TEMP': str(tmp_path), **env}
    return _run(s['run'], e), tmp_path / 'biggo_completed_sync.json'


def _biggo(tmp_path, **env):
    s = _steps()['BigGo 上游／provider 失敗 receipt（只反映未執行或未知，不假稱 provider）']
    e = {'BIGGO_STEP_OUTCOME': env.pop('BIGGO_STEP_OUTCOME', 'skipped'),
         'BIGGO_JOB_STATUS': env.pop('BIGGO_JOB_STATUS', 'failure'),
         'RUNNER_TEMP': str(tmp_path), **env}
    return _run(s['run'], e), tmp_path / 'biggo-stage-status.json'


def test_shell_sync_fallback_for_pretest_and_all_early_failures(tmp_path):
    for label in ('test', 'browser', 'gov', 'EMSD'):
        d = tmp_path / label
        d.mkdir()
        proc, report = _sync(d)
        assert proc.returncode == 0, (label, proc.stderr)
        got = json.loads(report.read_text())
        assert got['ok'] is False and got['notAttempted'] is True
        assert got['reason'] == 'upstream-blocked'
        assert got['sourceRunId'] == int(RUN) and got['headSha'] == HEAD


def test_shell_sync_does_not_fabricate_after_actual_receive_or_corrupt(tmp_path):
    d = tmp_path / 'attempted'
    d.mkdir()
    proc, report = _sync(d, SYNC_OUTCOME='failure', SYNC_JOB_STATUS='failure')
    assert proc.returncode != 0 and not report.exists()
    d2 = tmp_path / 'corrupt'
    d2.mkdir()
    (d2 / 'biggo_completed_sync.json').write_text('{broken', encoding='utf-8')
    before = (d2 / 'biggo_completed_sync.json').read_bytes()
    proc, report = _sync(d2)
    assert proc.returncode != 0 and report.read_bytes() == before


def test_shell_sync_preserves_real_report_and_binds_identity(tmp_path):
    d = tmp_path / 'real'
    d.mkdir()
    p = d / 'biggo_completed_sync.json'
    p.write_text(json.dumps({'schemaVersion': 2, 'mode': 'sync-completed',
                             'ok': False, 'applied': False,
                             'reason': 'apply-error',
                             'generatedAt': '2026-10-20T00:00:00Z'}), encoding='utf-8')
    proc, report = _sync(d)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(report.read_text())
    assert got['reason'] == 'apply-error' and got['ok'] is False
    assert got['sourceRunId'] == int(RUN)


def test_shell_biggo_upstream_and_unknown_are_distinct(tmp_path):
    d = tmp_path / 'up'; d.mkdir()
    proc, status = _biggo(d, BIGGO_STEP_OUTCOME='skipped', BIGGO_JOB_STATUS='failure')
    assert proc.returncode == 0, proc.stderr
    got = json.loads(status.read_text())
    assert got['status'] == 'blocked-upstream'
    assert got['providerPhaseEntered'] is False
    assert got['requests'] == {'token': 0, 'search': 0}
    d2 = tmp_path / 'unknown'; d2.mkdir()
    proc, status = _biggo(d2, BIGGO_STEP_OUTCOME='failure', BIGGO_JOB_STATUS='failure')
    assert proc.returncode == 0, proc.stderr
    got = json.loads(status.read_text())
    assert got['status'] == 'blocked-provider-unknown'
    assert got['requests'] is None and got['providerPhaseEntered'] is None


def test_shell_biggo_official_after_receive_keeps_sync_and_does_not_rewrite_status(tmp_path):
    d = tmp_path / 'official-after-receive'; d.mkdir()
    sync = d / 'biggo_completed_sync.json'
    sync.write_text(json.dumps({'schemaVersion': 2, 'mode': 'sync-completed',
                                'ok': True, 'applied': True,
                                'reason': 'applied',
                                'generatedAt': '2026-10-20T00:00:00Z'}), encoding='utf-8')
    existing = d / 'biggo-stage-status.json'
    real = {'schemaVersion': 1, 'status': 'skip-cooldown', 'alert': False,
            'rerun': 'allowed', 'requests': {'token': 0, 'search': 0}}
    existing.write_text(json.dumps(real), encoding='utf-8')
    before = existing.read_bytes()
    proc, status = _biggo(d, BIGGO_STEP_OUTCOME='failure', BIGGO_JOB_STATUS='failure')
    assert proc.returncode == 0, proc.stderr
    assert status.read_bytes() == before, 'real runner status must not be overwritten'
    assert json.loads(sync.read_text())['ok'] is True
