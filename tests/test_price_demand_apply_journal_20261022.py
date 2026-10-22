# -*- coding: utf-8 -*-
"""R7: demand participates in the apply journal before metadata commits."""
import json
import sys

import pytest

BASE = __import__('os').path.dirname(__import__('os').path.dirname(
    __import__('os').path.abspath(__file__)))
sys.path[:0] = [BASE, __import__('os').path.join(BASE, 'scripts'),
                __import__('os').path.join(BASE, 'tests')]

import biggo_apply  # noqa: E402
import price_demand  # noqa: E402
import test_biggo_acquisition_chain as AC  # noqa: E402


def test_demand_is_in_apply_journal_and_retry_does_not_double_count(tmp_path):
    sr, base, new = AC.stage_result()
    sr['outcomes'][0]['canonicalKey'] = 'HITACHI|RA10RF'
    bundle = AC.bundle_for(sr, base, new)
    meta = AC.write_local(tmp_path, bundle)
    for key in ('appliedBundleHash', 'appliedSnapshotHash', 'appliedAt'):
        meta.pop(key, None)
    (tmp_path / 'prices_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    demand = price_demand.merge_new_models(
        price_demand.empty(1_793_000_000),
        {'models': [{'brand': 'HITACHI', 'model': 'RA-10RF',
                     'first_seen': '2026-10-01',
                     'first_seen_at': '2026-10-01T00:00:00Z'}]},
        now=1_793_000_000)
    (tmp_path / 'price_demand.json').write_bytes(price_demand._canonical_doc(demand))
    before = (tmp_path / 'price_demand.json').read_bytes()
    with pytest.raises(biggo_apply.ApplyError):
        biggo_apply.apply_bundle(
            bundle, repo_root=str(tmp_path), now=1_793_000_000,
            meta_saver=lambda *a, **k: (_ for _ in ()).throw(OSError('inject')))
    journal = biggo_apply._read_journal(str(tmp_path))
    assert journal is not None
    assert journal['demand']['state'] == 'post'
    assert 'demand' in journal
    # Partial post-state includes demand, but meta is still the commit point.
    assert json.loads((tmp_path / 'prices_meta.json').read_text()).get(
        'appliedBundleHash') != bundle['bundleHash']
    applied = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path),
                                       now=1_793_000_060)
    assert applied['noop'] is False
    assert biggo_apply._read_journal(str(tmp_path)) is None
    doc = price_demand.load(tmp_path / 'price_demand.json')
    assert doc['demands'][0]['status'] == 'processed'
    assert len(doc['demands'][0]['evidence']) == 1
    # Re-applying the same bundle is a no-op and never increments evidence.
    again = biggo_apply.apply_bundle(bundle, repo_root=str(tmp_path),
                                     now=1_793_000_120)
    assert again['noop'] is True
    doc2 = price_demand.load(tmp_path / 'price_demand.json')
    assert len(doc2['demands'][0]['evidence']) == 1
    assert doc2['demands'][0]['processedAt'] == doc['demands'][0]['processedAt']
