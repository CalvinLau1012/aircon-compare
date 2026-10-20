# -*- coding: utf-8 -*-
"""BigGo local apply（P0）離線回歸：pre/post image 驗證、meta 最後寫、冪等、可恢復。

無網絡；全部檔案在 tmp_path。
"""
import copy
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_apply  # noqa: E402
import biggo_canonical  # noqa: E402
import biggo_coordinator as coord  # noqa: E402


def _entry(price='$2,500起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}


def _h(obj):
    return biggo_canonical.sha256_id(biggo_canonical.canonical_json_bytes(obj))


def _write(path, obj):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _read(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _bundle(tmp_path, *, pre_tracking=None, post_tracking=None,
            pre_blacklist=None, post_blacklist=None, base=None, new=None):
    pre_tracking = {'HITACHI|OLD': {'misses': 1, 'batch_id': 'old'}} \
        if pre_tracking is None else pre_tracking
    post_tracking = {} if post_tracking is None else post_tracking
    pre_blacklist = {'RASONIC|ABC': {'status': 'auto_discontinued'}} \
        if pre_blacklist is None else pre_blacklist
    post_blacklist = {} if post_blacklist is None else post_blacklist
    base = {'OLD-1': _entry('$1,000起')} if base is None else base
    new = dict(base) if new is None else dict(new)
    new.setdefault('RA-10RF', _entry())
    effects = {
        'trackingUpserts': {}, 'trackingRemovals': ['HITACHI|OLD'],
        'blacklistUpserts': {}, 'blacklistRemovals': ['RASONIC|ABC'],
    }
    new_effects = {}
    for key, value in effects.items():
        if key.endswith('Upserts'):
            delta = {m: v for m, v in post_tracking.items()
                     if pre_tracking.get(m) != v} if key.startswith('tracking') else \
                    {m: v for m, v in post_blacklist.items()
                     if pre_blacklist.get(m) != v}
            new_effects[key] = delta
        else:
            src_pre = pre_tracking if key.startswith('tracking') else pre_blacklist
            src_post = post_tracking if key.startswith('tracking') else post_blacklist
            new_effects[key] = [m for m in src_pre if m not in src_post]
    effects = new_effects
    sr = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': '2026-10-01:1/7', 'stage': 1, 'mode': 'price-batch',
        'generatedAt': '2026-10-01T00:00:00Z',
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'requestAttempts': {'token': 1, 'search': 2},
        'responseStatusCounts': {'200': 2},
        'outcomes': [{'model': 'RA-10RF', 'canonicalKey': 'HITACHI|RA10RF',
                      'outcome': 'priced', 'price': new['RA-10RF']}],
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'effects': effects,
        'preState': {'trackingHash': _h(pre_tracking), 'blacklistHash': _h(pre_blacklist)},
        'postState': {'trackingHash': _h(post_tracking), 'blacklistHash': _h(post_blacklist)},
        'effectsHash': _h(effects),
    }
    coord.validate_stage_result(sr, base, new, cycle_id='2026-10-01:1/7', stage=1,
                                mode='price-batch')
    manifest = {
        'schemaVersion': coord.BUNDLE_MANIFEST_SCHEMA_VERSION,
        'cycleId': '2026-10-01:1/7', 'stage': 1, 'mode': 'price-batch',
        'writer': 'writer-a', 'writerKind': 'github-actions', 'commit': 'a' * 40,
        'publishedAt': '2026-10-01T00:00:00Z', 'recordCount': len(new),
        'baseSnapshotHash': _h(base), 'newSnapshotHash': _h(new),
        'stageResultHash': _h(sr),
        'files': {'base': 'b.json', 'snapshot': 's.json', 'stageResult': 'r.json'},
        'authority': 'coordinator-stage-result-v1',
    }
    return {
        'manifest': manifest, 'stageResult': sr,
        'baseSnapshot': base, 'newSnapshot': new,
        'baseSnapshotHash': _h(base), 'newSnapshotHash': _h(new),
        'stageResultHash': _h(sr),
        'bundleHash': _h(manifest),
        'snapshotPath': 'coordinator/snapshots/x/biggo_prices.json',
    }


def _seed(tmp_path, *, snapshot=None, tracking=None, blacklist=None, meta=None):
    snapshot = snapshot if snapshot is not None else {'OLD-1': _entry('$1,000起')}
    tracking = tracking if tracking is not None else {'HITACHI|OLD': {'misses': 1, 'batch_id': 'old'}}
    blacklist = blacklist if blacklist is not None else {'RASONIC|ABC': {'status': 'auto_discontinued'}}
    meta = meta if meta is not None else {'price_batch_start': '2026-10-01', 'price_batch_idx': 0}
    _write(os.path.join(tmp_path, 'biggo_prices.json'), snapshot)
    _write(os.path.join(tmp_path, 'model_status.json'), tracking)
    _write(os.path.join(tmp_path, 'model_blacklist.json'),
           {'version': 1, 'updated': '2026-09-30', 'models': blacklist})
    _write(os.path.join(tmp_path, 'prices_meta.json'), meta)


def test_apply_happy_path_writes_and_advances_once(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert report['snapshot'] == 'applied'
    assert report['tracking'] == 'applied'
    assert report['blacklist'] == 'applied'
    assert report['meta'] == 'written'
    assert _read(os.path.join(tmp_path, 'biggo_prices.json')) == bundle['newSnapshot']
    assert _read(os.path.join(tmp_path, 'model_status.json')) == {}
    assert _read(os.path.join(tmp_path, 'model_blacklist.json'))['models'] == {}
    meta = _read(os.path.join(tmp_path, 'prices_meta.json'))
    assert meta['price_batch_idx'] == 1
    assert meta['appliedBundleHash'] == bundle['bundleHash']
    assert meta['appliedSnapshotHash'] == bundle['newSnapshotHash']
    # 第二次 apply 同 bundle → no-op，不可重複推進
    again = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_100)
    assert again['noop'] is True
    assert _read(os.path.join(tmp_path, 'prices_meta.json'))['price_batch_idx'] == 1


def test_apply_pre_state_mismatch_fails_closed_no_writes(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path, tracking={'WRONG|KEY': {'misses': 9}})
    before = {name: open(os.path.join(tmp_path, name), 'rb').read()
              for name in ('biggo_prices.json', 'model_status.json',
                           'model_blacklist.json', 'prices_meta.json')}
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    for name, data in before.items():
        assert open(os.path.join(tmp_path, name), 'rb').read() == data, name


def test_apply_partial_recovery_only_pre_or_post_images(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    # 模擬上次 apply 只寫到 snapshot；tracking/blacklist/meta 仍係 pre
    _write(os.path.join(tmp_path, 'biggo_prices.json'), bundle['newSnapshot'])
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert report['snapshot'] == 'post'
    assert report['tracking'] == 'applied'
    assert report['meta'] == 'written'


def test_apply_unknown_snapshot_hash_fails(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path, snapshot={'SOMETHING|ELSE': _entry('$5起')})
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)


def test_apply_meta_last_and_recoverable(tmp_path, monkeypatch):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    calls = {'n': 0}
    real_save = biggo_apply.batch_utils.save_meta

    def flaky_save(meta, path=None):
        calls['n'] += 1
        if calls['n'] == 1:
            raise biggo_apply.batch_utils.MetaError('simulated crash before meta')
        return real_save(meta, path)

    monkeypatch.setattr(biggo_apply.batch_utils, 'save_meta', flaky_save)
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    # meta 未寫入；已寫 files 係 post image
    meta = _read(os.path.join(tmp_path, 'prices_meta.json'))
    assert 'appliedBundleHash' not in meta
    assert _read(os.path.join(tmp_path, 'biggo_prices.json')) == bundle['newSnapshot']
    # 重跑（第二次 save 成功）→ 完成；idx 只推一次
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_100)
    assert report['meta'] == 'written'
    assert _read(os.path.join(tmp_path, 'prices_meta.json'))['price_batch_idx'] == 1


def test_apply_cycle_mismatch_fails(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path, meta={'price_batch_start': '2026-09-01', 'price_batch_idx': 0})
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)


# ---------------------------------------------------------------- 2026-10-17 safety

def test_planner_does_not_mutate_nested_caller_tracking():
    import model_lifecycle
    pre = {'FORTRESS|FWAC08M18': {'first_missed': '2026-08-26', 'misses': 1,
                                  'last_checked': '2026-08-26'}}
    snapshot = {'FORTRESS|FWAC08M18': copy.deepcopy(pre['FORTRESS|FWAC08M18'])}
    new_tracking, new_black = model_lifecycle.plan_record_results(
        [('FORTRESS|FWAC08M18', False)], protected=set(), batch_id='2026-10-10:3/7',
        tracking=pre, blacklist={}, today='2026-10-12')
    assert pre == snapshot, 'planner 唔可以改 caller 嘅 nested tracking'
    assert new_tracking['FORTRESS|FWAC08M18']['misses'] == 2
    assert new_tracking['FORTRESS|FWAC08M18']['batch_id'] == '2026-10-10:3/7'
    assert new_black['FORTRESS|FWAC08M18']['status'] == 'auto_discontinued'


def _repair_bundle(tmp_path):
    """Synthetic reproduction of the 2026-10-10 shallow-copy bug."""
    cycle = '2026-10-10:3/7'
    key = 'FORTRESS|FWAC08M18'
    pre_tracking = {key: {'first_missed': '2026-08-26', 'misses': 1,
                          'last_checked': '2026-08-26'}}
    post_tracking = {key: {'first_missed': '2026-08-26', 'misses': 2,
                           'last_checked': '2026-10-12', 'batch_id': cycle}}
    pre_blacklist = {}
    post_blacklist = {key: {'status': 'auto_discontinued'}}
    base = {'OLD-1': _entry('$1,000起')}
    new = dict(base)
    new['RA-10RF'] = _entry()
    # The buggy planner omitted the existing-record update from effects.
    effects = {
        'trackingUpserts': {}, 'trackingRemovals': [],
        'blacklistUpserts': {key: post_blacklist[key]}, 'blacklistRemovals': [],
    }
    sr = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': cycle, 'stage': 3, 'mode': 'price-batch',
        'generatedAt': '2026-10-12T00:37:28Z',
        'counters': {'got': 1, 'cleanMiss': 1, 'netErrors': 0},
        'requestAttempts': {'token': 1, 'search': 2},
        'responseStatusCounts': {'200': 2},
        'outcomes': [
            {'model': 'RA-10RF', 'canonicalKey': 'HITACHI|RA10RF',
             'outcome': 'priced', 'price': new['RA-10RF']},
            {'model': 'FWAC08M18', 'canonicalKey': key, 'outcome': 'clean_miss'},
        ],
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'effects': effects,
        'preState': {'trackingHash': _h(pre_tracking), 'blacklistHash': _h(pre_blacklist)},
        'postState': {'trackingHash': _h(post_tracking), 'blacklistHash': _h(post_blacklist)},
        'effectsHash': _h(effects),
    }
    manifest = {
        'schemaVersion': coord.BUNDLE_MANIFEST_SCHEMA_VERSION,
        'cycleId': cycle, 'stage': 3, 'mode': 'price-batch',
        'writer': 'writer-a', 'writerKind': 'github-actions', 'commit': 'a' * 40,
        'publishedAt': '2026-10-12T00:37:28Z', 'recordCount': len(new),
        'baseSnapshotHash': _h(base), 'newSnapshotHash': _h(new),
        'stageResultHash': _h(sr),
        'files': {'base': 'b.json', 'snapshot': 's.json', 'stageResult': 'r.json'},
        'authority': 'coordinator-stage-result-v1',
    }
    return {
        'manifest': manifest, 'stageResult': sr,
        'baseSnapshot': base, 'newSnapshot': new,
        'baseSnapshotHash': _h(base), 'newSnapshotHash': _h(new),
        'stageResultHash': _h(sr), 'bundleHash': _h(manifest),
        'snapshotPath': 'coordinator/snapshots/x/biggo_prices.json',
    }, pre_tracking, post_tracking, pre_blacklist, post_blacklist


def test_real_shallow_bug_repair_derives_exact_post_and_is_idempotent(tmp_path):
    bundle, pre_t, post_t, pre_b, post_b = _repair_bundle(tmp_path)
    _seed(tmp_path, snapshot=bundle['baseSnapshot'], tracking=pre_t,
          blacklist=pre_b, meta={'price_batch_start': '2026-10-10',
                                 'price_batch_idx': 2})
    # Without repair the original immutable bundle must fail closed.
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    repair = biggo_apply.derive_tracking_repair(
        bundle, _read(os.path.join(tmp_path, 'model_status.json')))
    assert repair is not None
    assert repair['postState']['trackingHash'] == _h(post_t)
    assert set(repair['trackingUpserts']) == {'FORTRESS|FWAC08M18'}
    # Dry derivation is pure.
    assert _h(_read(os.path.join(tmp_path, 'model_status.json'))) != _h(post_t)
    report = biggo_apply.apply_repair(bundle, repair, repo_root=str(tmp_path))
    assert report['tracking'] == 'applied'
    assert _h(_read(os.path.join(tmp_path, 'model_status.json'))) == _h(post_t)
    # Replay of the same repair is a no-op.
    again = biggo_apply.apply_repair(bundle, repair, repo_root=str(tmp_path))
    assert again['noop'] is True
    # Normal apply now completes the original transaction, with the original
    # bundle identity, and is idempotent.
    out = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), repair=repair,
                                   now=1_000_000)
    assert out['noop'] is False
    assert _read(os.path.join(tmp_path, 'biggo_prices.json')) == bundle['newSnapshot']
    assert _h(_read(os.path.join(tmp_path, 'model_status.json'))) == _h(post_t)
    assert _h(_read(os.path.join(tmp_path, 'model_blacklist.json'))['models']) == _h(post_b)
    meta = _read(os.path.join(tmp_path, 'prices_meta.json'))
    assert meta['appliedBundleHash'] == bundle['bundleHash']
    assert meta['price_batch_idx'] == 3
    assert biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), repair=repair,
                                    now=1_000_100)['noop'] is True


def test_apply_journal_recovers_partial_write_and_does_not_duplicate_meta(tmp_path, monkeypatch):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    real_write = biggo_apply._write_json
    calls = {'n': 0}

    def flaky_write(path, obj):
        calls['n'] += 1
        if calls['n'] == 3:  # snapshot + tracking succeed, blacklist fails
            raise biggo_apply.ApplyError('simulated process crash')
        return real_write(path, obj)

    monkeypatch.setattr(biggo_apply, '_write_json', flaky_write)
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert os.path.exists(os.path.join(tmp_path, biggo_apply.JOURNAL_NAME))
    assert _read(os.path.join(tmp_path, 'biggo_prices.json')) == bundle['newSnapshot']
    assert _h(_read(os.path.join(tmp_path, 'model_status.json'))) == bundle['stageResult']['preState']['trackingHash']
    monkeypatch.setattr(biggo_apply, '_write_json', real_write)
    out = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert out['noop'] is False
    assert not os.path.exists(os.path.join(tmp_path, biggo_apply.JOURNAL_NAME))
    assert _read(os.path.join(tmp_path, 'prices_meta.json'))['price_batch_idx'] == 1
    assert biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_100)['noop'] is True


def test_apply_lock_contention_fails_closed(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    with biggo_apply._OperationLock(str(tmp_path)):
        with pytest.raises(biggo_apply.ApplyError, match='lock busy'):
            biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert _read(os.path.join(tmp_path, 'prices_meta.json')).get('appliedBundleHash') is None


def test_meta_target_is_validated_before_any_write(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path, meta={'price_batch_start': '2026-09-01', 'price_batch_idx': 0})
    before = {n: open(os.path.join(tmp_path, n), 'rb').read()
              for n in ('biggo_prices.json', 'model_status.json',
                        'model_blacklist.json', 'prices_meta.json')}
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert not os.path.exists(os.path.join(tmp_path, biggo_apply.JOURNAL_NAME))
    for n, data in before.items():
        assert open(os.path.join(tmp_path, n), 'rb').read() == data, n


def test_repair_cli_receipt_binds_original_bundle_and_is_tamper_evident(tmp_path, monkeypatch):
    bundle, pre_t, post_t, pre_b, post_b = _repair_bundle(tmp_path)
    _seed(tmp_path, snapshot=bundle['baseSnapshot'], tracking=pre_t,
          blacklist=pre_b, meta={'price_batch_start': '2026-10-10',
                                 'price_batch_idx': 2})
    bdir = tmp_path / 'bundle'
    bdir.mkdir()
    _write(bdir / 'manifest.json', bundle['manifest'])
    _write(bdir / 'base-biggo_prices.json', bundle['baseSnapshot'])
    _write(bdir / 'biggo_prices.json', bundle['newSnapshot'])
    _write(bdir / 'stage-result.json', bundle['stageResult'])
    # Import the CLI as a module so the test does not depend on cwd.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'repair_cli', os.path.join(BASE, 'scripts', 'repair_biggo_apply.py'))
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    receipt_path = tmp_path / 'repair.json'
    clock = [1792490000]
    monkeypatch.setattr(cli.time, 'time', lambda: clock[0])
    rc = cli.main(['--bundle-dir', str(bdir), '--repo', str(tmp_path),
                   '--out', str(receipt_path)])
    assert rc == 0
    receipt = _read(receipt_path)
    assert receipt['originalBundleHash'] == bundle['bundleHash']
    assert receipt['postState']['trackingHash'] == _h(post_t)
    # A second run must be byte-identical (immutable receipt), and applying
    # with the wrong expected id must fail closed.
    before = receipt_path.read_bytes()
    clock[0] += 3600  # Real repeat invocations cross wall-clock seconds.
    assert cli.main(['--bundle-dir', str(bdir), '--repo', str(tmp_path),
                     '--out', str(receipt_path)]) == 0
    assert receipt_path.read_bytes() == before
    assert cli.main(['--bundle-dir', str(bdir), '--repo', str(tmp_path),
                     '--out', str(receipt_path), '--apply',
                     '--expect-repair-id', 'sha256:' + '0' * 64]) == 1
    # Tampering the receipt breaks the id check in normal apply.
    tampered = dict(receipt)
    tampered['trackingUpserts'] = {}
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.merge_repair_effects(bundle, tampered)


def test_stage_runner_import_passes_discovered_repair_attachment(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'runner_repair_test', os.path.join(BASE, 'scripts', 'biggo_stage_runner.py'))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    bundle = {
        'newSnapshotHash': 'sha256:' + 'a' * 64,
        'snapshotPath': 'coordinator/snapshots/x/biggo_prices.json',
        'bundleHash': 'sha256:' + 'b' * 64,
        'stageResultHash': 'sha256:' + 'c' * 64,
    }
    repair = {'repairId': 'sha256:' + 'd' * 64, 'kind': 'test'}
    received = {}

    class Client:
        def import_bundle(self, cycle, stage):
            return bundle

    def apply_fn(b, *, repo_root, now, repair):
        received['repair'] = repair
        return {'noop': False}

    def emit(*args, **kwargs):
        pass

    # Explicit attachment is passed through without needing discovery.
    assert runner._import_and_apply(Client(), emit, apply_fn, str(tmp_path),
                                    lambda: 0, '2026-10-10:3/7', 3,
                                    final_status='completed', repair=repair) == 0
    assert received['repair'] == repair


def test_stage_runner_discovers_hash_named_repair_attachment(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'runner_repair_discovery', os.path.join(BASE, 'scripts', 'biggo_stage_runner.py'))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    bundle_hash = 'sha256:' + 'e' * 64
    d = tmp_path / 'repair-receipts'; d.mkdir()
    receipt = {'repairId': 'sha256:' + 'f' * 64, 'kind': 'test'}
    (d / (bundle_hash.split(':', 1)[1] + '.json')).write_text(json.dumps(receipt), encoding='utf-8')
    assert runner._load_repair_receipt({}, str(tmp_path), bundle_hash) == receipt


def test_apply_records_full_utc_applied_at_and_snapshot_identity(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    meta = _read(os.path.join(tmp_path, 'prices_meta.json'))
    assert report['noop'] is False
    assert meta['appliedAt'] == '1970-01-12T13:46:40Z'
    assert meta['appliedSnapshotId'] == (
        'biggo-2026-10-01:1/7-stage1-' + bundle['newSnapshotHash'][-12:])
    assert meta['appliedSnapshotHash'] == bundle['newSnapshotHash']
    # no-op must preserve the original successful timestamp and identity
    biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_100)
    assert _read(os.path.join(tmp_path, 'prices_meta.json')) == meta


def test_apply_rejects_date_only_new_timestamp(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    with pytest.raises(biggo_apply.ApplyError, match='date-only'):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path),
                                 applied_at='2026-10-17')
    assert 'appliedAt' not in _read(os.path.join(tmp_path, 'prices_meta.json'))


def test_journal_recovery_preserves_original_applied_at(tmp_path, monkeypatch):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path)
    real_save = biggo_apply.batch_utils.save_meta
    calls = {'n': 0}

    def flaky(meta, path=None):
        calls['n'] += 1
        if calls['n'] == 1:
            raise biggo_apply.batch_utils.MetaError('simulated crash')
        return real_save(meta, path)

    monkeypatch.setattr(biggo_apply.batch_utils, 'save_meta', flaky)
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    journal = _read(os.path.join(tmp_path, biggo_apply.JOURNAL_NAME))
    assert journal['appliedAt'] == '1970-01-12T13:46:40Z'
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_100)
    assert report['noop'] is False
    meta = _read(os.path.join(tmp_path, 'prices_meta.json'))
    assert meta['appliedAt'] == journal['appliedAt']
    assert meta['appliedSnapshotId'] == journal['appliedSnapshotId']


def test_legacy_date_only_applied_at_is_readable_but_not_rewritten(tmp_path):
    bundle = _bundle(str(tmp_path))
    _seed(tmp_path, meta={'price_batch_start': '2026-10-01', 'price_batch_idx': 0,
                          'appliedBundleHash': bundle['bundleHash'],
                          'appliedSnapshotHash': bundle['newSnapshotHash'],
                          'appliedAt': '2026-10-17'})
    for name in ('biggo_prices.json', 'model_status.json', 'model_blacklist.json'):
        _write(os.path.join(tmp_path, name),
               bundle['newSnapshot'] if name == 'biggo_prices.json' else
               (bundle['stageResult']['postState']['trackingHash'] if False else {}))
    # Use a real post image for tracking/blacklist.
    _write(os.path.join(tmp_path, 'model_status.json'), {})
    _write(os.path.join(tmp_path, 'model_blacklist.json'), {'version': 1, 'updated': 'x', 'models': {}})
    report = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path), now=1_000_000)
    assert report['noop'] is True
    assert _read(os.path.join(tmp_path, 'prices_meta.json'))['appliedAt'] == '2026-10-17'
