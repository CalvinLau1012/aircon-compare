"""Root regressions for witnessed result ordering, real merge and Schema."""
import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts'), str(ROOT / 'tests')]
import biggo_acquisition
from biggo_canonical import sha256_json
import extract_governance
import price_demand
import price_status
import source_health_monitor
import test_biggo_acquisition_chain as acquisition_fixture
import test_source_health_monitor as health_fixture

NOW = dt.datetime(2026, 10, 22, 6, tzinfo=dt.timezone.utc)


def demand(first='2026-10-22T05:00:00Z'):
    return price_demand.merge_new_models(
        price_demand.empty(NOW), {'models': [{
            'brand': 'HITACHI', 'model': 'RA-10RF',
            'first_seen': '2026-10-22', 'first_seen_at': first}]}, now=NOW)


def result(acquired='2026-10-22T05:10:00Z'):
    packet, base, new = acquisition_fixture.stage_result(
        acquired=acquired, generated='2026-10-22T05:30:00Z',
        cycle='2026-10-22:1/7', stage=1)
    packet['outcomes'][0]['canonicalKey'] = 'HITACHI|RA10RF'
    manifest = {'cycleId': packet['cycleId'], 'stage': packet['stage']}
    return packet, manifest


def test_packet_clock_cannot_cover_a_later_demand():
    packet, manifest = result('2026-10-22T04:30:00Z')
    # Prove this is a real, internally valid acquisition witness, not a made-up
    # acquiredAt field that an upstream validator would already reject.
    witness = biggo_acquisition.from_stage_result(packet, now=NOW.timestamp())
    assert witness['provenance'] == 'recorded'
    document = demand()
    before = copy.deepcopy(document)
    after = price_demand.record_outcomes(document, packet, manifest, NOW)
    assert after['demands'][0]['status'] == 'pending'
    assert document == before


def test_current_witness_covers_demand_and_repeat_keeps_time():
    packet, manifest = result()
    done = price_demand.record_outcomes(demand(), packet, manifest, NOW)
    assert done['demands'][0]['status'] == 'processed'
    assert done['demands'][0]['processedAt'] == '2026-10-22T05:10:00Z'
    assert price_demand.record_outcomes(
        done, packet, manifest, NOW + dt.timedelta(days=1)) == done


def test_generated_time_without_witness_does_not_clear_new_demand():
    packet, manifest = result()
    packet.pop('acquisition')
    after = price_demand.record_outcomes(demand(), packet, manifest, NOW)
    assert after['demands'][0]['status'] == 'pending'


def test_legacy_date_needs_verified_query_lower_bound():
    document = price_demand.merge_new_models(price_demand.empty(NOW), {
        'models': [{'brand': 'HITACHI', 'model': 'RA-10RF',
                    'first_seen': '2026-10-01'}]}, NOW)
    packet, manifest = result()
    packet.pop('acquisition')
    after = price_demand.record_outcomes(
        document, packet, manifest, NOW, allow_legacy_date=True)
    assert after['demands'][0]['status'] == 'unknown'


def test_merge_persists_incoming_processed_in_the_actual_list():
    current = demand()
    packet, manifest = result()
    incoming = price_demand.record_outcomes(current, packet, manifest, NOW)
    merged = price_demand.merge_documents(current, incoming)
    assert merged['demands'][0]['status'] == 'processed'
    assert merged['demands'][0]['evidence'] == incoming['demands'][0]['evidence']
    assert current['demands'][0]['status'] == 'pending'
    assert price_demand.merge_documents(merged, current)['demands'][0]['status'] == 'processed'


def test_merge_persists_unknown_to_proven_pending():
    current = price_demand.merge_new_models(price_demand.empty(NOW), {
        'models': [{'brand': 'HITACHI', 'model': 'RA-10RF',
                    'first_seen': '2026-10-22'}]}, NOW)
    assert current['demands'][0]['status'] == 'unknown'
    merged = price_demand.merge_documents(current, demand())
    assert merged['demands'][0]['status'] == 'pending'
    assert merged['demands'][0]['firstSeenAt'] == '2026-10-22T05:00:00Z'


def test_actual_price_receipt_satisfies_the_explicit_governance_schema():
    receipt = price_status.build_receipt(
        now=NOW, meta={'price_batch_idx': 0}, demand=price_demand.empty(NOW),
        biggo={'status': 'skip-not-active', 'alert': False},
        completed_sync={}, snapshot_hash='sha256:' + 'a' * 64)
    schema = extract_governance.price_status_schema()
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(receipt)) == []
    assert price_status.validate_receipt(receipt, now=NOW) == receipt
    assert extract_governance.metadata_schema()['$id'] == (
        'urn:aircon-compare:metadata-schema:1.0.0+source-status.1')
    bad = copy.deepcopy(receipt)
    bad['evidence']['unregistered'] = True
    body = {k: v for k, v in bad.items() if k != 'receiptHash'}
    bad['receiptHash'] = sha256_json(body)
    with pytest.raises(price_status.PriceStatusError):
        price_status.validate_receipt(bad, now=NOW)


def test_apply_evidence_failure_still_produces_valid_unhealthy_receipt():
    receipt = price_status.build_receipt(
        now=NOW, meta={'price_batch_idx': 7, 'last_price_month': '2026-10',
                       'last_applied_cycle': '2026-10-10:7/7',
                       'appliedBundleHash': 'sha256:' + 'b' * 64,
                       'appliedSnapshotHash': 'sha256:' + 'a' * 64},
        demand=price_demand.empty(NOW), biggo={}, completed_sync={},
        snapshot_hash='sha256:' + 'a' * 64,
        post_state={'trackingHash': 'sha256:' + 'c' * 64,
                    'blacklistHash': 'sha256:' + 'd' * 64})
    assert receipt['status'] == 'blocked'
    assert receipt['safeReason'] == 'apply-evidence-missing'
    assert price_status.validate_receipt(receipt, now=NOW) == receipt


def test_enabled_price_monitor_cannot_fall_back_when_both_sidecars_vanish(
        tmp_path, monkeypatch):
    health_fixture._repo(tmp_path)
    status = health_fixture._status(tmp_path)
    status_path = tmp_path / 'source_status.json'
    status_path.write_text(json.dumps(status))
    official = tmp_path / 'official_batch_status.json'
    official.write_text(json.dumps(health_fixture._official(tmp_path)))
    monkeypatch.setenv('AIRCON_PRICE_QUERY_MODE', 'receive-only')
    code = source_health_monitor.main([
        '--repo', str(tmp_path), '--status', str(status_path),
        '--official-status-file', str(official), '--state', str(tmp_path / 'state.json'),
        '--report', str(tmp_path / 'report.json'), '--summary', str(tmp_path / 'summary.json'),
        '--now', '2026-10-17T04:00:00Z'])
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert code == 1
    assert summary['schemaVersion'] == 3
    assert summary['ok'] is False
    assert summary['priceHealthy'] is False


def test_unknown_migration_disposition_never_turns_into_price_demand(tmp_path):
    from test_price_cycle_migrate_20261022 import _fixture, _plan
    import price_demand
    repo = _fixture(tmp_path)
    path = repo / 'new_models.json'
    doc = json.loads(path.read_text())
    doc['models'].append({'brand': 'GREE', 'model': 'OLDUNKNOWN',
                          'first_seen': '2026-09-01'})
    path.write_text(json.dumps(doc))
    blocked = _plan(repo)
    assert blocked['completedBaselineReady'] and not blocked['enableReady']
    ready = _plan(repo, quarantine_unknown=True)
    assert ready['ready'] and ready['enableReady']
    assert ready['demand'] == blocked['demand']
    unknown = price_demand.unknown(ready['demand'])
    assert len(unknown) == 1 and unknown[0]['key'] == 'GREE|OLDUNKNOWN'
    assert ready['unknownDisposition']['policy'] == 'retain-unknown-ineligible'
    assert ready['unknownDisposition']['demands'][0]['key'] == 'GREE|OLDUNKNOWN'
    assert price_demand.pending(ready['demand']) == []
    new = price_demand.merge_new_models(ready['demand'], {'models': [
        {'brand': 'GREE', 'model': 'ACTUALNEW', 'first_seen': '2026-11-01',
         'first_seen_at': '2026-11-01T00:00:00Z'}]}, now=dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc))
    assert price_demand.pending_keys(new) == {'GREE|ACTUALNEW'}
    assert price_demand.unknown(new) == unknown
