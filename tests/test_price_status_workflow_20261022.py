# -*- coding: utf-8 -*-
"""Price freshness split, v3 health, demand policy and cooldown contracts."""
import copy
import datetime as dt
import hashlib
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts'), os.path.join(BASE, 'tests')]

import batch_utils  # noqa: E402
import check_health_summary as chs  # noqa: E402
import price_demand  # noqa: E402
import price_status  # noqa: E402
import source_health_monitor as shm  # noqa: E402
from biggo_canonical import sha256_json  # noqa: E402
import test_source_health_monitor as tsh  # noqa: E402

NOW = dt.datetime(2026, 10, 23, 4, 30, 6, tzinfo=dt.timezone.utc)


def _demand():
    doc = price_demand.empty(NOW)
    doc = price_demand.merge_new_models(
        doc, {'models': [{'brand': '格力', 'model': 'GEH63ZE/GEH63ZC',
                          'first_seen': '2026-10-01'}]}, now=NOW)
    doc = price_demand.record_outcomes(
        doc,
        {'cycleId': '2026-10-10:7/7', 'stage': 7,
         'generatedAt': '2026-10-20T20:15:34Z',
         'outcomes': [{'model': 'GEH63ZE/GEH63ZC',
                       'canonicalKey': 'GREE|GEH63ZEGEH63ZC',
                       'outcome': 'clean_miss', 'resultHash': None}]},
        {'cycleId': '2026-10-10:7/7', 'stage': 7}, now=NOW)
    # Leave one unresolved demand so waiting-eligible branches can be exercised.
    doc['demands'].append({'key': 'GREE|NEWMODEL', 'brand': '格力',
                           'model': 'NEWMODEL', 'firstSeen': '2026-10-20',
                           'status': 'pending', 'evidence': []})
    doc['updatedAt'] = '2026-10-22T00:00:00Z'
    return price_demand.validate(doc)


def _meta(**over):
    meta = {'last_price_month': '2026-10', 'price_batch_idx': 7,
            'last_applied_cycle': '2026-10-10:7/7',
            'appliedSnapshotHash': 'sha256:' + 'a' * 64,
            'appliedBundleHash': 'sha256:' + 'b' * 64,
            'appliedAt': '2026-10-21T16:30:05Z'}
    meta.update(over)
    return meta


def _biggo(**over):
    doc = {'schemaVersion': 1, 'status': 'skip-not-active', 'alert': False,
           'at': '2026-10-21T16:31:25Z', 'snapshotHash': 'sha256:' + 'a' * 64}
    doc.update(over)
    return doc


def _receipt(**over):
    inputs = {'meta': _meta(), 'demand': _demand(), 'biggo': _biggo(),
              'completed_sync': {}, 'snapshot_hash': 'sha256:' + 'a' * 64,
              'anchor': None, 'shared': False}
    inputs.update(over)
    return price_status.build_receipt(now=over.pop('now', NOW), **inputs)


def test_month_completed_ignores_only_biggo_age_and_other_sources_stay_strict(tmp_path):
    tsh._repo(tmp_path)
    status = tsh._status(tmp_path)
    live_hash = sha256_json(json.load(open(tmp_path / 'biggo_prices.json')))
    status['biggo']['snapshotHash'] = live_hash
    status['biggo']['lastAppliedAt'] = '2026-10-20T20:15:41Z'
    status['emsd']['lastAcquiredAt'] = '2026-10-22T20:00:00Z'
    status['emsd']['lastAppliedAt'] = '2026-10-22T20:00:00Z'
    status['generatedAt'] = '2026-10-22T20:00:00Z'
    official = tsh._official(tmp_path)
    official['generatedAt'] = '2026-10-22T20:00:00Z'
    official['lastSuccessAt'] = '2026-10-22T20:00:00Z'
    status['official']['lastAcquiredAt'] = '2026-10-22T20:00:00Z'
    status['official']['lastAppliedAt'] = '2026-10-22T20:00:00Z'
    price = _receipt(snapshot_hash=live_hash,
                     meta=_meta(appliedSnapshotHash=live_hash),
                     biggo=_biggo(snapshotHash=live_hash))
    r = shm.evaluate(status=status, repo=str(tmp_path), now=NOW,
                     price_status_doc=price, official_status_doc=official)
    assert r['ok'] is True, r['failures']
    # EMSD/workflow heartbeat still expires independently.
    late = NOW + dt.timedelta(hours=40)
    r2 = shm.evaluate(status=status, repo=str(tmp_path), now=late,
                      price_status_doc=price, official_status_doc=official)
    assert r2['ok'] is False
    assert any('emsd:age>36h' in f for f in r2['failures'])
    assert not any('biggo:age>36h' in f for f in r2['failures'])
    # A bad deployment hash remains red even with a valid price receipt.
    bad = copy.deepcopy(status)
    bad['publish']['releasePayloadHash'] = 'sha256:' + '0' * 64
    dep = tsh._deployment(tmp_path)
    dep['liveHashes']['index.html'] = 'sha256:' + '0' * 64
    r3 = shm.evaluate(status=bad, repo=str(tmp_path), now=NOW, price_status_doc=price,
                      require_deployed=True, deployment_receipt=dep,
                      metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64},
                      web_root=str(tmp_path), official_status_doc=official)
    assert r3['ok'] is False and any('publish:' in f for f in r3['failures'])


def test_provider_failure_visible_even_with_old_snapshot_and_receipt():
    price = _receipt(biggo=_biggo(status='needs-review', alert=True))
    assert price['status'] == 'blocked'
    assert price['safeReason'] == 'provider-failure'
    assert price['healthy'] is False


def test_active_no_progress_and_legal_vs_fake_cooldown():
    now = 1792600000  # 2026-10-21T...
    active_meta = _meta(price_batch_start='2026-10-18', price_batch_idx=2,
                        last_price_month='2026-10', appliedAt='2026-10-19T00:00:00Z',
                        appliedBundleHash='sha256:' + 'b' * 64)
    biggo = _biggo(status='completed', at='2026-10-19T00:00:00Z')
    inputs = {'meta': active_meta, 'demand': _demand(), 'biggo': biggo,
              'completed_sync': {}, 'snapshot_hash': 'sha256:' + 'a' * 64,
              'anchor': None, 'shared': False}
    r = price_status.build_receipt(now=now + 3 * 86400, **inputs)
    assert r['status'] == 'active' and r['safeReason'] == 'active-no-progress'
    assert r['healthy'] is False
    # Legal cooldown: waiting measured from its actual recovery time.
    legal_meta = dict(active_meta, blocked_until=now + 3600)
    r2 = price_status.build_receipt(now=now, **dict(inputs, meta=legal_meta))
    assert r2['status'] == 'active' and r2['safeReason'] == 'cooldown'
    assert r2['nextEligibleAt'] == price_status._stamp(
        dt.datetime.fromtimestamp(now + 3600, dt.timezone.utc))
    price_status.validate_receipt(r2, now=now)
    # Shortly after legal recovery, the 36h stall deadline runs from recovery,
    # not from the ancient last progress.
    r2b = price_status.build_receipt(now=now + 3600 + 60,
                                     **dict(inputs, meta=legal_meta))
    assert r2b['status'] == 'active' and r2b['safeReason'] == 'cooldown'
    assert r2b['healthy'] is True
    assert r2b['nextEligibleAt'] == price_status._stamp(
        dt.datetime.fromtimestamp(now + 3600 + 36 * 3600, dt.timezone.utc))
    # Fake/unbounded future cooldown never becomes a legal wait.
    fake = dict(active_meta, blocked_until=now + price_status.MAX_COOLDOWN_SECONDS + 1)
    r3 = price_status.build_receipt(now=now, **dict(inputs, meta=fake))
    assert r3['status'] == 'blocked' and r3['safeReason'] == 'invalid-cooldown'
    # Repeated skip receipt time is not verified progress.
    skips = _biggo(status='skip-not-active', at='2026-10-21T23:00:00Z')
    r4 = price_status.build_receipt(now=now + 3 * 86400,
                                    **dict(inputs, biggo=skips))
    assert r4['status'] == 'active' and r4['healthy'] is False
    assert r4['lastVerifiedProgressAt'] == '2026-10-19T00:00:00Z'


def test_waiting_and_no_demand_states():
    meta = _meta(last_price_month='2026-09', price_batch_idx=7,
                 last_applied_cycle='2026-09-10:7/7',
                 appliedAt='2026-09-20T00:00:00Z')
    inputs = {'meta': meta, 'demand': _demand(), 'biggo': _biggo(),
              'completed_sync': {}, 'snapshot_hash': 'sha256:' + 'a' * 64,
              'anchor': None, 'shared': False}
    r = price_status.build_receipt(now=NOW, **inputs)
    assert r['status'] == 'waiting-eligible' and r['healthy'] is True
    empty = price_demand.empty(NOW)
    r2 = price_status.build_receipt(now=NOW, **dict(inputs, demand=empty))
    assert r2['status'] == 'no-demand' and r2['healthy'] is True
    # A malformed/missing demand fact is never silently no-demand.
    r3 = price_status.build_receipt(now=NOW, **dict(inputs, demand=None))
    assert r3['status'] == 'blocked' and r3['safeReason'] == 'demand-missing'


def test_v3_consumer_rejects_missing_bad_and_forged_self_hash():
    # Build a real v3 summary using the existing producer fixture.
    assert price_status.validate_receipt(_receipt(), now=NOW)
    good = _receipt()
    summary = {'schemaVersion': 3, 'kind': 'aircon-source-health-summary',
               'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'ok': False, 'status': 'unhealthy', 'failures': [],
               'operations': {'ok': True, 'status': 'healthy', 'failures': [],
                              'coverageExpectedPending': False,
                              'priceHealthy': True},
               'coverage': {'status': 'verified', 'pending': False, 'expected': False},
               'sources': {'emsd': {'status': 'ok', 'snapshotHash': 'sha256:' + '1' * 64},
                           'biggo': {'status': 'ok', 'snapshotHash': 'sha256:' + 'a' * 64},
                           'official': {'status': 'ok', 'snapshotHash': 'sha256:' + '2' * 64}},
               'sourceStatusHash': 'sha256:' + '3' * 64,
               'sourceStatusGeneratedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'releasePayloadHash': 'sha256:' + '4' * 64,
               'emsdSnapshotHash': 'sha256:' + '1' * 64,
               'biggoSnapshotHash': 'sha256:' + 'a' * 64,
               'officialSnapshotHash': 'sha256:' + '2' * 64,
               'officialManifestHash': 'sha256:' + '2' * 64,
               'build': 'B1', 'deploymentReceipt': 'r1',
               'price': good, 'priceHealthy': True}
    good['status'] = 'month-completed'
    # Fix outer operation status to match a healthy v3 fixture (only price/v3 checks matter).
    summary['ok'] = True
    summary['status'] = 'healthy'
    summary['operations']['ok'] = True
    summary['operations']['status'] = 'healthy'
    # Missing receipt.
    bad = copy.deepcopy(summary); bad.pop('price')
    assert chs.evaluate(bad, now=NOW)['ok'] is False
    assert any('price-receipt-missing' in f for f in chs.evaluate(bad, now=NOW)['failures'])
    # Self-hash valid but internally contradictory: month-completed with stage 3.
    forged = copy.deepcopy(good)
    forged['stage'] = 3
    body = {k: v for k, v in forged.items() if k != 'receiptHash'}
    from biggo_canonical import canonical_json_bytes, sha256_id
    forged['receiptHash'] = sha256_id(canonical_json_bytes(body))
    forged_summary = copy.deepcopy(summary); forged_summary['price'] = forged
    assert chs.evaluate(forged_summary, now=NOW)['ok'] is False
    # Binding mismatch between price evidence and the summary snapshot.
    mismatch = copy.deepcopy(summary)
    mismatch['price'] = copy.deepcopy(good)
    mismatch['biggoSnapshotHash'] = 'sha256:' + 'f' * 64
    assert chs.evaluate(mismatch, now=NOW)['ok'] is False


def test_v1_v2_summary_readers_keep_original_strict_meaning():
    v1 = {'schemaVersion': 1, 'kind': 'aircon-source-health-summary',
          'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'), 'ok': False,
          'status': 'unhealthy', 'failures': ['x']}
    assert chs.evaluate(v1, now=NOW)['ok'] is False
    v2 = {'schemaVersion': 2, 'kind': 'aircon-source-health-summary',
          'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'), 'ok': False,
          'status': 'unhealthy', 'failures': ['x']}
    assert chs.evaluate(v2, now=NOW)['ok'] is False
    # v2 cannot become v3 by adding a price object.
    v2['price'] = _receipt()
    assert chs.evaluate(v2, now=NOW)['ok'] is False


def test_source_health_main_emits_v3_and_never_defaults_missing_price_to_green(tmp_path):
    tsh._repo(tmp_path)
    live_hash = sha256_json(json.load(open(tmp_path / 'biggo_prices.json')))
    status = tsh._status(tmp_path)
    status['biggo']['snapshotHash'] = live_hash
    status['biggo']['lastAppliedAt'] = '2026-10-20T20:15:41Z'
    status['emsd']['lastAcquiredAt'] = '2026-10-22T20:00:00Z'
    status['emsd']['lastAppliedAt'] = '2026-10-22T20:00:00Z'
    status['generatedAt'] = '2026-10-22T20:00:00Z'
    official = tsh._official(tmp_path)
    official['generatedAt'] = '2026-10-22T20:00:00Z'
    official['lastSuccessAt'] = '2026-10-22T20:00:00Z'
    status['official']['lastAcquiredAt'] = '2026-10-22T20:00:00Z'
    status['official']['lastAppliedAt'] = '2026-10-22T20:00:00Z'
    (tmp_path / 'source_status.json').write_text(json.dumps(status), encoding='utf-8')
    (tmp_path / 'official_batch_status.json').write_text(json.dumps(official), encoding='utf-8')
    (tmp_path / 'price_demand.json').write_text(
        json.dumps(_demand()), encoding='utf-8')
    (tmp_path / 'prices_meta.json').write_text(
        json.dumps(_meta(appliedSnapshotHash=live_hash)), encoding='utf-8')
    (tmp_path / 'biggo_stage_status.json').write_text(
        json.dumps(_biggo(snapshotHash=live_hash)), encoding='utf-8')
    out = tmp_path / 'source_health_summary.json'
    args = ['--status', str(tmp_path / 'source_status.json'), '--repo', str(tmp_path),
            '--state', str(tmp_path / 'state.json'),
            '--report', str(tmp_path / 'report.json'),
            '--summary', str(out),
            '--official-status-file', str(tmp_path / 'official_batch_status.json'),
            '--now', NOW.strftime('%Y-%m-%dT%H:%M:%SZ')]
    assert shm.main(args) == 0
    summary = json.load(open(out))
    assert summary['schemaVersion'] == 3
    assert summary['price']['status'] == 'month-completed'
    assert summary['priceHealthy'] is True
    # A v3 summary with the full receipt removed must be rejected by the
    # public consumer, never silently treated as the old v2 contract.
    del summary['price']
    assert chs.evaluate(summary, now=NOW)['ok'] is False


def test_v3_consumer_rejects_replayed_old_receipt_with_new_checkedAt():
    old_now = NOW - dt.timedelta(days=3)
    old_meta = _meta(appliedAt='2026-10-19T00:00:00Z')
    old_biggo = _biggo(at='2026-10-19T00:00:00Z')
    old_receipt = price_status.build_receipt(
        now=old_now, meta=old_meta, demand=_demand(), biggo=old_biggo,
        completed_sync={}, snapshot_hash='sha256:' + 'a' * 64,
        anchor=None, shared=False)
    assert old_receipt['status'] == 'month-completed'
    summary = {'schemaVersion': 3, 'kind': 'aircon-source-health-summary',
               'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'ok': True, 'status': 'healthy', 'failures': [],
               'operations': {'ok': True, 'status': 'healthy', 'failures': [],
                              'coverageExpectedPending': False,
                              'priceHealthy': True},
               'coverage': {'status': 'verified', 'pending': False, 'expected': False},
               'sources': {'emsd': {'status': 'ok', 'snapshotHash': 'sha256:' + '1' * 64},
                           'biggo': {'status': 'ok', 'snapshotHash': 'sha256:' + 'a' * 64},
                           'official': {'status': 'ok', 'snapshotHash': 'sha256:' + '2' * 64}},
               'sourceStatusHash': 'sha256:' + '3' * 64,
               'sourceStatusGeneratedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
               'releasePayloadHash': 'sha256:' + '4' * 64,
               'emsdSnapshotHash': 'sha256:' + '1' * 64,
               'biggoSnapshotHash': 'sha256:' + 'a' * 64,
               'officialSnapshotHash': 'sha256:' + '2' * 64,
               'officialManifestHash': 'sha256:' + '2' * 64,
               'build': 'B1', 'deploymentReceipt': 'r1',
               'price': old_receipt, 'priceHealthy': True}
    result = chs.evaluate(summary, now=NOW, max_age_hours=48)
    assert result['ok'] is False
    assert any('price-receipt-stale' in f for f in result['failures'])
