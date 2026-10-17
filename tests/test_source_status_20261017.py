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
        official_status={'decision': 'advanced', 'generatedAt': '2026-10-16T19:44:37Z',
                         'lastSuccessAt': '2026-10-16T19:44:37Z',
                         'snapshotId': 'official-1',
                         'snapshotHash': 'sha256:' + 'b' * 64},
        deployment_receipt=dep,
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    summary = {
        'schemaVersion': 1, 'kind': 'aircon-source-health-summary',
        'checkedAt': '2026-10-17T04:00:00Z', 'ok': True, 'status': 'healthy',
        'failures': [], 'sourceStatusHash': 'sha256:' + '1' * 64,
        'sourceStatusGeneratedAt': s['generatedAt'],
        'build': s['publish']['build'], 'releasePayloadHash': s['publish']['releasePayloadHash'],
        'deploymentReceipt': s['publish']['deploymentReceipt'],
        'emsdSnapshotHash': s['emsd']['snapshotHash'],
        'biggoSnapshotHash': s['biggo']['snapshotHash'],
        'officialSnapshotHash': s['official']['snapshotHash'],
        'officialManifestHash': s['official']['snapshotHash'],
        'sources': {n: {'status': s[n]['status'], 'snapshotHash': s[n]['snapshotHash']}
                    for n in ('emsd', 'biggo', 'official')},
    }
    r = check_health_summary.evaluate(summary, now=dt.datetime(2026, 10, 17, 4, 0,
                                                               tzinfo=dt.timezone.utc))
    assert r['ok'] is True, r
    # Raw source status without health-check evidence is not PASS.
    raw = check_health_summary.evaluate(s)
    assert raw['ok'] is None and raw['status'] == 'unknown'
    # Blocked source in summary must be unhealthy.
    blocked = json.loads(json.dumps(summary)); blocked['sources']['biggo']['status'] = 'blocked'
    assert check_health_summary.evaluate(blocked)['ok'] is False
    bad = json.loads(json.dumps(summary)); bad['emsdSnapshotHash'] = None
    assert check_health_summary.evaluate(bad)['ok'] is False
    stale = json.loads(json.dumps(summary)); stale['checkedAt'] = '2020-01-01T00:00:00Z'
    assert check_health_summary.evaluate(stale)['ok'] is False
    missing = json.loads(json.dumps(summary)); missing.pop('sources')
    assert check_health_summary.evaluate(missing)['ok'] is False
    wrong = json.loads(json.dumps(summary)); wrong['build'] = 'OTHER'
    assert check_health_summary.evaluate(wrong, expected_metadata={'build': 'B1'})['ok'] is False
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


def test_daily_has_completed_receive_phase_without_manual_confirm():
    p = os.path.join(BASE, '.github', 'workflows', 'daily-update.yml')
    text = open(p, encoding='utf-8').read()
    assert 'sync_biggo_completed.py --apply --degraded-exit-zero' in text
    assert 'biggo_completed_sync.json' in text
    assert 'confirm_no_provider' not in text
    assert not os.path.exists(os.path.join(BASE, '.github', 'workflows',
                                           'biggo-completed-sync.yml'))
    allow = open(os.path.join(BASE, 'scripts', 'stage_artifacts.py'), encoding='utf-8').read()
    assert 'biggo_completed_sync.json' in allow


def _valid_deployment():
    return {'success': True, 'deployedAt': '2026-10-17T03:30:00Z',
            'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
            'receiptId': 'r1',
            'liveHashes': {'index.html': 'sha256:' + '1' * 64,
                           '空調對比報告.pdf': 'sha256:' + '2' * 64,
                           'emsd_空調能源標籤.csv': 'sha256:' + '3' * 64,
                           'metadata.json': 'sha256:' + '4' * 64}}


def _meta_for_deploy():
    return {'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
            'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64}


def test_deployment_matches_requires_build_payload_receipt_and_all4_hashes():
    meta = _meta_for_deploy()
    assert source_status.deployment_matches(meta, _valid_deployment()) is True
    variants = []
    for field in ('build', 'releasePayloadHash'):
        r = _valid_deployment(); r.pop(field); variants.append(r)
        r = _valid_deployment(); r[field] = 'OLD-' + r[field]; variants.append(r)
    r = _valid_deployment(); r['liveHashes']['index.html'] = 'sha256:' + '1' * 12
    variants.append(r)
    r = _valid_deployment(); r['liveHashes'].pop('metadata.json'); variants.append(r)
    r = _valid_deployment(); r['liveHashes']['extra.txt'] = 'sha256:' + '5' * 64
    variants.append(r)
    r = _valid_deployment(); r.pop('receiptId'); variants.append(r)
    for r in variants:
        assert source_status.deployment_matches(meta, r) is False, r
    old_meta = dict(meta, build='B2')
    assert source_status.deployment_matches(old_meta, _valid_deployment()) is False


def test_official_advanced_missing_full_hash_cannot_be_ok_or_healthy(tmp_path):
    import check_health_summary
    s = source_status.build_status(
        metadata=_meta_for_deploy(),
        emsd_receipt=_receipt(),
        prices_meta={'appliedAt': '2026-10-16T16:00:00Z',
                     'appliedSnapshotHash': 'sha256:' + 'b' * 64},
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64},
        official_status={'decision': 'advanced',
                         'generatedAt': '2026-10-16T19:44:37Z',
                         'lastSuccessAt': '2026-10-16T19:44:37Z',
                         'snapshotId': 'official-1',
                         'snapshotHash': None},
        deployment_receipt=_valid_deployment(),
        biggo_snapshot_hash='sha256:' + 'b' * 64,
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    assert s['official']['status'] == 'degraded'
    assert s['official']['safeReason'] == 'official-snapshot-untrusted'
    assert s['official']['lastAcquiredAt'] is None
    r = check_health_summary.evaluate(s, now=dt.datetime(2026, 10, 17, 4, 0,
                                                         tzinfo=dt.timezone.utc))
    assert r['ok'] is not True
    assert r['status'] == 'unknown'


def test_official_failed_unknown_never_uses_site_deployment_time():
    s = source_status.build_status(
        metadata=_meta_for_deploy(),
        emsd_receipt=_receipt(),
        official_status={'decision': 'queue-kept-fail-closed',
                         'generatedAt': '2026-10-17T03:00:00Z',
                         'lastSuccessAt': '2026-10-10T01:00:00Z',
                         'snapshotId': 'official-old',
                         'snapshotHash': 'sha256:' + 'b' * 64},
        deployment_receipt=_valid_deployment())
    assert s['official']['status'] == 'degraded'
    assert s['official']['lastAppliedAt'] is None
    assert s['official']['lastAppliedDate'] is None
    assert s['official']['lastAcquiredAt'] == '2026-10-10T01:00:00Z'
    assert s['official']['snapshotHash'] == 'sha256:' + 'b' * 64


def test_official_ok_requires_full_hash_id_and_explicit_apply_fact():
    base = {'decision': 'advanced', 'generatedAt': '2026-10-16T19:44:37Z',
            'lastSuccessAt': '2026-10-16T19:44:37Z',
            'snapshotId': 'official-1', 'snapshotHash': 'sha256:' + 'b' * 64,
            'lastAppliedAt': '2026-10-16T20:00:00Z',
            'appliedSnapshotHash': 'sha256:' + 'b' * 64}
    s = source_status.build_status(metadata=_meta_for_deploy(),
                                  official_status=base,
                                  deployment_receipt=_valid_deployment())
    assert s['official']['status'] == 'ok'
    assert s['official']['lastAppliedAt'] == '2026-10-16T20:00:00Z'
    no_id = dict(base); no_id.pop('snapshotId')
    s2 = source_status.build_status(metadata=_meta_for_deploy(), official_status=no_id,
                                    deployment_receipt=_valid_deployment())
    assert s2['official']['status'] == 'degraded'
    assert s2['official']['lastAppliedAt'] is None


def test_source_status_has_no_duplicate_dict_literal_keys():
    import ast
    path = os.path.join(BASE, 'scripts', 'source_status.py')
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            assert len(keys) == len(set(keys)), f'duplicate literal keys: {keys}'


def test_completed_sync_missing_history_degrades_biggo_even_if_provider_ok():
    s = source_status.build_status(
        metadata=_meta_for_deploy(),
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64},
        biggo_snapshot_hash='sha256:' + 'b' * 64,
        completed_sync={'ok': False, 'reason': 'missing-local-history',
                        'plan': {'ok': False, 'reason': 'missing-local-history'}},
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    assert s['biggo']['status'] == 'degraded'
    assert s['biggo']['safeReason'] == 'completed-sync-missing-history'
    assert s['biggo']['snapshotHash'] == 'sha256:' + 'b' * 64
    import check_health_summary
    assert check_health_summary.evaluate(
        s, now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc))['ok'] is not True


def test_completed_sync_success_does_not_degrade():
    s = source_status.build_status(
        metadata=_meta_for_deploy(),
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64},
        biggo_snapshot_hash='sha256:' + 'b' * 64,
        completed_sync={'ok': True, 'reason': 'applied', 'applied': True},
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())
    assert s['biggo']['status'] == 'ok'


def test_completed_receive_step_precedes_provider_stage_in_daily():
    p = os.path.join(BASE, '.github', 'workflows', 'daily-update.yml')
    text = open(p, encoding='utf-8').read()
    i_recv = text.index('接收 shared BigGo completed bundle')
    i_provider = text.index('價錢快照分批更新（BigGo API')
    assert i_recv < i_provider, 'completed receive 必須在 provider stage 前'


def test_completed_sync_report_stays_out_of_worktree_and_health_not_cross_build_bound():
    daily = open(os.path.join(BASE, '.github', 'workflows', 'daily-update.yml'), encoding='utf-8').read()
    assert 'cp "$RUNNER_TEMP/biggo_completed_sync.json" biggo_completed_sync.json' not in daily
    assert '--completed-sync-report "$RUNNER_TEMP/biggo_completed_sync.json"' in daily
    fresh = open(os.path.join(BASE, '.github', 'workflows', 'freshness-monitor.yml'), encoding='utf-8').read()
    assert '--metadata metadata.json' not in fresh, 'public build metadata is not the private site build identity'


def test_biggo_applied_full_timestamp_and_snapshot_identity_from_meta():
    meta = {'last_run': '2026-10-12', 'price_batch_start': '2026-10-10',
            'price_batch_idx': 3, 'appliedAt': '2026-10-17T08:52:47Z',
            'appliedSnapshotHash': 'sha256:' + 'b' * 64,
            'appliedSnapshotId': 'biggo-2026-10-10:3/7-stage3-bbbbbbbbbbbb'}
    s = source_status.build_status(
        metadata={'datasetDate': '2026-10-17', 'datasetHash': 'sha256:' + 'a' * 64,
                  'deployTime': '2026-10-17T08:53:35Z'},
        emsd_receipt=_receipt(), prices_meta=meta,
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': 'sha256:' + 'b' * 64})
    assert s['biggo']['status'] == 'ok'
    assert s['biggo']['lastAppliedAt'] == '2026-10-17T08:52:47Z'
    assert s['biggo']['lastAppliedDate'] == '2026-10-17'
    assert s['biggo']['snapshotId'] == 'biggo-2026-10-10:3/7-stage3-bbbbbbbbbbbb'
    assert source_status.validate_status(s) == []
