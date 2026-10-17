# -*- coding: utf-8 -*-
"""Source status: truthful dates, degraded price, schema compatibility, UI."""
import datetime as dt
import importlib.util
import json
import os
import sys

import pytest
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import source_status  # noqa: E402
import generate_html  # noqa: E402


def _receipt():
    return {
        'success': True, 'aborted': False, 'error': None,
        'retrievedAt': '2026-10-16T16:31:12Z',
        'datasetHash': 'sha256:' + 'a' * 64,
    }


def _prices():
    return {'last_run': '2026-10-12', 'price_batch_start': '2026-10-10',
            'price_batch_idx': 3, 'appliedAt': '2026-10-12T00:37:36Z'}


def test_legacy_missing_facts_are_unknown_not_today():
    s = source_status.build_status(now=1_700_000_000)
    assert s['emsd']['status'] == 'unknown'
    assert s['biggo']['status'] == 'unknown'
    assert s['emsd']['lastAcquiredAt'] is None
    assert s['biggo']['lastAppliedDate'] is None
    assert s['biggo']['safeReason'] == 'legacy-unknown'
    assert source_status.validate_status(s) == []


def test_fresh_emsd_and_successful_biggo_are_distinct_dates():
    s = source_status.build_status(
        metadata={'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64,
                  'datasetRetrievedAt': '2026-10-16T16:31:12Z',
                  'datasetSnapshotId': 'emsd-2026-10-17-aaaaaaaaaaaa',
                  'deployTime': '2026-10-16T16:32:54Z', 'build': 'B20261017',
                  'commit': 'a' * 40},
        emsd_receipt=_receipt(), prices_meta=_prices(),
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64},
        now=1_700_000_000)
    assert s['emsd']['status'] == 'ok'
    assert s['emsd']['lastAcquiredAt'] == '2026-10-16T16:31:12Z'
    # Package/deployTime is not proof of a live apply; only an actual deployment
    # receipt may set lastAppliedAt.
    assert s['emsd']['lastAppliedAt'] is None
    assert s['publish']['packagedAt'] == '2026-10-16T16:32:54Z'
    assert s['publish']['deployedAt'] is None
    assert s['biggo']['status'] == 'ok'
    assert s['biggo']['lastAppliedDate'] == '2026-10-12'
    assert s['official']['status'] == 'unknown'
    assert source_status.validate_status(s) == []


def test_price_apply_failure_is_degraded_and_preserves_old_snapshot_wording():
    s = source_status.build_status(
        metadata={'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64,
                  'deployTime': '2026-10-16T16:32:54Z'},
        emsd_receipt=_receipt(), prices_meta=_prices(),
        biggo_status={'status': 'completed-apply-failed', 'cycle': '2026-10-10:3/7',
                      'stage': 3, 'snapshotHash': 'sha256:' + 'b' * 64})
    assert s['biggo']['status'] == 'degraded'
    assert s['biggo']['safeReason'] == 'price-apply-failed'
    assert s['biggo']['snapshotHash'] is None, 'failed bundle hash 唔可以當目前沿用快照'
    line1, line2 = generate_html.format_status(
        {'datasetDate': '2026-10-17', 'deployTime': '2026-10-16T16:32:54Z',
         'version': '1.2.9', 'sourceStatus': s}, '1.2.9')
    assert '2026-10-17' in line1
    assert '沿用先前已驗證快照' in line2
    assert '2026-09-14' not in line2, '唔可以硬編舊 BigGo 日期'


def test_schema_accepts_source_status_and_rejects_unknown_status():
    from extract_governance import extract_blocks, GOV_FILE, metadata_schema
    from validate_metadata import validate
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = metadata_schema(f.read())
    s = source_status.build_status(
        metadata={'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64,
                  'deployTime': '2026-10-16T16:32:54Z'},
        emsd_receipt=_receipt(), prices_meta=_prices(),
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64})
    meta = {
        'schemaVersion': '1.0.0', 'version': '1.2.9', 'build': 'B1',
        'commit': 'a' * 40, 'deployTime': '2026-10-16T16:32:54Z',
        'workflowRunId': '1', 'deploymentType': 'release',
        'releasePayloadHash': 'sha256:' + 'c' * 64,
        'datasetDate': '2026-10-17', 'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-10-16T16:31:12Z',
        'datasetSourceUrl': 'https://example.com', 'datasetSnapshotId': 's1',
        'datasetHash': 'sha256:' + 'a' * 64, 'recordCount': 1,
        'sourceStatus': s,
    }
    assert validate(meta, schema) == []
    bad = json.loads(json.dumps(meta))
    bad['sourceStatus']['biggo']['status'] = 'invented'
    assert validate(bad, schema)
    missing_official = json.loads(json.dumps(meta))
    del missing_official['sourceStatus']['official']
    assert validate(missing_official, schema)


def test_versioned_history_is_append_only(tmp_path):
    s = source_status.build_status(now=1_700_000_000)
    p = tmp_path / 'source_status.json'
    h = tmp_path / 'history'
    source_status.write_receipt(p, s, history_dir=h)
    first = p.read_bytes()
    files = list(h.iterdir())
    assert len(files) == 1
    source_status.write_receipt(p, s, history_dir=h)
    assert p.read_bytes() == first
    assert len(list(h.iterdir())) == 1


def test_daily_workflow_has_precommit_candidate_gate_after_generation():
    p = os.path.join(BASE, '.github', 'workflows', 'daily-update.yml')
    wf = yaml.safe_load(open(p, encoding='utf-8'))
    steps = wf['jobs']['update']['steps']
    names = [s.get('name', '') for s in steps]
    i_gen = next(i for i, n in enumerate(names) if '完成 metadata.json' in n)
    i_gate = next(i for i, n in enumerate(names) if '候選接受閘門' in n)
    i_commit = next(i for i, n in enumerate(names) if '提交並推送' in n)
    assert i_gen < i_gate < i_commit
    runs = {s.get('name', ''): s.get('run', '') for s in steps}
    assert 'verify_candidate_acceptance.py' in runs[names[i_gate]]
    assert 'source_status.py' in open(p, encoding='utf-8').read()


def test_health_summary_validates_binding_and_unconfigured_unknown(tmp_path):
    import check_health_summary
    dep = {'success': True, 'deployedAt': '2026-10-17T03:30:00Z',
           'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
           'liveHashes': {'index.html': 'sha256:' + 'd' * 64,
                          '空調對比報告.pdf': 'sha256:' + 'e' * 64,
                          'emsd_空調能源標籤.csv': shm_hash('a'),
                          'metadata.json': 'sha256:' + 'f' * 64},
           'receiptId': 'r1'}
    s = source_status.build_status(
        metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
                  'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64},
        emsd_receipt=_receipt(),
        prices_meta={'appliedAt': '2026-10-16T16:00:00Z',
                     'appliedSnapshotHash': 'sha256:' + 'b' * 64},
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64},
        official_status={'decision': 'advanced', 'generatedAt': '2026-10-16T19:44:37Z'},
        deployment_receipt=dep,
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    r = check_health_summary.evaluate(s, now=dt.datetime(2026, 10, 17, 4, 0,
                                                         tzinfo=dt.timezone.utc))
    assert r['ok'] is True, r
    # Root counterexample: valid dates but a blocked source must not be healthy.
    blocked = json.loads(json.dumps(s))
    blocked['biggo']['status'] = 'blocked'
    blocked['biggo']['safeReason'] = 'needs-review'
    assert check_health_summary.evaluate(blocked)['ok'] is False
    assert any('biggo:blocked' in f for f in
               check_health_summary.evaluate(blocked)['failures'])
    bad = json.loads(json.dumps(s)); bad['emsd']['snapshotHash'] = None
    assert check_health_summary.evaluate(bad)['ok'] is False
    # A deployment receipt for a different build/payload must not be accepted.
    old_dep = dict(dep, build='OLD', releasePayloadHash='sha256:' + '0' * 64)
    old = source_status.build_status(
        metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
                  'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64},
        emsd_receipt=_receipt(), official_status={'decision': 'advanced',
                                                  'generatedAt': '2026-10-16T19:44:37Z'},
        deployment_receipt=old_dep)
    assert old['publish']['deployedAt'] is None
    report = tmp_path / 'health.json'
    assert check_health_summary.main(['--report', str(report)]) == 1
    saved = json.load(open(report))
    assert saved['ok'] is None and saved['status'] == 'unknown' and saved['skipped'] is True


def shm_hash(ch):
    return 'sha256:' + ch * 64


def test_postdeploy_source_status_binding_rejects_package_as_deploy():
    import postdeploy_check
    meta = {'datasetHash': 'sha256:' + 'a' * 64,
            'sourceStatus': source_status.build_status(
                metadata={'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64,
                          'deployTime': '2026-10-17T03:30:00Z'},
                emsd_receipt=_receipt(),
                official_status={'decision': 'advanced', 'generatedAt': '2026-10-16T19:44:37Z'})}
    assert postdeploy_check.source_status_errors(meta) == []
    meta['sourceStatus']['publish']['deployedAt'] = '2026-10-17T03:30:00Z'
    assert any('deploymentReceipt' in e for e in postdeploy_check.source_status_errors(meta))


def test_official_failed_attempt_does_not_become_successful_acquisition():
    s = source_status.build_status(
        metadata={'datasetHash': 'sha256:' + 'a' * 64},
        official_status={'decision': 'queue-kept-fail-closed',
                         'generatedAt': '2026-10-17T03:00:00Z',
                         'lastSuccessAt': '2026-10-10T01:00:00Z',
                         'snapshotId': 'official-old',
                         'snapshotHash': 'sha256:' + 'b' * 64})
    assert s['official']['status'] == 'degraded'
    assert s['official']['lastAttemptAt'] == '2026-10-17T03:00:00Z'
    assert s['official']['lastAcquiredAt'] == '2026-10-10T01:00:00Z'
    assert s['official']['snapshotHash'] == 'sha256:' + 'b' * 64
