# -*- coding: utf-8 -*-
"""User-authorized 2026-10-21 policy: trusted pure coverage pending is INFO.

These tests exercise the real batch -> publisher -> source_status -> private
monitor -> v2 public client chain, plus counterexamples.  They never contact an
official host, BigGo, GitHub, Docker or production paths.
"""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import official_coverage  # noqa: E402
import source_status  # noqa: E402
import source_health_monitor as shm  # noqa: E402
import check_freshness  # noqa: E402
import run_official_batch as rob  # noqa: E402
import publish_official_status as pub  # noqa: E402
import check_health_summary as client  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
RETRIEVED = '2026-10-21T04:00:00Z'


def _spec(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha(path):
    return 'sha256:' + hashlib.sha256(open(path, 'rb').read()).hexdigest()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _fixture(tmp_path, monkeypatch, *, old_output=None):
    repo = tmp_path
    (repo / 'emsd_空調能源標籤.csv').write_text('a,b\n1,2\n', encoding='utf-8')
    _write(repo / 'update_queue.json', {'stage': 1, 'models': ['M1']})
    (repo / 'advance_queue.py').write_text('import sys; sys.exit(0)\n')
    (repo / 'fake.py').write_text(
        'import json, os\n'
        'p=os.path.join(os.path.dirname(os.path.abspath(__file__)),"fake_official.json")\n'
        'json.dump({"OLD":{"size":"old"}}, open(p,"w"))\n'
        'print("AIRCON_FETCH_RECEIPT " + json.dumps({"schemaVersion":1,'
        '"script":"fake.py","attempted":1,"succeeded":0,"failed":1,'
        '"succeededModels":[],"failedModels":["M1"],"alreadyVerified":[],'
        '"covers":[],"failureReasons":{"M1":"http-404"},'
        '"coveragePendingModels":["M1"],"coveragePendingReasons":{"M1":"http-404"}}))\n')
    (repo / 'fake_official.json').write_text(
        json.dumps(old_output or {'OLD': {'size': 'old'}}), encoding='utf-8')
    monkeypatch.setattr(rob, 'BASE', str(repo))
    monkeypatch.setattr(pub, 'BASE', str(repo))
    monkeypatch.setattr(rob, 'STAGES', {
        1: {'scripts': ['fake.py'], 'outputs': ['fake_official.json']}})
    return repo


def _run_batch(repo, monkeypatch, *extra):
    receipt = repo / 'official_receipt.json'
    assert rob.main(['--stage', '1', '--receipt', str(receipt), *extra]) == 0
    return json.load(open(receipt, encoding='utf-8'))


def _project(repo, receipt):
    out = repo / 'official_batch_status.json'
    receipt_path = repo / 'official_receipt.json'
    _write(receipt_path, receipt)
    _write(out, {})
    assert pub.main(['--receipt', str(receipt_path), '--out', str(out)]) == 0
    return json.load(open(out, encoding='utf-8'))


def _metadata(repo):
    return {
        'build': 'B-PENDING-TEST', 'releasePayloadHash': 'sha256:' + 'a' * 64,
        'datasetHash': _sha(str(repo / 'emsd_空調能源標籤.csv')),
        'datasetSnapshotId': 'emsd-test', 'datasetDate': '2026-10-21',
        'datasetRetrievedAt': RETRIEVED, 'deployTime': '2026-10-21T04:30:00Z',
    }


def _deployment(repo, meta):
    files = ('index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv', 'metadata.json')
    for name in files:
        path = repo / name
        if not path.exists():
            path.write_text(name, encoding='utf-8')
    return {
        'success': True, 'deployedAt': '2026-10-21T04:31:00Z',
        'build': meta['build'], 'releasePayloadHash': meta['releasePayloadHash'],
        'receiptId': 'r1',
        'liveHashes': {name: _sha(str(repo / name)) for name in files},
    }


def _status(repo, official_doc, monkeypatch):
    meta = _metadata(repo)
    _write(repo / 'metadata.json', meta)
    dep = _deployment(repo, meta)
    prices = {'A': {'price': '$1起', 'merchants': 1, 'url': 'https://x',
                    'updated': '2026-10-21'}}
    _write(repo / 'biggo_prices.json', prices)
    monkeypatch.setattr(source_status, 'BASE', str(repo))
    return source_status.build_status(
        metadata=meta,
        emsd_receipt={'success': True, 'aborted': False, 'retrievedAt': RETRIEVED,
                      'datasetHash': meta['datasetHash']},
        prices_meta={'last_run': '2026-10-21', 'appliedAt': '2026-10-21T04:05:00Z',
                     'appliedSnapshotHash': __import__('biggo_canonical').sha256_json(prices)},
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:6/7', 'stage': 6,
                      'snapshotHash': __import__('biggo_canonical').sha256_json(prices),
                      'at': '2026-10-21T04:06:00Z'},
        official_status=official_doc, deployment_receipt=dep,
        now=NOW.timestamp())


def test_positive_real_chain_trusted_pending_is_operations_healthy(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    assert receipt['decision'] == 'queue-kept-pending-coverage'
    assert receipt['coverageObservation']['queue']['missingModels'] == ['M1']
    assert receipt['coverageObservation']['scripts'][0]['marker']['alreadyVerified'] == []
    doc = _project(repo, receipt)
    status = _status(repo, doc, monkeypatch)
    assert status['official']['status'] == 'pending'
    assert status['official']['safeReason'] == 'coverage-pending'
    _write(repo / 'source_status.json', status)
    _write(repo / 'official_batch_status.json', doc)
    _write(repo / 'metadata.json', _metadata(repo))
    dep = _deployment(repo, _metadata(repo))
    _write(repo / 'deployment_receipt.json', dep)
    summary = repo / 'summary.json'
    rc = shm.main(['--status', str(repo / 'source_status.json'), '--repo', str(repo),
                   '--state', str(repo / 'state.json'), '--report', str(repo / 'report.json'),
                   '--summary', str(summary), '--official-status-file',
                   str(repo / 'official_batch_status.json'), '--metadata',
                   str(repo / 'metadata.json'), '--deployment-receipt',
                   str(repo / 'deployment_receipt.json'), '--web-root', str(repo),
                   '--require-deployed', '--now', NOW.strftime('%Y-%m-%dT%H:%M:%SZ')])
    assert rc == 0, json.load(open(repo / 'report.json'))
    summary_doc = json.load(open(summary))
    assert summary_doc['schemaVersion'] == 2
    assert summary_doc['ok'] is True and summary_doc['operations']['ok'] is True
    assert summary_doc['coverage']['status'] == 'pending'
    assert summary_doc['coverage']['expected'] is True
    assert summary_doc['coverage']['missingCount'] == 1
    assert str(tmp_path) not in json.dumps(summary_doc)
    out = repo / 'client.json'
    real_evaluate = client.evaluate
    monkeypatch.setattr(client, 'evaluate', lambda s, **kw: real_evaluate(s, now=NOW, **kw))
    assert client.main(['--file', str(summary), '--metadata', str(repo / 'metadata.json'),
                        '--report', str(out)]) == 0
    report = json.load(open(out))
    assert report['ok'] is True and report['coveragePending'] is True
    # Freshness + deployment + health all good: pending is not an alert.
    fresh = check_freshness.evaluate(_metadata(repo), NOW)
    fresh.update(postdeployOk=True, healthSummaryOk=True,
                 coveragePending=True, coverageMissingCount=1)
    assert check_freshness.plan_issue(None, fresh)['action'] == 'noop'
    # A prior degraded issue closes only with this valid evidence.
    old = {'body': '<!-- aircon-freshness-fingerprint: stale_over_72h -->\nold'}
    assert check_freshness.plan_issue(old, fresh)['action'] == 'close'


def test_observation_tampering_and_hard_failures_are_rejected(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    obs = receipt['coverageObservation']
    assert official_coverage.validate_observation(obs, repo=str(repo)) == []
    # self-consistent rehash cannot hide a contradictory missing set
    bad = copy.deepcopy(obs)
    bad['queue']['missingModels'] = []
    bad['identityHash'] = official_coverage.sha256_id(
        official_coverage.canonical_json_bytes(
            {k: v for k, v in bad.items() if k != 'identityHash'}))
    assert official_coverage.validate_observation(bad, repo=str(repo))
    # actual retained output bytes / queue / EMSD input changes are detected
    (repo / 'fake_official.json').write_text('{"OLD":{"size":"changed"}}', encoding='utf-8')
    assert any('actual output' in e for e in
               official_coverage.validate_observation(obs, repo=str(repo)))
    _fixture(tmp_path, monkeypatch)  # restore output bytes
    (repo / 'update_queue.json').write_text(json.dumps({'stage': 1, 'models': ['M2']}),
                                            encoding='utf-8')
    assert any('actual queue' in e for e in
               official_coverage.validate_observation(obs, repo=str(repo)))
    _fixture(tmp_path, monkeypatch)
    (repo / 'emsd_空調能源標籤.csv').write_text('changed\n', encoding='utf-8')
    assert any('EMSD' in e for e in
               official_coverage.validate_observation(obs, repo=str(repo)))
    _fixture(tmp_path, monkeypatch)


def test_hard_failure_cannot_create_trusted_pending(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    bad = copy.deepcopy(receipt)
    bad['decision'] = 'queue-kept-pending-coverage'
    bad['failures'] = ['network-error']
    with pytest.raises(official_coverage.ObservationError):
        official_coverage.build_observation(
            bad, repo=str(repo), stages=rob.STAGES,
            route_fn=rob.route_for_model, plan_fn=rob.target_scoped_plan)
    # A marker that claims coverage pending while also carrying a hard failure
    # is rejected before it can be projected.
    marker = {'schemaVersion': 1, 'script': 'fake.py', 'attempted': 1,
              'succeeded': 0, 'failed': 1, 'succeededModels': [],
              'failedModels': ['M1'], 'alreadyVerified': [], 'covers': [],
              'failureReasons': {'M1': 'network-error'},
              'coveragePendingModels': ['M1'],
              'coveragePendingReasons': {'M1': 'http-404'}}
    assert official_coverage._marker_errors(marker, 'fake.py', set(), set())


def test_v1_and_v2_summary_semantics_are_not_silently_mixed(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    doc = _project(repo, receipt)
    status = _status(repo, doc, monkeypatch)
    _write(repo / 'metadata.json', _metadata(repo))
    dep = _deployment(repo, _metadata(repo))
    summary = {
        'schemaVersion': 2, 'kind': 'aircon-source-health-summary',
        'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'operations': {'ok': True, 'status': 'healthy', 'failures': [],
                       'coverageExpectedPending': True},
        'coverage': {'status': 'pending', 'pending': True, 'expected': True,
                     'missingCount': 1, 'missingModels': ['M1'],
                     'missingCanonicalModels': ['M1'],
                     'scriptCoveragePending': ['M1'],
                     'observation': doc['coverageObservation'],
                     'observationHash': doc['coverageObservation']['identityHash'],
                     'observedAt': doc['coverageObservation']['observedAt'],
                     'stage': 1, 'targetCount': 1,
                     'queueHash': doc['coverageObservation']['queue']['hash'],
                     'emsdSnapshotHash': doc['coverageObservation']['emsd']['snapshotHash'],
                     'officialSnapshotHash': None, 'officialManifestHash': None},
        'ok': True, 'status': 'healthy', 'failures': [],
        'sourceStatusHash': 'sha256:' + '1' * 64,
        'sourceStatusGeneratedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'releasePayloadHash': 'sha256:' + 'a' * 64,
        'build': 'B-PENDING-TEST', 'deploymentReceipt': 'r1',
        'emsdSnapshotHash': _sha(str(repo / 'emsd_空調能源標籤.csv')),
        'biggoSnapshotHash': 'sha256:' + 'b' * 64,
        'officialSnapshotHash': None, 'officialManifestHash': None,
        'sources': {
            'emsd': {'status': 'ok', 'safeReason': 'acquired',
                     'snapshotHash': _sha(str(repo / 'emsd_空調能源標籤.csv'))},
            'biggo': {'status': 'ok', 'safeReason': 'applied',
                      'snapshotHash': 'sha256:' + 'b' * 64},
            'official': {'status': 'pending', 'safeReason': 'coverage-pending',
                         'snapshotHash': None},
        },
    }
    assert client.evaluate(summary, now=NOW)['ok'] is True
    # A v1 summary with the same pending official source is still unhealthy.
    v1 = {'schemaVersion': 1, 'kind': 'aircon-source-health-summary',
          'checkedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
          'sourceStatusGeneratedAt': NOW.strftime('%Y-%m-%dT%H:%M:%SZ'),
          'ok': True, 'status': 'healthy', 'failures': [],
          'sourceStatusHash': 'sha256:' + '1' * 64,
          'releasePayloadHash': 'sha256:' + 'a' * 64,
          'emsdSnapshotHash': summary['emsdSnapshotHash'],
          'biggoSnapshotHash': summary['biggoSnapshotHash'],
          'officialSnapshotHash': None, 'officialManifestHash': None,
          'build': 'B', 'deploymentReceipt': 'r1',
          'sources': summary['sources']}
    assert client.evaluate(v1, now=NOW)['ok'] is False
    # Outer tampering cannot turn a pending proof green.
    broken = copy.deepcopy(summary)
    broken['sources']['official']['status'] = 'ok'
    assert client.evaluate(broken, now=NOW)['ok'] is False
    broken = copy.deepcopy(summary)
    broken['coverage']['observation']['queue']['missingModels'] = []
    assert client.evaluate(broken, now=NOW)['ok'] is False
    # A true failure alongside pending remains unhealthy and never closes.
    failed = copy.deepcopy(summary)
    failed['failures'] = ['summary:biggo-not-ok']
    failed['operations']['failures'] = ['summary:biggo-not-ok']
    assert client.evaluate(failed, now=NOW)['ok'] is False
    # A stale observation cannot be accepted merely because operations look ok.
    stale = copy.deepcopy(summary)
    stale_ts = (NOW - dt.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    stale['coverage']['observation']['observedAt'] = stale_ts
    stale['coverage']['observedAt'] = stale_ts
    assert client.evaluate(stale, now=NOW)['ok'] is False


def test_future_stale_and_cache_replay_times(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    obs = receipt['coverageObservation']
    future = copy.deepcopy(obs)
    future['observedAt'] = (NOW + dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    future['identityHash'] = official_coverage.sha256_id(
        official_coverage.canonical_json_bytes(
            {k: v for k, v in future.items() if k != 'identityHash'}))
    assert any('未來' in e for e in
               official_coverage.validate_observation(future, repo=str(repo)))
    old = copy.deepcopy(obs)
    old['observedAt'] = (NOW - dt.timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    old['identityHash'] = official_coverage.sha256_id(
        official_coverage.canonical_json_bytes(
            {k: v for k, v in old.items() if k != 'identityHash'}))
    assert any('過期' in e for e in official_coverage.validate_observation(
        old, repo=str(repo), enforce_age=True, max_age_hours=36, now=NOW))
    replay_path = repo / 'old-observation.json'
    _write(replay_path, old)
    replay_receipt = copy.deepcopy(receipt)
    # Re-running the normal batch with the same evidence must preserve the
    # original observation time, not borrow the replay wall clock.
    replay_receipt.pop('coverageObservation')
    new_obs = official_coverage.build_observation(
        replay_receipt, repo=str(repo), previous_status=None,
        replay_observation=old, stages=rob.STAGES,
        route_fn=rob.route_for_model, plan_fn=rob.target_scoped_plan)
    assert new_obs['observedAt'] == old['observedAt']
    assert new_obs['observedAtKind'] == 'cache-replay'
    changed = copy.deepcopy(receipt)
    changed['scripts'][0]['receipt']['coveragePendingReasons'] = {'M1': 'http-410'}
    changed['scripts'][0]['receipt']['failureReasons'] = {'M1': 'http-410'}
    changed.pop('coverageObservation')
    with pytest.raises(official_coverage.ObservationError):
        official_coverage.build_observation(
            changed, repo=str(repo), previous_status=None,
            replay_observation=old, stages=rob.STAGES,
            route_fn=rob.route_for_model, plan_fn=rob.target_scoped_plan)


def test_pending_does_not_hide_stale_or_deploy_mismatch(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    doc = _project(repo, receipt)
    status = _status(repo, doc, monkeypatch)
    _write(repo / 'metadata.json', _metadata(repo))
    dep = _deployment(repo, _metadata(repo))
    stale_meta = _metadata(repo)
    stale_meta['datasetRetrievedAt'] = (NOW - dt.timedelta(days=4)).strftime('%Y-%m-%dT%H:%M:%SZ')
    stale = check_freshness.evaluate(stale_meta, NOW)
    stale.update(postdeployOk=True, healthSummaryOk=True, coveragePending=True)
    assert check_freshness.plan_issue(None, stale)['action'] == 'create'
    mismatch = check_freshness.evaluate(_metadata(repo), NOW,
                                        deployment={'mismatch': True,
                                                    'failures': ['metadata.build']})
    mismatch.update(postdeployOk=False, healthSummaryOk=True, coveragePending=True)
    assert check_freshness.plan_issue(None, mismatch)['action'] == 'create'
    # Monitor deployment mismatch remains unhealthy despite valid pending proof.
    r = shm.evaluate(status=status, repo=str(repo), now=NOW,
                     require_deployed=True, deployment_receipt=dep,
                     metadata=_metadata(repo), web_root=str(repo),
                     official_status_doc=doc)
    assert r['ok'] is True, r['failures']
    bad_dep = copy.deepcopy(dep)
    bad_dep['liveHashes']['index.html'] = 'sha256:' + '0' * 64
    r = shm.evaluate(status=status, repo=str(repo), now=NOW,
                     require_deployed=True, deployment_receipt=bad_dep,
                     metadata=_metadata(repo), web_root=str(repo),
                     official_status_doc=doc)
    assert r['ok'] is False
    assert any('live-hash-mismatch' in f for f in r['failures'])


def test_previous_complete_manifest_is_preserved_and_corruption_fails(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    (repo / 'fake_official.json').write_text(
        json.dumps({'M1': {'size': '100x200x300'}}), encoding='utf-8')
    # A previous complete snapshot (real batch would produce it via the
    # advanced path).  This fixture is only the prior status input.
    body = open(repo / 'fake_official.json', 'rb').read()
    files = {'fake_official.json': {'sha256': 'sha256:' + hashlib.sha256(body).hexdigest(),
                                    'size': len(body)}}
    manifest = {'schemaVersion': 1, 'stage': 1, 'files': files}
    manifest['manifestHash'] = __import__('biggo_canonical').sha256_id(
        __import__('biggo_canonical').canonical_json_bytes(manifest))
    previous = {
        'schemaVersion': 1, 'decision': 'advanced', 'stage': 1,
        'lastSuccessAt': '2026-10-21T03:00:00Z',
        'snapshotId': 'official-1-' + manifest['manifestHash'][-12:],
        'snapshotHash': manifest['manifestHash'], 'outputManifest': manifest,
        'pendingCoverage': False,
    }
    _write(repo / 'official_batch_status.json', previous)
    # Next batch keeps the complete old snapshot while M2 remains pending.
    _write(repo / 'update_queue.json', {'stage': 1, 'models': ['M1', 'M2']})
    (repo / 'fake.py').write_text(
        'import json, os\n'
        'p=os.path.join(os.path.dirname(os.path.abspath(__file__)),"fake_official.json")\n'
        'json.dump({"M1":{"size":"100x200x300"}}, open(p,"w"))\n'
        'print("AIRCON_FETCH_RECEIPT " + json.dumps({"schemaVersion":1,'
        '"script":"fake.py","attempted":0,"succeeded":0,"failed":0,'
        '"succeededModels":[],"failedModels":[],"alreadyVerified":["M1"],'
        '"covers":["M1"]}))\n')
    receipt = _run_batch(repo, monkeypatch)
    obs = receipt['coverageObservation']
    assert obs['previousSnapshot']['snapshotHash'] == previous['snapshotHash']
    assert obs['queue']['missingModels'] == ['M2']
    assert official_coverage.validate_observation(
        obs, repo=str(repo), previous_status=previous) == []
    # A damaged complete snapshot cannot be carried forward as a clean proof.
    original = (repo / 'fake_official.json').read_bytes()
    (repo / 'fake_official.json').write_bytes(original + b' ')
    assert official_coverage.validate_observation(
        obs, repo=str(repo), previous_status=previous)
    (repo / 'fake_official.json').write_bytes(original)


def test_legacy_naked_pending_is_never_healthy(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    legacy = dict(receipt)
    legacy.pop('coverageObservation', None)
    doc = _project(repo, legacy)
    assert doc.get('coverageObservation') is None
    status = _status(repo, doc, monkeypatch)
    assert status['official']['status'] == 'degraded'
    assert status['official']['safeReason'] == 'official-coverage-pending-untrusted'
    r = shm.evaluate(status=status, repo=str(repo), now=NOW,
                     official_status_doc=doc)
    assert r['ok'] is False
    assert any('coverage' in f for f in r['failures'])


def test_corrupt_observation_and_outer_tamper_are_unhealthy(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    receipt = _run_batch(repo, monkeypatch)
    doc = _project(repo, receipt)
    status = _status(repo, doc, monkeypatch)
    # Corrupt the embedded observation identity: source status degrades.
    bad_doc = copy.deepcopy(doc)
    bad_doc['coverageObservation']['identityHash'] = 'sha256:' + '0' * 64
    _write(repo / 'official_batch_status.json', bad_doc)
    monkeypatch.setattr(pub, 'BASE', str(repo))
    bad_status = _status(repo, bad_doc, monkeypatch)
    assert bad_status['official']['status'] == 'degraded'
    r = shm.evaluate(status=bad_status, repo=str(repo), now=NOW,
                     official_status_doc=bad_doc)
    assert r['ok'] is False
    # Outer status tampering cannot green a still-pending observation.
    _write(repo / 'official_batch_status.json', doc)
    tampered = copy.deepcopy(status)
    tampered['official']['status'] = 'ok'
    tampered['official']['safeReason'] = 'applied'
    r = shm.evaluate(status=tampered, repo=str(repo), now=NOW,
                     official_status_doc=doc)
    assert r['ok'] is False
    assert any('coverage' in f for f in r['failures'])
