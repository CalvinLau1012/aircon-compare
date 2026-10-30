# -*- coding: utf-8 -*-
"""True fake-provider entry -> collector -> stage bundle -> local apply/receive.

No network: only fetch_biggo's provider function is replaced.  This exercises
the same run_price_batch, witness builder, bundle hashes, apply and receiver
used in production.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import biggo_acquisition  # noqa: E402
import biggo_apply  # noqa: E402
import biggo_canonical  # noqa: E402
import fetch_biggo  # noqa: E402
import model_lifecycle  # noqa: E402
import source_status  # noqa: E402
import sync_biggo_completed as sync  # noqa: E402
from scripts import biggo_stage_runner as runner  # noqa: E402


def _h(obj):
    return biggo_canonical.sha256_json(obj)


def test_true_entry_full_chain_and_receive_does_not_refresh(tmp_path, monkeypatch):
    root = tmp_path
    base = {'OLD': {'price': '$1,000起', 'merchants': 1,
                    'url': 'https://x', 'updated': '2026-10-01'}}
    tracking = {}
    blacklist = {'models': {}}
    (root / 'biggo_prices.json').write_text(json.dumps(base), encoding='utf-8')
    (root / 'model_status.json').write_text(json.dumps(tracking), encoding='utf-8')
    (root / 'model_blacklist.json').write_text(json.dumps(blacklist), encoding='utf-8')
    meta = {'price_batch_start': '2026-10-20', 'price_batch_idx': 0,
            'last_price_month': '2026-10'}
    (root / 'prices_meta.json').write_text(json.dumps(meta), encoding='utf-8')

    payload = {'list': [{'title': 'GREE K1234 window air conditioner',
                         'price': '2500', 'nindex': 'hk_shop'}]}
    monkeypatch.setattr(fetch_biggo, '_api_search',
                        lambda model, **kw: (payload, True))
    monkeypatch.setattr(fetch_biggo, 'load_meta', lambda: meta)
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: ['K1234'])
    monkeypatch.setattr(fetch_biggo, 'load_brand_lookup', lambda: {'K1234': 'GREE'})
    monkeypatch.setattr(fetch_biggo, 'filter_active',
                        lambda models, key_of=None: (list(models), []))
    monkeypatch.setattr(fetch_biggo, 'blacklist_review_slice',
                        lambda idx, blacklist=None: [])
    monkeypatch.setattr(fetch_biggo, 'protected_models', lambda: set())
    monkeypatch.setattr(fetch_biggo, 'OUT_PATH', str(root / 'biggo_prices.json'))
    monkeypatch.setattr(model_lifecycle, 'TRACKING_PATH', str(root / 'model_status.json'))
    monkeypatch.setattr(model_lifecycle, 'BLACKLIST_PATH', str(root / 'model_blacklist.json'))
    monkeypatch.setattr(batch_utils_path(), 'META_PATH', str(root / 'prices_meta.json'))

    # Keep collector, witness and apply on one isolated clock; wall-clock
    # second boundaries must not make this offline contract nondeterministic.
    now = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(time, 'time', lambda: now)
    fetch_biggo.reset_acquisitions()
    staged = fetch_biggo.run_price_batch()
    assert staged['status'] == 'completed', staged
    witness = staged['acquisitionWindow']
    assert witness['startedAt'] <= witness['finishedAt']
    priced = [o for o in staged['outcomes'] if o['outcome'] == 'priced']
    assert priced and priced[0]['model'] == 'K1234'
    assert priced[0]['acquiredAt'].endswith('Z')
    assert priced[0]['resultHash'] == biggo_acquisition.result_hash(priced[0]['price'])

    class FakeClient:
        state = {'requestAttempts': {'token': 0, 'search': 1},
                 'responseStatusCounts': {'200': 1}}
    sr = runner._build_stage_result(cycle_id='2026-10-20:1/7', stage_no=1,
                                    mode='price-batch', client=FakeClient(),
                                    now_fn=lambda: now, staged=staged)
    assert sr['acquisition']['lastValidResultAt'] == priced[0]['acquiredAt']
    new = staged['snapshot']
    manifest = {
        'schemaVersion': 2, 'cycleId': '2026-10-20:1/7', 'stage': 1,
        'mode': 'price-batch', 'publishedAt': '2026-10-20T01:00:00Z',
        'baseSnapshotHash': biggo_canonical.sha256_json(base),
        'newSnapshotHash': biggo_canonical.sha256_json(new),
        'snapshotHash': biggo_canonical.sha256_json(new),
        'stageResultHash': biggo_canonical.sha256_json(sr),
        'recordCount': len(new)}
    bundle = {'manifest': manifest, 'stageResult': sr,
              'baseSnapshot': base, 'newSnapshot': new,
              'baseSnapshotHash': manifest['baseSnapshotHash'],
              'newSnapshotHash': manifest['newSnapshotHash'],
              'stageResultHash': manifest['stageResultHash'],
              'bundleHash': biggo_canonical.sha256_json(manifest)}
    apply_now = now + 60
    applied = biggo_apply.apply_bundle(bundle, repo_root=str(root), now=apply_now)
    assert applied['snapshot'] == 'applied'
    meta_after = json.load(open(root / 'prices_meta.json'))
    acquired = priced[0]['acquiredAt']
    assert meta_after['appliedAt'] != acquired  # apply time is independent
    verified = sync.verify_local_post(bundle, meta_after, root)
    assert verified['acquiredAt'] == acquired
    assert verified['acquiredAtProvenance'] == 'recorded'
    # A re-receive is a no-op and never refreshes acquisition/apply facts.
    before = (root / 'prices_meta.json').read_bytes()
    again = sync.verify_local_post(bundle, meta_after, root)
    assert again['acquiredAt'] == acquired
    assert (root / 'prices_meta.json').read_bytes() == before
    status = source_status.build_status(
        metadata={'datasetDate': '2026-10-20',
                  'datasetHash': 'sha256:' + 'a' * 64},
        prices_meta=meta_after,
        biggo_status={'status': 'completed', 'cycle': '2026-10-20:1/7',
                      'stage': 1, 'at': sr['generatedAt']},
        biggo_snapshot_hash=bundle['newSnapshotHash'],
        completed_sync={'ok': True, 'postStateVerified': True,
                        'reason': 'already-applied',
                        'cycleId': meta_after['last_applied_cycle'],
                        'stage': meta_after['price_batch_idx'],
                        'bundleHash': bundle['bundleHash'],
                        'verifiedPost': verified,
                        'acquiredAt': verified['acquiredAt'],
                        'acquiredAtProvenance': verified['acquiredAtProvenance'],
                        'acquisitionHash': verified['acquisitionHash']},
        now=now)
    assert status['biggo']['lastAcquiredAt'] == acquired
    assert status['biggo']['lastAppliedAt'] == meta_after['appliedAt']
    assert status['biggo']['status'] == 'ok'


def batch_utils_path():
    import batch_utils
    return batch_utils
