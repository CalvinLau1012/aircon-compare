# -*- coding: utf-8 -*-
"""Migration planner: completed stage7, missing anchor, dual baselines, no-op."""
import copy
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import price_cycle  # noqa: E402
import price_cycle_migrate as mig  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402

PUB = 'a' * 40
PRIV = 'b' * 40
AT = '2026-10-22T03:00:00+00:00'


def _fixture(tmp_path, *, public_snapshot=None, private_snapshot=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    snap = {'M1': {'price': '$1,000起', 'merchants': 1,
                   'url': 'https://x', 'updated': '2026-10-01'}}
    base = {'M1': {'price': '$900起', 'merchants': 1,
                   'url': 'https://x', 'updated': '2026-09-01'}}
    sr = {'schemaVersion': 1, 'cycleId': '2026-10-10:7/7', 'stage': 7,
          'mode': 'price-batch', 'generatedAt': '2026-10-20T20:15:34Z',
          'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
          'requestAttempts': {'token': 0, 'search': 1},
          'responseStatusCounts': {'200': 1},
          'outcomes': [{'model': 'M1', 'canonicalKey': 'GREE|M1',
                        'outcome': 'priced', 'price': snap['M1'],
                        'resultHash': sha256_id(canonical_json_bytes(snap['M1']))}],
          'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
          'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                      'blacklistUpserts': {}, 'blacklistRemovals': []},
          'preState': {'trackingHash': sha256_id(canonical_json_bytes({})),
                       'blacklistHash': sha256_id(canonical_json_bytes({}))},
          'postState': {'trackingHash': sha256_id(canonical_json_bytes({})),
                        'blacklistHash': sha256_id(canonical_json_bytes({}))}}
    sr['effectsHash'] = sha256_id(canonical_json_bytes(sr['effects']))
    import biggo_acquisition
    sr['outcomes'][0]['acquiredAt'] = '2026-10-20T20:15:33Z'
    sr['acquisition'] = biggo_acquisition.build_block(
        cycle_id=sr['cycleId'], stage=7, base_snapshot=base, new_snapshot=snap,
        outcomes=sr['outcomes'], batch_started_at='2026-10-20T20:15:00Z',
        generated_at=sr['generatedAt'], require_complete=True)
    manifest = {'schemaVersion': 2, 'cycleId': '2026-10-10:7/7', 'stage': 7,
                'mode': 'price-batch', 'publishedAt': '2026-10-20T20:15:34Z',
                'baseSnapshotHash': sha256_id(canonical_json_bytes(base)),
                'newSnapshotHash': sha256_id(canonical_json_bytes(snap)),
                'snapshotHash': sha256_id(canonical_json_bytes(snap)),
                'stageResultHash': sha256_id(canonical_json_bytes(sr)),
                'recordCount': len(snap)}
    meta = {'last_price_month': '2026-10', 'price_batch_idx': 7,
            'last_applied_cycle': '2026-10-10:7/7',
            'appliedSnapshotHash': sha256_id(canonical_json_bytes(snap)),
            'appliedBundleHash': sha256_id(canonical_json_bytes(manifest)),
            'appliedAt': '2026-10-21T16:30:05Z'}
    meta['appliedBundleHash'] = sha256_id(canonical_json_bytes(manifest))
    files = {'biggo_prices.json': snap, 'prices_meta.json': meta,
             'base-biggo_prices.json': base,
             'model_status.json': {},
             'model_blacklist.json': {'models': {}},
             'new_models.json': {'models': [
                 {'brand': '格力', 'model': 'M1', 'first_seen': '2026-10-01'}]}}
    for name, data in files.items():
        (tmp_path / name).write_text(json.dumps(data), encoding='utf-8')
    (tmp_path / 'stage7-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (tmp_path / 'stage7-result.json').write_text(json.dumps(sr), encoding='utf-8')
    pub = copy.deepcopy(meta)
    priv = copy.deepcopy(meta)
    priv['appliedAt'] = '2026-10-22T00:30:05Z'
    if public_snapshot:
        pub['appliedSnapshotHash'] = public_snapshot
    if private_snapshot:
        priv['appliedSnapshotHash'] = private_snapshot
    (tmp_path / 'public-meta.json').write_text(json.dumps(pub), encoding='utf-8')
    (tmp_path / 'private-meta.json').write_text(json.dumps(priv), encoding='utf-8')
    return tmp_path


def _plan(repo, **over):
    kwargs = dict(repo=str(repo),
                  prices_meta=str(repo / 'prices_meta.json'),
                  snapshot=str(repo / 'biggo_prices.json'),
                  new_models=str(repo / 'new_models.json'),
                  stage_manifest=str(repo / 'stage7-manifest.json'),
                  stage_result=str(repo / 'stage7-result.json'),
                  base_snapshot=str(repo / 'base-biggo_prices.json'),
                  public_meta=str(repo / 'public-meta.json'),
                  private_meta=str(repo / 'private-meta.json'),
                  source_public=PUB, source_private=PRIV, at=AT)
    kwargs.update(over)
    return mig.plan(**kwargs)


def test_preview_completed_stage7_missing_anchor_and_price_batch_start(tmp_path):
    repo = _fixture(tmp_path)
    r = _plan(repo)
    assert r['ok'] is True and r['ready'] is True, r['blockedReasons']
    assert r['cycle'] == '2026-10-10:7/7' and r['stage'] == 7
    assert r['anchor']['status'] == 'completed'
    assert r['anchor']['stage'] == 7
    assert r['anchor']['initialBaseHash'] == r['newSnapshotHash']
    assert r['transition']['writers']['github']['sourceRevision'] == PUB
    assert r['transition']['writers']['private']['sourceRevision'] == PRIV
    assert r['transition']['initial']['snapshotHash'] == r['newSnapshotHash']
    assert r['missingRefs']  # earlier stage chain/base not silently invented
    assert r['demand']['demands'][0]['status'] == 'processed'
    assert r['applyPlan']['expectedCoordinates']['anchorSha'] is None


def test_public_private_applied_at_may_differ_same_snapshot(tmp_path):
    repo = _fixture(tmp_path)
    r = _plan(repo)
    assert r['ok'] is True


def test_snapshot_or_source_mismatch_stops(tmp_path):
    repo = _fixture(tmp_path, public_snapshot='sha256:' + 'f' * 64)
    r = _plan(repo)
    assert r['ok'] is False
    assert any('public:applied-snapshot-mismatch' in e for e in r['blockedReasons'])
    repo2 = _fixture(tmp_path / 'b')
    r2 = _plan(repo2, source_public='not-a-rev')
    assert r2['ok'] is False
    assert 'public:source-revision-invalid' in r2['blockedReasons']


def test_apply_writes_anchor_transition_history_demand_then_noop(tmp_path):
    repo = _fixture(tmp_path)
    anchor = repo / 'anchor.json'
    transition = repo / 'anchor.transition'
    history = repo / 'history-2026-10.json'
    r = _plan(repo, anchor_file=str(anchor), transition_file=str(transition),
              history_file=str(history))
    assert r['ok'] is True and r['mode'] == 'applied', r['blockedReasons']
    assert json.loads(anchor.read_text())['stage'] == 7
    assert json.loads(transition.read_text())['oldEntryStopped'] is True
    assert json.loads(history.read_text())['finalSnapshotHash'] == r['newSnapshotHash']
    assert (repo / 'price_demand.json').is_file()
    # Repeated apply is byte-identical and a true no-op.
    before = {p.name: p.read_bytes() for p in (anchor, transition, history,
                                               repo / 'price_demand.json')}
    r2 = _plan(repo, anchor_file=str(anchor), transition_file=str(transition),
               history_file=str(history), at='2026-10-23T03:00:00+00:00')
    assert r2['ok'] is True and r2['mode'] == 'no-op'
    after = {p.name: p.read_bytes() for p in (anchor, transition, history,
                                              repo / 'price_demand.json')}
    assert after == before
    # Any prestate conflict fails closed without overwriting.
    anchor.write_text('{"different": true}', encoding='utf-8')
    r3 = _plan(repo, anchor_file=str(anchor), transition_file=str(transition),
               history_file=str(history))
    assert r3['ok'] is False and 'anchor:prestate-conflict' in r3['blockedReasons']


def test_demand_prestate_conflict_blocks_when_anchor_history_not_semantic_noop(tmp_path):
    repo = _fixture(tmp_path)
    anchor = repo / 'anchor.json'; transition = repo / 'anchor.transition'
    history = repo / 'history-2026-10.json'
    assert _plan(repo, anchor_file=str(anchor), transition_file=str(transition),
                 history_file=str(history))['mode'] == 'applied'
    demand = repo / 'price_demand.json'
    doc = json.loads(demand.read_text())
    doc['demands'][0]['status'] = 'pending'
    doc['demands'][0].pop('processedAt', None)
    doc['demands'][0]['evidence'] = []
    doc['demands'][0]['firstSeenAt'] = '2026-10-01T00:00:00Z'
    demand.write_text(json.dumps(doc), encoding='utf-8')
    # Make one prestate absent so the early semantic-no-op path is not taken.
    transition.unlink()
    r = _plan(repo, anchor_file=str(anchor), transition_file=str(transition),
              history_file=str(history))
    assert r['ok'] is False
    assert 'demand:prestate-conflict' in r['blockedReasons']


def test_unknown_legacy_demand_blocks_enable_but_not_terminal_baseline(tmp_path):
    repo = _fixture(tmp_path)
    nm = json.loads((repo / 'new_models.json').read_text())
    nm['models'].append({'brand': '格力', 'model': 'OLD-UNKNOWN',
                         'first_seen': '2026-09-01'})
    (repo / 'new_models.json').write_text(json.dumps(nm), encoding='utf-8')
    r = _plan(repo)
    assert r['ok'] is True
    assert r['completedBaselineReady'] is True
    assert r['priceStartReady'] is False
    assert r['ready'] is False
    unknown = [e for e in r['demandEligibility']
               if e['key'] == 'GREE|OLDUNKNOWN']
    assert unknown and unknown[0]['eligibility'] == 'unknown-provenance'
    # Terminal baseline can still be installed in receive-only mode.
    assert _plan(repo, anchor_file=str(repo / 'anchor.json'))['mode'] == 'applied'
