# -*- coding: utf-8 -*-
"""BigGo local apply（P0）離線回歸：pre/post image 驗證、meta 最後寫、冪等、可恢復。

無網絡；全部檔案在 tmp_path。
"""
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
