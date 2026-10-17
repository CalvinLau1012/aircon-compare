# -*- coding: utf-8 -*-
import json
"""Read-only completed sync must not skip stages or acquire a lease."""
import importlib.util
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
spec = importlib.util.spec_from_file_location(
    'sync_biggo_completed', os.path.join(BASE, 'scripts', 'sync_biggo_completed.py'))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def _state(stage=3, status='completed'):
    return {'status': status, 'cycleId': '2026-10-10:3/7', 'stage': stage}


def test_sequential_stage_only():
    meta = {'price_batch_start': '2026-10-10', 'price_batch_idx': 2}
    assert sc.plan_sync(meta, _state(3)) == {'ok': True, 'reason': 'ready', 'stage': 3}
    gap = sc.plan_sync(meta, _state(4))
    assert gap['ok'] is False and gap['reason'] == 'missing-intermediate-stage'
    assert sc.plan_sync(meta, _state(2))['reason'] == 'stage-not-ahead'


def test_missing_history_and_cycle_mismatch_fail_closed():
    assert sc.plan_sync({}, _state(3))['reason'] == 'missing-local-history'
    meta = {'price_batch_start': '2026-10-09', 'price_batch_idx': 2}
    assert sc.plan_sync(meta, _state(3))['reason'] == 'cycle-mismatch'
    assert sc.plan_sync({'price_batch_start': '2026-10-10', 'price_batch_idx': 2},
                        _state(3, status='active'))['reason'] == 'remote-not-completed'


def test_all_exit_paths_write_atomic_report_and_do_not_keep_old_success(tmp_path, monkeypatch):
    for k in ('AIRCON_BIGGO_COORDINATOR_REPO', 'AIRCON_BIGGO_COORDINATOR_TOKEN'):
        monkeypatch.delenv(k, raising=False)
    p = tmp_path / 'sync.json'
    p.write_text('{"ok": true, "reason": "old-success"}', encoding='utf-8')
    rc = sc.main(['--report', str(p)])
    assert rc == 2
    d = json.load(open(p, encoding='utf-8'))
    assert d['ok'] is False and d['reason'] == 'coordinator-not-configured'
    assert d['receiptId'].startswith('sha256:')
    assert p.read_text(encoding='utf-8') != '{"ok": true, "reason": "old-success"}'


def test_remote_old_stage_after_newer_local_stage_is_rejected():
    meta = {'price_batch_start': '2026-10-10', 'price_batch_idx': 4,
            'appliedBundleHash': 'sha256:' + 'a' * 64,
            'last_applied_cycle': '2026-10-10:4/7'}
    out = sc.plan_sync(meta, {'status': 'completed', 'cycleId': '2026-10-10:3/7', 'stage': 3})
    assert out['ok'] is False and out['reason'] == 'stage-not-ahead'
