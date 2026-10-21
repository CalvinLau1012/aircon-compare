"""Independent regression cases found by Root while reviewing the pending policy."""
import copy
import datetime as dt
import json

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import official_coverage as coverage
import check_health_summary as health_client
import run_official_batch as batch
from test_official_pending_policy_20261021 import _fixture, _run_batch, _project


def _rehash(value):
    value['identityHash'] = coverage.sha256_id(coverage.canonical_json_bytes(
        {k: v for k, v in value.items() if k != 'identityHash'}))
    return value


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(stage=3),
    lambda d: d.update(queue=['malformed']),
    lambda d: d.update(scripts=[None]),
    lambda d: d['queue'].update(reasons=['bad']),
    lambda d: d.update(previousSnapshot='bad'),
])
def test_corrupt_observation_returns_failure_without_escaping(tmp_path, monkeypatch, mutation):
    repo = _fixture(tmp_path, monkeypatch)
    observation = copy.deepcopy(_run_batch(repo, monkeypatch)['coverageObservation'])
    mutation(observation)
    _rehash(observation)
    assert coverage.validate_observation(observation, repo=str(repo))
    document = {'decision': coverage.DECISION, 'coverageObservation': observation}
    decision = coverage.expected_pending(document, repo=str(repo))
    assert decision['trusted'] is False


def _catalogue_marker(stamp):
    return {'schemaVersion': 1, 'script': 'fetch_gilman.py',
            'attempted': 0, 'succeeded': 0, 'failed': 0, 'skipped': 0,
            'succeededModels': [], 'failedModels': [], 'alreadyVerified': [],
            'covers': [], 'failureReasons': {}, 'catalogPages': 9,
            'cataloguePagesAttempted': 9, 'cataloguePagesFailed': 0,
            'catalogueFailureReasons': {}, 'pageCounts': {'GREE': 7, 'MITSUBISHIHEAVY': 2},
            'cataloguePagesFromCache': 0, 'catalogueObservedAt': stamp}


def test_catalogue_transport_failure_is_not_clean_missing_data():
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    marker = _catalogue_marker(stamp)
    assert coverage._marker_errors(marker, 'fetch_gilman.py', set(), set()) == []
    for mutation in [
        lambda m: m.update(cataloguePagesFailed=1, catalogueFailureReasons={'tls-error': 1}),
        lambda m: m.update(cataloguePagesAttempted=8),
        lambda m: m.update(catalogueObservedAt=None),
        lambda m: m.update(cataloguePagesFromCache=10),
    ]:
        bad = copy.deepcopy(marker)
        mutation(bad)
        assert coverage._marker_errors(bad, 'fetch_gilman.py', set(), set())


def test_two_pending_rounds_preserve_a_previous_complete_snapshot(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    first = _run_batch(repo, monkeypatch)
    manifest = batch._output_manifest(str(repo), 1, ['fake_official.json'])
    previous = {'decision': 'advanced', 'snapshotHash': manifest['manifestHash'],
                'snapshotId': 'official-1-retained',
                'lastSuccessAt': '2026-10-20T00:00:00Z', 'outputManifest': manifest}
    first['coverageObservation'] = coverage.build_observation(
        first, repo=str(repo), staging_repo=str(repo), previous_status=previous)
    (repo / 'official_batch_status.json').write_text(json.dumps(previous))
    # _project clears the output intentionally, so first project without reusing
    # the helper; this exercises the real publisher's prior-state validation.
    import publish_official_status as publisher
    receipt_path = repo / 'official_receipt.json'
    receipt_path.write_text(json.dumps(first))
    assert publisher.main(['--receipt', str(receipt_path), '--out',
                           str(repo / 'official_batch_status.json')]) == 0
    before = json.loads((repo / 'official_batch_status.json').read_text())
    second = _run_batch(repo, monkeypatch)
    assert second['coverageObservation']['previousSnapshot']['snapshotHash'] == manifest['manifestHash']
    assert publisher.main(['--receipt', str(receipt_path), '--out',
                           str(repo / 'official_batch_status.json')]) == 0
    after = json.loads((repo / 'official_batch_status.json').read_text())
    assert after['snapshotHash'] == before['snapshotHash']
    assert after['lastSuccessAt'] == previous['lastSuccessAt']
    assert after['missingModels'] == ['M1']


def test_ordinary_cached_catalogue_cannot_refresh_observation_clock(tmp_path, monkeypatch):
    repo = _fixture(tmp_path, monkeypatch)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=40)).strftime('%Y-%m-%dT%H:%M:%SZ')
    marker = _catalogue_marker(old)
    marker['cataloguePagesFromCache'] = 9
    script = repo / 'fetch_gilman.py'
    script.write_text('import json\nprint("AIRCON_FETCH_RECEIPT " + ' + repr(json.dumps(marker)) + ')\n')
    monkeypatch.setattr(batch, 'STAGES', {1: {'scripts': ['fetch_gilman.py'],
                                             'outputs': ['fake_official.json']}})
    receipt = _run_batch(repo, monkeypatch)
    observation = receipt['coverageObservation']
    assert observation['observedAt'] == old
    assert observation['observedAtKind'] == 'cache-replay'
    assert coverage.validate_observation(observation, repo=str(repo), enforce_age=True)


def test_malformed_v2_client_payload_is_unhealthy():
    report = health_client.evaluate({'kind': 'aircon-source-health-summary',
        'schemaVersion': 2, 'sources': {'emsd': ['bad'], 'biggo': {}, 'official': {}},
        'coverage': {'status': 'pending', 'observation': {'queue': ['bad']}}})
    assert report['ok'] is False
    assert report['status'] == 'unhealthy'
