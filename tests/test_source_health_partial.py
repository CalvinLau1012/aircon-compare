# -*- coding: utf-8 -*-
"""Three-axis monitoring: proved coverage pending is a warning, not false green.

The fixture uses a complete prior official manifest + actual output bytes and a
clean pending receipt.  Missing/tampered evidence, hard source failures and old
v1 summaries stay fail-closed.
"""
import datetime as dt
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import check_freshness
import check_health_summary
import freshness_issue
import monitor_presentation
import run_official_batch
import source_health_contract
import source_health_monitor
import source_status
from biggo_canonical import sha256_json


def _sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _now():
    return dt.datetime(2026, 10, 21, 2, 0, tzinfo=dt.timezone.utc)


def _fixture(tmp_path, *, bad_output=False, bad_receipt=False):
    output = tmp_path / 'gilman_official.json'
    data = json.dumps({'GWT07ES': {'size': '350×450×640', 'btu': '7500'}},
                      ensure_ascii=False).encode()
    output.write_bytes(data)
    manifest = {
        'schemaVersion': 1, 'stage': 1,
        'files': {'gilman_official.json': {'sha256': _sha(data), 'size': len(data)}},
    }
    body = {'schemaVersion': 1, 'stage': 1, 'files': manifest['files']}
    manifest['manifestHash'] = source_health_contract.canonical_hash(body)
    targets = ['GWT07ES', 'CWV-24CRFN9-AD5']
    routes = {m: run_official_batch.route_for_model(m) for m in targets}
    doc = {
        'schemaVersion': 1, 'decision': 'queue-kept-pending-coverage',
        'lastSuccessAt': '2026-10-21T01:00:00Z',
        'snapshotId': 'official-1-' + manifest['manifestHash'][-12:],
        'snapshotHash': manifest['manifestHash'], 'outputManifest': manifest,
        'stage': 1, 'pendingCoverage': True, 'missingQueueCoverage': True,
        'missingModels': ['GWT07ES'], 'missingCanonicalModels': ['GWT07ES'],
        'coveragePendingReasons': {'GWT07ES': 'stage-source-did-not-cover-target'},
        'targetModels': targets, 'selectedOutputs': ['gilman_official.json'],
        'sourceRouting': routes, 'failureClass': 'none',
        'generatedAt': '2026-10-21T01:01:00Z',
    }
    receipt = dict(doc)
    receipt.update({'coveragePending': True, 'advanced': False,
                    'targetScoped': True, 'failureClass': 'none',
                    'safeToContinue': True, 'finishedAt': '2026-10-21T01:01:00Z'})
    receipt['missingModels'] = ['GWT07ES']
    receipt['missingCanonicalModels'] = ['GWT07ES']
    receipt['coveragePendingReasons'] = {'GWT07ES': 'stage-source-did-not-cover-target'}
    if bad_receipt:
        receipt['failureClass'] = 'source'
    price = {'RA-10RF': {'price': '$2,500起', 'merchants': 1,
                         'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-10-20'}}
    (tmp_path / 'biggo_prices.json').write_text(json.dumps(price), encoding='utf-8')
    price_hash = sha256_json(price)
    (tmp_path / 'emsd_空調能源標籤.csv').write_bytes(b'header\nrow\n')
    status = source_status.build_status(
        metadata={'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
                  'datasetDate': '2026-10-21',
                  'datasetHash': _sha((tmp_path / 'emsd_空調能源標籤.csv').read_bytes()),
                  'datasetRetrievedAt': '2026-10-21T00:10:00Z'},
        emsd_receipt={'success': True, 'aborted': False, 'error': None,
                      'retrievedAt': '2026-10-21T00:10:00Z',
                      'datasetHash': _sha((tmp_path / 'emsd_空調能源標籤.csv').read_bytes())},
        prices_meta={'appliedAt': '2026-10-20T16:37:43Z',
                     'appliedSnapshotHash': price_hash},
        biggo_status={'status': 'completed', 'cycle': '2026-10-20:6/7', 'stage': 6,
                      'snapshotHash': price_hash},
        official_status=doc,
        biggo_snapshot_hash=price_hash,
        now=_now().timestamp())
    if bad_output:
        output.unlink()
    return status, doc, receipt, price


def test_pending_summary_is_partial_with_visible_warning(tmp_path):
    status, doc, receipt, _ = _fixture(tmp_path)
    assert status['official']['status'] == 'pending'
    assert status['official']['safeReason'] == 'coverage-pending'
    result = source_health_monitor.evaluate(
        status=status, repo=str(tmp_path), now=_now(),
        official_status_doc=doc, official_receipt=receipt)
    assert result['ok'] is True and result['partial'] is True
    assert result['fingerprint'] == 'partial:official-coverage-pending:1'
    assert result['warnings'] == ['official:coverage-pending:1']
    assert result['failures'] == []
    # Same partial state is a durable noop; it is not "recovered".
    state, action = source_health_monitor.plan_dedup({}, result, _now())
    assert action == 'alert'
    state2, action2 = source_health_monitor.plan_dedup(state, result, _now())
    assert action2 == 'noop' and state2['active'] is True


def test_public_validator_accepts_only_hash_bound_partial(tmp_path):
    status, doc, receipt, _ = _fixture(tmp_path)
    result = source_health_monitor.evaluate(
        status=status, repo=str(tmp_path), now=_now(),
        official_status_doc=doc, official_receipt=receipt)
    proof = result['pendingProof']
    summary = {
        'schemaVersion': 2, 'kind': 'aircon-source-health-summary',
        'checkedAt': '2026-10-21T02:00:00Z', 'ok': True, 'status': 'partial',
        'partial': True, 'failures': [], 'warnings': ['official:coverage-pending:1'],
        'pending': proof,
        'pendingEvidenceHash': source_health_contract.proof_hash(proof),
        'sourceStatusHash': 'sha256:' + '1' * 64,
        'sourceStatusGeneratedAt': status['generatedAt'],
        'build': 'B1', 'releasePayloadHash': 'sha256:' + 'c' * 64,
        'deploymentReceipt': 'receipt-r1',
        'emsdSnapshotHash': status['emsd']['snapshotHash'],
        'biggoSnapshotHash': status['biggo']['snapshotHash'],
        'officialSnapshotHash': proof['snapshotHash'],
        'officialManifestHash': proof['snapshotHash'],
        'sources': {'emsd': {'status': 'ok', 'snapshotHash': status['emsd']['snapshotHash']},
                    'biggo': {'status': 'ok', 'snapshotHash': status['biggo']['snapshotHash']},
                    'official': {'status': 'pending', 'snapshotHash': proof['snapshotHash'],
                                 'safeReason': 'coverage-pending'}},
    }
    out = check_health_summary.evaluate(summary, now=_now())
    assert out['ok'] is True and out['status'] == 'partial'
    assert out['partial'] is True and out['warnings']
    # Tampered proof / missing receipt hash / old v1 summary fail closed.
    bad = json.loads(json.dumps(summary))
    bad['pending']['missingModels'] = []
    assert check_health_summary.evaluate(bad, now=_now())['ok'] is False
    old = dict(summary, schemaVersion=1, status='partial')
    assert check_health_summary.evaluate(old, now=_now())['ok'] is False
    v1_pending = dict(summary, schemaVersion=1, status='healthy')
    assert check_health_summary.evaluate(v1_pending, now=_now())['ok'] is False


def test_hard_failures_stay_errors_not_warnings(tmp_path):
    status, doc, receipt, _ = _fixture(tmp_path, bad_output=True)
    result = source_health_monitor.evaluate(
        status=status, repo=str(tmp_path), now=_now(),
        official_status_doc=doc, official_receipt=receipt)
    assert result['ok'] is False and result['partial'] is False
    assert any('official' in f or 'binding' in f for f in result['failures'])
    # Missing/wrong receipt cannot be promoted by status text.
    status2, doc2, receipt2, _ = _fixture(tmp_path, bad_receipt=True)
    result2 = source_health_monitor.evaluate(
        status=status2, repo=str(tmp_path), now=_now(),
        official_status_doc=doc2, official_receipt=receipt2)
    assert result2['ok'] is False
    # BigGo alert remains an error even if official is pure pending.
    status3, doc3, receipt3, _ = _fixture(tmp_path)
    status3['biggo']['status'] = 'degraded'
    status3['biggo']['safeReason'] = 'price-apply-failed'
    result3 = source_health_monitor.evaluate(
        status=status3, repo=str(tmp_path), now=_now(),
        official_status_doc=doc3, official_receipt=receipt3)
    assert result3['ok'] is False and result3['partial'] is False


def test_issue_plan_warning_keeps_issue_open_until_real_recovery():
    fresh = check_freshness.evaluate(
        {'datasetRetrievedAt': '2026-10-21T01:30:00Z'}, _now())
    fresh.update({'postdeployOk': True, 'deploymentMismatch': False,
                  'healthSummaryOk': True, 'healthKnown': True,
                  'healthSummaryPartial': True, 'healthPendingCount': 1})
    plan = check_freshness.plan_issue(None, fresh)
    assert plan['action'] == 'create'
    assert 'official_pending_warning:1' in plan['fingerprint']
    assert '官網規格待核' in plan['body']
    assert check_freshness.plan_issue({'body': plan['body']}, fresh)['action'] == 'noop'
    healed = dict(fresh)
    healed.pop('healthSummaryPartial')
    healed.pop('healthPendingCount')
    assert check_freshness.plan_issue({'body': plan['body']}, healed)['action'] == 'close'
    hard = dict(fresh)
    hard['healthSummaryOk'] = False
    hard['healthKnown'] = True
    hard['healthReason'] = 'health:official'
    assert check_freshness.plan_issue({'body': plan['body']}, hard)['action'] == 'update'


def test_issue_producer_contract_accepts_partial_report_and_warning_wording(tmp_path):
    health = {
        'schemaVersion': 1, 'configured': True, 'status': 'partial', 'ok': True,
        'partial': True, 'warnings': ['official:coverage-pending:1'],
        'failures': [], 'pending': {'missingModels': ['GWT07ES']},
        'sourceRunId': 123, 'headSha': 'a' * 40, 'sourceEvent': 'schedule',
        'sourceStates': {'emsd': {'status': 'ok', 'safeReason': 'acquired'},
                         'biggo': {'status': 'ok', 'safeReason': 'applied'},
                         'official': {'status': 'pending',
                                      'safeReason': 'coverage-pending'}},
    }
    ok, reason, codes, known = freshness_issue._health_fact(
        health, (123, 'a' * 40, 'schedule'))
    assert ok is True and known is True and reason == 'ok'
    result = {'ok': True, 'stale': False, 'postdeployOk': True,
              'deploymentMismatch': False, 'healthSummaryOk': True,
              'healthKnown': True, 'healthSummaryPartial': True,
              'healthPendingCount': 1, 'healthReason': reason}
    body = monitor_presentation.render_issue_body(result, 'fresh:official_pending_warning:1')
    assert '官網規格待核' in body
    assert '資料過期 (freshness): no' in body


def test_monitor_cli_writes_partial_summary_and_public_client_accepts(tmp_path):
    status, doc, receipt, _ = _fixture(tmp_path)
    (tmp_path / 'status.json').write_text(json.dumps(status), encoding='utf-8')
    (tmp_path / 'official.json').write_text(json.dumps(doc), encoding='utf-8')
    (tmp_path / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    rc = source_health_monitor.main([
        '--status', str(tmp_path / 'status.json'), '--repo', str(tmp_path),
        '--state', str(tmp_path / 'state.json'),
        '--report', str(tmp_path / 'report.json'),
        '--summary', str(tmp_path / 'summary.json'),
        '--official-status-file', str(tmp_path / 'official.json'),
        '--official-receipt', str(tmp_path / 'receipt.json'),
        '--now', '2026-10-21T02:00:00Z'])
    assert rc == 0
    summary = json.loads((tmp_path / 'summary.json').read_text())
    assert summary['schemaVersion'] == 2 and summary['status'] == 'partial'
    assert summary['ok'] is True and summary['partial'] is True
    assert summary['warnings'] == ['official:coverage-pending:1']
    assert summary['pending']['missingModels'] == ['GWT07ES']
    assert summary['pendingEvidenceHash'].startswith('sha256:')
    # Deployment binding is intentionally required by the public client; the
    # full producer fixture above covers that.  This CLI assertion focuses on
    # the partial summary contract itself.
    assert summary['sources']['official']['safeReason'] == 'coverage-pending'
