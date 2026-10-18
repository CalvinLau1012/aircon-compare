# -*- coding: utf-8 -*-
"""Read-only source health: binding, missing/corrupt receipt, durable dedup/recovery."""
import importlib.util
import json
import os
import sys
import datetime as dt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
spec = importlib.util.spec_from_file_location(
    'source_health_monitor', os.path.join(BASE, 'scripts', 'source_health_monitor.py'))
shm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shm)
import source_status  # noqa: E402
from biggo_canonical import sha256_json, canonical_json_bytes, sha256_id  # noqa: E402


def _official(repo):
    files = {}
    names = ('official_specs.json', 'shew_official.json', 'rasonic_official.json')
    for name in names:
        body = json.dumps({'M1': {'size': '100x200x300'}}, ensure_ascii=False).encode()
        (repo / name).write_bytes(body)
        files[name] = {'sha256': 'sha256:' + __import__('hashlib').sha256(body).hexdigest(),
                       'size': len(body)}
    manifest = {'schemaVersion': 1, 'stage': 1, 'files': files}
    manifest['manifestHash'] = sha256_id(canonical_json_bytes(manifest))
    return {'schemaVersion': 1, 'decision': 'advanced', 'stage': 1,
            'generatedAt': '2026-10-17T03:00:00Z',
            'lastSuccessAt': '2026-10-17T03:00:00Z',
            'snapshotId': 'official-1', 'snapshotHash': manifest['manifestHash'],
            'outputManifest': manifest}


def _repo(tmp_path):
    (tmp_path / 'emsd_空調能源標籤.csv').write_text('a,b\n1,2\n', encoding='utf-8')
    prices = {'A': {'price': '$1起', 'merchants': 1, 'url': 'https://x',
                    'updated': '2026-10-12'}}
    (tmp_path / 'biggo_prices.json').write_text(json.dumps(prices), encoding='utf-8')
    return prices


def _deployment(repo):
    import hashlib
    files = ['index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv', 'metadata.json']
    for name in files:
        if not (repo / name).exists():
            (repo / name).write_text(name, encoding='utf-8')
    return {'success': True, 'deployedAt': '2026-10-17T03:30:00Z',
            'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
            'receiptId': 'r1',
            'liveHashes': {name: 'sha256:' + hashlib.sha256(
                (repo / name).read_bytes()).hexdigest() for name in files}}


def _status(repo):
    prices = json.load(open(repo / 'biggo_prices.json'))
    dep = _deployment(repo)
    official = _official(repo)
    return source_status.build_status(
        metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
                  'datasetDate': '2026-10-17',
                  'datasetHash': shm._sha256_file(str(repo / 'emsd_空調能源標籤.csv')),
                  'deployTime': '2026-10-16T16:32:54Z'},
        emsd_receipt={'success': True, 'aborted': False,
                      'retrievedAt': '2026-10-16T16:31:12Z',
                      'datasetHash': shm._sha256_file(str(repo / 'emsd_空調能源標籤.csv'))},
        prices_meta={'last_run': '2026-10-12',
                     'appliedAt': '2026-10-16T16:00:00Z',
                     'appliedSnapshotHash': sha256_json(prices)},
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': sha256_json(prices)},
        official_status=official,
        deployment_receipt=dep,
        now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc).timestamp())


def test_health_binds_hashes_and_dedups_recovery(tmp_path):
    _repo(tmp_path)
    status = _status(tmp_path)
    now = dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc)
    r = shm.evaluate(status=status, repo=str(tmp_path), now=now,
                     require_deployed=True, deployment_receipt=_deployment(tmp_path),
                     metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64},
                     web_root=str(tmp_path), official_status_doc=_official(tmp_path))
    assert r['ok'] is True
    state, action = shm.plan_dedup({}, r, now)
    assert action == 'noop'
    bad = dict(status)
    bad['emsd'] = dict(status['emsd'], snapshotHash='sha256:' + '0' * 64)
    rb = shm.evaluate(status=bad, repo=str(tmp_path), now=now)
    assert rb['ok'] is False
    state, action = shm.plan_dedup({}, rb, now)
    assert action == 'alert' and state['active'] is True
    state2, action2 = shm.plan_dedup(state, rb, now + dt.timedelta(hours=6))
    assert action2 == 'noop', 'same fingerprint 必須 dedup'
    good = shm.evaluate(status=status, repo=str(tmp_path), now=now,
                        official_status_doc=_official(tmp_path))
    state3, action3 = shm.plan_dedup(state2, good, now + dt.timedelta(hours=7))
    assert action3 == 'recovered' and state3['active'] is False


def test_missing_or_corrupt_receipt_is_alert(tmp_path):
    (tmp_path / 'source_status.json').write_text('not-json', encoding='utf-8')
    rc = shm.main(['--status', str(tmp_path / 'source_status.json'), '--repo', str(tmp_path),
                   '--state', str(tmp_path / 'state.json'),
                   '--report', str(tmp_path / 'report.json'),
                   '--summary', str(tmp_path / 'summary.json')])
    assert rc == 1
    report = json.load(open(tmp_path / 'report.json'))
    assert report['action'] == 'alert'
    assert report['notifications']['sent'] is False


def test_receipt_age_is_separate_from_publish_delay(tmp_path):
    _repo(tmp_path)
    status = _status(tmp_path)
    now = dt.datetime(2026, 10, 20, 4, 0, tzinfo=dt.timezone.utc)
    r = shm.evaluate(status=status, repo=str(tmp_path), now=now, max_age_hours=36,
                     require_deployed=True, deployment_receipt=_deployment(tmp_path),
                     metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64},
                     web_root=str(tmp_path))
    assert r['ok'] is False
    assert any('age>36h' in f for f in r['failures'])
    assert r['delayMinutes'] is not None
    assert r['schedule']['delayIsInformationalOnly'] is True


def test_ok_source_without_full_snapshot_hash_is_unhealthy(tmp_path):
    _repo(tmp_path)
    status = _status(tmp_path)
    status['biggo']['snapshotHash'] = None
    r = shm.evaluate(status=status, repo=str(tmp_path),
                     now=dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc))
    assert r['ok'] is False
    assert any('biggo:ok-without-full-snapshotHash' in f for f in r['failures'])


def test_corrupt_price_pending_unknown_and_generated_times_fail(tmp_path):
    _repo(tmp_path)
    status = _status(tmp_path)
    now = dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc)
    # corrupt price bytes must be an explicit failure, not live=None success
    (tmp_path / 'biggo_prices.json').write_text('{not-json', encoding='utf-8')
    r = shm.evaluate(status=status, repo=str(tmp_path), now=now)
    assert r['ok'] is False
    assert any('biggo-snapshot-corrupt' in f for f in r['failures'])
    # restore and exercise pending / unknown-with-hash
    _repo(tmp_path)
    pending = json.loads(json.dumps(status)); pending['emsd']['status'] = 'pending'
    pending['emsd']['snapshotHash'] = None
    rp = shm.evaluate(status=pending, repo=str(tmp_path), now=now)
    assert rp['ok'] is False and any('emsd:pending' in f for f in rp['failures'])
    unknown = json.loads(json.dumps(status)); unknown['biggo']['status'] = 'unknown'
    ru = shm.evaluate(status=unknown, repo=str(tmp_path), now=now)
    assert ru['ok'] is False and any('biggo:unknown' in f for f in ru['failures'])
    # generatedAt missing/stale/future
    for value in (None, '2026-10-01T00:00:00Z', '2026-10-18T00:00:00Z', 'bad'):
        bad = json.loads(json.dumps(status)); bad['generatedAt'] = value
        assert shm.evaluate(status=bad, repo=str(tmp_path), now=now)['ok'] is False
    future = json.loads(json.dumps(status))
    future['official']['lastAcquiredAt'] = '2026-10-17T05:00:00Z'
    assert shm.evaluate(status=future, repo=str(tmp_path), now=now)['ok'] is False


def test_official_manifest_binding_and_corrupt_aux_reports(tmp_path):
    _repo(tmp_path)
    status = _status(tmp_path)
    now = dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc)
    bad_manifest = {'decision': 'advanced', 'snapshotHash': 'sha256:' + 'e' * 64}
    r = shm.evaluate(status=status, repo=str(tmp_path), now=now,
                     official_status_doc=bad_manifest)
    assert r['ok'] is False
    assert any('official:manifest-missing' in f for f in r['failures'])
    # corrupt deployment receipt / metadata must still produce an unhealthy report
    bad = tmp_path / 'bad.json'; bad.write_text('not-json', encoding='utf-8')
    status_path = tmp_path / 'source_status.json'
    status_path.write_text(json.dumps(status), encoding='utf-8')
    rc = shm.main(['--status', str(status_path), '--repo', str(tmp_path),
                   '--state', str(tmp_path / 'state.json'),
                   '--report', str(tmp_path / 'report.json'),
                   '--summary', str(tmp_path / 'summary.json'),
                   '--require-deployed', '--deployment-receipt', str(bad),
                   '--metadata', str(bad), '--web-root', str(tmp_path)])
    assert rc == 1
    report = json.load(open(tmp_path / 'report.json'))
    assert report['check']['ok'] is False
    assert any('receipt-aux' in f for f in report['check']['failures'])


def _full_repo(tmp_path):
    _repo(tmp_path)
    meta = {'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
            'datasetHash': shm._sha256_file(str(tmp_path / 'emsd_空調能源標籤.csv')),
            'datasetSnapshotId': 'emsd-test', 'datasetDate': '2026-10-17',
            'deployTime': '2026-10-16T16:32:54Z'}
    (tmp_path / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
    dep = _deployment(tmp_path)
    status = _status(tmp_path)
    official = _official(tmp_path)
    (tmp_path / 'source_status.json').write_text(json.dumps(status), encoding='utf-8')
    (tmp_path / 'deployment_receipt.json').write_text(json.dumps(dep), encoding='utf-8')
    (tmp_path / 'official_batch_status.json').write_text(json.dumps(official), encoding='utf-8')
    return tmp_path, status, dep, meta, official


def test_summary_to_public_consumer_chain_and_tamper(tmp_path):
    import importlib.util
    repo, status, dep, meta, official = _full_repo(tmp_path)
    summary = repo / 'source_health_summary.json'; report = repo / 'source_health_report.json'
    args = ['--status', str(repo / 'source_status.json'), '--repo', str(repo),
            '--state', str(repo / 'state.json'), '--report', str(report),
            '--summary', str(summary), '--require-deployed',
            '--deployment-receipt', str(repo / 'deployment_receipt.json'),
            '--metadata', str(repo / 'metadata.json'), '--web-root', str(repo),
            '--official-status-file', str(repo / 'official_batch_status.json'),
            '--now', '2026-10-17T04:00:00Z']
    assert shm.main(args) == 0
    summary_doc = json.load(open(summary))
    assert summary_doc['ok'] is True and summary_doc['status'] == 'healthy'
    assert str(tmp_path) not in json.dumps(summary_doc)
    spec = importlib.util.spec_from_file_location(
        'health_client_chain', os.path.join(BASE, 'scripts', 'check_health_summary.py'))
    client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
    out = repo / 'client-report.json'
    assert client.main(['--file', str(summary), '--metadata', str(repo / 'metadata.json'),
                        '--report', str(out)]) == 0
    # Tamper actual price bytes; private monitor must rewrite unhealthy summary.
    (repo / 'biggo_prices.json').write_text('{not-json', encoding='utf-8')
    assert shm.main(args) == 1
    summary_doc = json.load(open(summary))
    assert summary_doc['ok'] is False
    assert client.main(['--file', str(summary), '--metadata', str(repo / 'metadata.json'),
                        '--report', str(out)]) == 1
    # Tamper official output bytes, then missing manifest.
    (repo / 'biggo_prices.json').write_text(json.dumps({'A': {'price': '$1起', 'merchants': 1,
                                                               'url': 'https://x',
                                                               'updated': '2026-10-12'}}),
                                            encoding='utf-8')
    (repo / 'official_specs.json').write_text('{not-json', encoding='utf-8')
    assert shm.main(args) == 1
    assert json.load(open(summary))['ok'] is False
    broken = dict(official); broken.pop('outputManifest')
    (repo / 'official_batch_status.json').write_text(json.dumps(broken), encoding='utf-8')
    assert shm.main(args) == 1
    assert json.load(open(summary))['ok'] is False
    # Corrupt deployment receipt still writes a fresh unhealthy report/summary.
    (repo / 'deployment_receipt.json').write_text('not-json', encoding='utf-8')
    assert shm.main(args) == 1
    assert json.load(open(report))['check']['ok'] is False
    assert json.load(open(summary))['ok'] is False


def test_missing_status_still_writes_unhealthy_summary(tmp_path):
    repo = tmp_path
    rc = shm.main(['--status', str(repo / 'missing.json'), '--repo', str(repo),
                   '--state', str(repo / 'state.json'), '--report', str(repo / 'report.json'),
                   '--summary', str(repo / 'summary.json')])
    assert rc == 1
    s = json.load(open(repo / 'summary.json'))
    assert s['ok'] is False and s['status'] == 'unhealthy'
    assert any('receipt' in f for f in s['failures'])


def test_real_runner_publisher_source_status_monitor_summary_chain(tmp_path, monkeypatch):
    import importlib.util
    import pytest
    rob_spec = importlib.util.spec_from_file_location(
        'rob_v4_chain', os.path.join(BASE, 'scripts', 'run_official_batch.py'))
    rob = importlib.util.module_from_spec(rob_spec); rob_spec.loader.exec_module(rob)
    pub = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
        'pub_v4_chain', os.path.join(BASE, 'scripts', 'publish_official_status.py')))
    pub.__spec__.loader.exec_module(pub)
    names = ('official_specs.json', 'shew_official.json', 'rasonic_official.json')
    (tmp_path / 'update_queue.json').write_text(json.dumps({'stage': 1, 'models': ['M1']}))
    (tmp_path / 'advance_queue.py').write_text('import sys; sys.exit(0)\n')
    scripts = []
    for i, name in enumerate(names):
        script = f'fake_{i}.py'
        (tmp_path / script).write_text(
            'import json, os\n'
            f'p=os.path.join(os.path.dirname(os.path.abspath(__file__)), {name!r})\n'
            'json.dump({"M1":{"size":"100x200x300"}}, open(p,"w"))\n'
            'print("AIRCON_FETCH_RECEIPT " + json.dumps({"schemaVersion":1,'
            f'"script":{script!r},"attempted":1,"succeeded":1,"failed":0,'
            '"succeededModels":["M1"],"failedModels":[],"alreadyVerified":[],'
            '"covers":["M1"]}))\n')
        scripts.append(script)
    monkeypatch.setattr(rob, 'BASE', str(tmp_path))
    monkeypatch.setattr(rob, 'STAGES', {1: {'scripts': scripts, 'outputs': list(names)}})
    receipt_path = tmp_path / 'official_receipt.json'
    assert rob.main(['--stage', '1', '--receipt', str(receipt_path)]) == 0
    official_status = tmp_path / 'official_batch_status.json'
    assert pub.main(['--receipt', str(receipt_path), '--out', str(official_status)]) == 0
    official_doc = json.load(open(official_status))
    assert official_doc['outputManifest']['manifestHash'] == official_doc['snapshotHash']
    _repo(tmp_path)
    current = dt.datetime.now(dt.timezone.utc)
    retrieved = (current - dt.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    meta = {'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
            'datasetHash': shm._sha256_file(str(tmp_path / 'emsd_空調能源標籤.csv')),
            'datasetSnapshotId': 'emsd-test', 'datasetDate': current.date().isoformat(),
            'datasetRetrievedAt': retrieved,
            'deployTime': '2026-10-16T16:32:54Z'}
    (tmp_path / 'metadata.json').write_text(json.dumps(meta))
    dep = _deployment(tmp_path)
    # Keep the fixture's deployment age relative to the real clock; the
    # monitor intentionally enforces a 36h deployed-data age.
    dep['deployedAt'] = retrieved
    status = source_status.build_status(
        metadata=meta,
        emsd_receipt={'success': True, 'aborted': False,
                      'retrievedAt': retrieved,
                      'datasetHash': meta['datasetHash']},
        prices_meta={'appliedAt': retrieved,
                     'appliedSnapshotHash': sha256_json(json.load(open(tmp_path / 'biggo_prices.json')))},
        biggo_status={'status': 'completed', 'cycle': '2026-10-10:3/7', 'stage': 3,
                      'snapshotHash': sha256_json(json.load(open(tmp_path / 'biggo_prices.json')))},
        official_status=official_doc, deployment_receipt=dep,
        now=dt.datetime.now(dt.timezone.utc).timestamp())
    assert status['official']['status'] == 'ok'
    (tmp_path / 'source_status.json').write_text(json.dumps(status))
    (tmp_path / 'deployment_receipt.json').write_text(json.dumps(dep))
    (tmp_path / 'official_batch_status.json').write_text(json.dumps(official_doc))
    rc = shm.main(['--status', str(tmp_path / 'source_status.json'), '--repo', str(tmp_path),
                   '--state', str(tmp_path / 'state.json'), '--report', str(tmp_path / 'report.json'),
                   '--summary', str(tmp_path / 'summary.json'), '--require-deployed',
                   '--deployment-receipt', str(tmp_path / 'deployment_receipt.json'),
                   '--metadata', str(tmp_path / 'metadata.json'), '--web-root', str(tmp_path),
                   '--official-status-file', str(tmp_path / 'official_batch_status.json')])
    assert rc == 0, json.load(open(tmp_path / 'report.json'))
    assert json.load(open(tmp_path / 'summary.json'))['ok'] is True


def test_official_manifest_actual_byte_negative_matrix(tmp_path):
    import copy
    import hashlib
    repo = tmp_path
    _repo(repo)
    base_status = _status(repo)
    base_doc = _official(repo)
    now = dt.datetime(2026, 10, 17, 4, 0, tzinfo=dt.timezone.utc)
    assert shm.evaluate(status=base_status, repo=str(repo), now=now,
                        official_status_doc=base_doc)['ok'] is True
    cases = []

    def run(doc):
        st = copy.deepcopy(base_status)
        st['official']['snapshotHash'] = doc.get('snapshotHash')
        return shm.evaluate(status=st, repo=str(repo), now=now, official_status_doc=doc)

    d = copy.deepcopy(base_doc); (repo / 'shew_official.json').unlink() if (repo / 'shew_official.json').exists() else None
    cases.append(('missing-file', run(d), 'actual-missing'))
    _official(repo)
    d = copy.deepcopy(base_doc); (repo / 'official_specs.json').write_text('{}', encoding='utf-8')
    cases.append(('tampered-file', run(d), 'actual-size-mismatch'))
    _official(repo)
    d = copy.deepcopy(base_doc); d['outputManifest']['manifestHash'] = 'sha256:' + '0' * 64
    cases.append(('wrong-manifest-hash', run(d), 'manifest-hash-mismatch'))
    _official(repo)
    d = copy.deepcopy(base_doc); d['outputManifest']['files']['official_specs.json']['sha256'] = 'sha256:' + 'a' * 8
    cases.append(('short-hash', run(d), 'short-or-missing-hash'))
    _official(repo)
    d = copy.deepcopy(base_doc); d['outputManifest']['stage'] = 2; d['stage'] = 2
    cases.append(('wrong-stage', run(d), 'manifest-output-set-mismatch'))
    _official(repo)
    d = copy.deepcopy(base_doc); d['stage'] = 1; d['outputManifest']['stage'] = 1
    d['stage'] = 2
    cases.append(('status-stage-mismatch', run(d), 'status-stage-mismatch'))
    _official(repo)
    raw = b'{not-json'
    (repo / 'rasonic_official.json').write_bytes(raw)
    d = copy.deepcopy(base_doc)
    d['outputManifest']['files']['rasonic_official.json'] = {
        'sha256': 'sha256:' + hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    body = {'schemaVersion': 1, 'stage': 1, 'files': d['outputManifest']['files']}
    d['outputManifest']['manifestHash'] = sha256_id(canonical_json_bytes(body))
    d['snapshotHash'] = d['outputManifest']['manifestHash']
    cases.append(('invalid-json', run(d), 'actual-json-invalid'))
    _official(repo)
    d = copy.deepcopy(base_doc); d.pop('outputManifest')
    cases.append(('missing-manifest', run(d), 'manifest-missing'))
    for name, result, needle in cases:
        assert result['ok'] is False, (name, result)
        assert any(needle in e for e in result['failures']), (name, result['failures'])
