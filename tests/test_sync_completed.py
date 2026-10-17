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


# ---------------------------------------------------------------- verified no-op
import copy
import hashlib


def _bundle(cycle='2026-10-10:3/7', stage=3, price=None, tracking=None,
            blacklist=None, published_at='2026-10-11T16:37:28Z'):
    from biggo_canonical import sha256_json
    price = price if price is not None else {
        'A': {'price': '$1,000起', 'merchants': 1, 'updated': '2026-10-10'}}
    tracking = tracking if tracking is not None else {'A|X': {'misses': 0}}
    blacklist = blacklist if blacklist is not None else {}
    sr = {
        'schemaVersion': 1, 'cycleId': cycle, 'stage': stage, 'mode': 'price-batch',
        'generatedAt': published_at, 'effects': {
            'trackingUpserts': {}, 'trackingRemovals': [],
            'blacklistUpserts': {}, 'blacklistRemovals': []},
        'preState': {'trackingHash': sha256_json(tracking),
                     'blacklistHash': sha256_json(blacklist)},
        'postState': {'trackingHash': sha256_json(tracking),
                      'blacklistHash': sha256_json(blacklist)},
    }
    sr['effectsHash'] = sha256_json(sr['effects'])
    manifest = {'cycleId': cycle, 'stage': stage, 'mode': 'price-batch',
                'publishedAt': published_at}
    manifest.update({'baseSnapshotHash': sha256_json(price),
                     'newSnapshotHash': sha256_json(price),
                     'snapshotHash': sha256_json(price),
                     'stageResultHash': sha256_json(sr)})
    return {'manifest': manifest, 'stageResult': sr, 'newSnapshot': price,
            'baseSnapshot': price, 'baseSnapshotHash': sha256_json(price),
            'newSnapshotHash': sha256_json(price),
            'stageResultHash': sha256_json(sr),
            'bundleHash': sha256_json(manifest)}


def _write_fixture(tmp_path, bundle, *, baseline=False, meta_patch=None):
    import json
    from biggo_canonical import sha256_json
    price = bundle['newSnapshot']
    tracking = {'A|X': {'misses': 0}}
    blacklist = {}
    for name, value in [('biggo_prices.json', price),
                        ('model_status.json', tracking),
                        ('model_blacklist.json', {'models': blacklist})]:
        (tmp_path / name).write_text(json.dumps(value), encoding='utf-8')
    cycle = bundle['manifest']['cycleId']; stage = bundle['manifest']['stage']
    meta = {'last_run': '2026-10-12', 'price_batch_start': '2026-10-10',
            'price_batch_idx': stage, 'appliedAt': '2026-10-17T08:52:47Z',
            'appliedBundleHash': bundle['bundleHash'],
            'appliedSnapshotHash': bundle['newSnapshotHash'],
            'last_applied_cycle': cycle,
            'appliedSnapshotId': ('biggo-' + cycle + '-stage' + str(stage)
                                  + '-' + bundle['newSnapshotHash'][-12:])}
    if baseline:
        active = {'biggo_prices.json': sha256_json(price),
                  'model_status.json': sha256_json(tracking),
                  'model_blacklist.json': sha256_json(blacklist)}
        acc = {'schemaVersion': 1, 'kind': 'accepted-shared-baseline-v1',
               'adoptedAt': '2026-10-17T18:30:00Z',
               'source': {'bundleHash': bundle['bundleHash'], 'cycleId': cycle,
                          'stage': stage, 'newSnapshotHash': bundle['newSnapshotHash'],
                          'acquiredAt': bundle['manifest']['publishedAt'],
                          'repairId': 'sha256:' + '3' * 64},
               'activePost': active}
        acc['acceptanceId'] = 'sha256:' + hashlib.sha256(
            json.dumps({k: v for k, v in acc.items() if k != 'acceptanceId'},
                       ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        meta['baselineAcceptance'] = acc
        meta['appliedSnapshotId'] = ('biggo-baseline-' + cycle + '-stage' + str(stage)
                                     + '-' + bundle['newSnapshotHash'][-12:])
    if meta_patch:
        meta.update(meta_patch)
    (tmp_path / 'prices_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    return meta


def _run_sync(tmp_path, monkeypatch, bundle, *, extra=()):
    state = {'status': 'completed', 'cycleId': bundle['manifest']['cycleId'],
             'stage': bundle['manifest']['stage']}
    class Contents:
        def __init__(self, *a, **k): pass
        def read_state(self): return state, 'sha'
    class Client:
        def __init__(self, *a, **k): self.state = None
        def import_bundle(self, cycle, stage): return bundle
    monkeypatch.setattr(sc.coord.CoordinatorConfig, 'from_env', staticmethod(lambda: object()))
    monkeypatch.setattr(sc.coord, 'GitHubContentsClient', Contents)
    monkeypatch.setattr(sc.coord, 'CoordinatorClient', Client)
    monkeypatch.setattr(sc, 'BASE', str(tmp_path))
    report = tmp_path / 'sync.json'
    rc = sc.main(['--apply', '--report', str(report), *extra])
    return rc, json.loads(report.read_text())


def test_verified_baseline_stage3_noop_reports_post_state_and_zero_write(tmp_path, monkeypatch):
    bundle = _bundle(); _write_fixture(tmp_path, bundle, baseline=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
              if p.is_file()}
    rc, report = _run_sync(tmp_path, monkeypatch, bundle)
    assert rc == 0 and report['ok'] is True and report['applied'] is False
    assert report['reason'] == 'already-applied' and report['postStateVerified'] is True
    assert report['verifiedPost']['baselineAcceptanceId'].startswith('sha256:')
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
             if p.is_file() and p.name != 'sync.json'}
    assert after == {k: v for k, v in before.items() if k != 'sync.json'}


def test_noop_tampered_actual_files_or_meta_is_rc3_even_degraded(tmp_path, monkeypatch):
    for name in ('biggo_prices.json', 'model_status.json', 'model_blacklist.json',
                 'prices_meta.json'):
        d = tmp_path / name; d.mkdir()
        bundle = _bundle(); _write_fixture(d, bundle, baseline=True)
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()
                  if p.is_file()}
        if name == 'biggo_prices.json':
            (d / name).write_text('{"A": {"price": "$9"}}', encoding='utf-8')
        elif name == 'model_status.json':
            (d / name).write_text('{}', encoding='utf-8')
        elif name == 'model_blacklist.json':
            (d / name).write_text('{"models": {"X|Y": {"status": "x"}}}', encoding='utf-8')
        else:
            (d / name).write_text((d / name).read_text(encoding='utf-8').replace(
                '"appliedBundleHash": "' + bundle['bundleHash'] + '"',
                '"appliedBundleHash": "sha256:' + '0' * 64 + '"'), encoding='utf-8')
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()
                  if p.is_file()}
        rc, report = _run_sync(d, monkeypatch, bundle, extra=('--degraded-exit-zero',))
        assert rc == 3, (name, rc, report)
        assert report['ok'] is False and report['postStateVerified'] is False
        assert report['reason'] == 'local-integrity-error'
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()
                 if p.is_file() and p.name != 'sync.json'}
        assert after == {k: v for k, v in before.items() if k != 'sync.json'}


def test_noop_bad_baseline_acceptance_and_missing_bundle_fail_closed(tmp_path, monkeypatch):
    bundle = _bundle(); _write_fixture(tmp_path, bundle, baseline=True)
    meta_path = tmp_path / 'prices_meta.json'
    meta = json.loads(meta_path.read_text())
    meta['baselineAcceptance']['acceptanceId'] = 'sha256:' + '9' * 64
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    rc, report = _run_sync(tmp_path, monkeypatch, bundle)
    assert rc == 3 and report['reason'] == 'local-integrity-error'
    d2 = tmp_path / 'missing'; d2.mkdir(); _write_fixture(d2, bundle, baseline=True)
    class BrokenClient:
        def __init__(self, *a, **k): self.state = None
        def import_bundle(self, *a): raise sc.coord.CoordinatorError('missing')
    monkeypatch.setattr(sc.coord, 'CoordinatorClient', BrokenClient)
    monkeypatch.setattr(sc, 'BASE', str(d2))
    rc2 = sc.main(['--apply', '--report', str(d2 / 'sync.json'),
                   '--degraded-exit-zero'])
    assert rc2 == 2


def test_stage4_noop_keeps_historical_baseline_but_reports_normal_stage(tmp_path, monkeypatch):
    bundle = _bundle(cycle='2026-10-10:4/7', stage=4)
    _write_fixture(tmp_path, bundle)
    meta_path = tmp_path / 'prices_meta.json'; meta = json.loads(meta_path.read_text())
    # Historical stage3 acceptance is retained verbatim, but current stage is 4.
    old = _bundle(); agg = {'schemaVersion': 1, 'kind': 'accepted-shared-baseline-v1',
                            'acceptanceId': 'sha256:' + '8' * 64,
                            'source': {'bundleHash': old['bundleHash'],
                                       'cycleId': old['manifest']['cycleId'],
                                       'stage': 3,
                                       'newSnapshotHash': old['newSnapshotHash'],
                                       'acquiredAt': old['manifest']['publishedAt']},
                            'activePost': {}}
    meta['baselineAcceptance'] = agg
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    rc, report = _run_sync(tmp_path, monkeypatch, bundle)
    assert rc == 0 and report['ok'] is True and report['applied'] is False
    assert report['verifiedPost']['stage'] == 4
    assert report['verifiedPost']['baselineAcceptanceId'] is None


def test_wrong_cycle_or_recomputed_bad_acceptance_is_not_a_green_noop(tmp_path, monkeypatch):
    bundle = _bundle(); d = tmp_path / 'wrong-cycle'; d.mkdir()
    _write_fixture(d, bundle, baseline=True)
    meta_path = d / 'prices_meta.json'; meta = json.loads(meta_path.read_text())
    meta['last_applied_cycle'] = '2026-10-10:2/7'
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    rc, report = _run_sync(d, monkeypatch, bundle, extra=('--degraded-exit-zero',))
    assert rc == 3 and report['ok'] is False
    assert report['reason'] == 'local-integrity-error'

    d2 = tmp_path / 'recomputed'; d2.mkdir(); _write_fixture(d2, bundle, baseline=True)
    p2 = d2 / 'prices_meta.json'; m2 = json.loads(p2.read_text())
    acc = m2['baselineAcceptance']
    acc['source']['newSnapshotHash'] = 'sha256:' + '7' * 64
    acc['activePost']['biggo_prices.json'] = acc['source']['newSnapshotHash']
    acc['acceptanceId'] = 'sha256:' + hashlib.sha256(
        json.dumps({k: v for k, v in acc.items() if k != 'acceptanceId'},
                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    p2.write_text(json.dumps(m2), encoding='utf-8')
    rc2, report2 = _run_sync(d2, monkeypatch, bundle)
    assert rc2 == 3 and report2['reason'] == 'local-integrity-error'


def test_idle_later_stage_verifies_adopted_baseline_read_only(tmp_path, monkeypatch):
    bundle = _bundle(); _write_fixture(tmp_path, bundle, baseline=True)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
              if p.is_file()}
    class Contents:
        def __init__(self, *a, **k): pass
        def read_state(self):
            return {'status': 'idle', 'cycleId': '2026-10-10:4/7', 'stage': 4}, 'sha'
    class Client:
        def __init__(self, *a, **k): self.state = None
        def import_bundle(self, cycle, stage):
            assert (cycle, stage) == ('2026-10-10:3/7', 3)
            return bundle
    monkeypatch.setattr(sc.coord.CoordinatorConfig, 'from_env', staticmethod(lambda: object()))
    monkeypatch.setattr(sc.coord, 'GitHubContentsClient', Contents)
    monkeypatch.setattr(sc.coord, 'CoordinatorClient', Client)
    monkeypatch.setattr(sc, 'BASE', str(tmp_path))
    report = tmp_path / 'sync.json'
    rc = sc.main(['--apply', '--report', str(report)])
    d = json.loads(report.read_text())
    assert rc == 0 and d['ok'] is True and d['applied'] is False
    assert d['reason'] == 'already-applied' and d['postStateVerified'] is True
    assert d['verifiedPost']['baselineAcceptanceId'].startswith('sha256:')
    assert d['baselineFallback'].startswith('idle-remote')
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()
             if p.is_file() and p.name != 'sync.json'}
    assert after == {k: v for k, v in before.items() if k != 'sync.json'}
