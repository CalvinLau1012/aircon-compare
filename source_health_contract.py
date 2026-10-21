#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared source-health contracts for public/private candidates.

Two related contracts live here:

1. ``coverage_pending_proof`` validates that an official ``pending`` status is
   *proved* coverage pending, not a generic source failure and not a text-only
   safeReason.  It checks the receipt/status identity, target routes, previous
   complete output manifest and missing-model reasons.  The private monitor
   additionally re-hashes the actual output bytes.
2. ``summary_is_partial`` / ``validate_pending_summary`` validate the sanitised
   health-summary v2 ``partial`` class.  Public CI can therefore accept the
   warning only when the summary carries the same proof; an old v1 summary is
   never upgraded to partial.

No hostnames, filesystem paths, credentials or raw provider bodies are stored
in either contract.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re

SUMMARY_KIND = 'aircon-source-health-summary'
PARTIAL_REASON = 'official:coverage-pending'
SUMMARY_SCHEMA_VERSION = 2
_SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
_PENDING_REASONS = {
    'target-not-in-configured-comfee-list', 'source-route-not-configured',
    'stage-source-did-not-cover-target', 'http-404', 'http-410',
}
PROOF_KEYS = {
    'schemaVersion', 'decision', 'stage', 'targetModels', 'selectedOutputs',
    'sourceRouting', 'missingModels', 'missingReasons', 'outputManifest',
    'snapshotHash', 'snapshotId', 'lastSuccessAt', 'receiptHash',
}


class ContractError(ValueError):
    pass


def _sha(value):
    return isinstance(value, str) and bool(_SHA_RE.match(value))


def _utc(value):
    if not isinstance(value, str) or not _UTC_RE.match(value):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def canonical_hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _manifest_errors(manifest, stage, snapshot_hash):
    errors = []
    if not isinstance(manifest, dict):
        return ['manifest-missing']
    if manifest.get('schemaVersion') != 1:
        errors.append('manifest-schema')
    if manifest.get('stage') != stage:
        errors.append('manifest-stage')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        errors.append('manifest-files')
    else:
        for name, meta in files.items():
            if not isinstance(name, str) or not name or '/' in name or '\\' in name:
                errors.append('manifest-file-name')
                continue
            if not isinstance(meta, dict):
                errors.append('manifest-file-meta')
                continue
            if not _sha(meta.get('sha256')):
                errors.append('manifest-file-hash')
            if isinstance(meta.get('size'), bool) or not isinstance(meta.get('size'), int) \
                    or meta['size'] <= 0:
                errors.append('manifest-file-size')
    body = {'schemaVersion': 1, 'stage': stage, 'files': files}
    if manifest.get('manifestHash') != canonical_hash(body):
        errors.append('manifest-hash')
    if snapshot_hash != manifest.get('manifestHash'):
        errors.append('snapshot-manifest-binding')
    return errors


def _routes(target_models):
    try:
        from run_official_batch import route_for_model
        from publish_official_status import SOURCE_OUTPUTS
    except Exception as exc:  # pragma: no cover - import environment failure
        raise ContractError(f'route-profile-unavailable:{type(exc).__name__}')
    routes = {m: route_for_model(m) for m in target_models}
    outputs = sorted({SOURCE_OUTPUTS[r['source']] for r in routes.values()
                      if r.get('supported') is True and r.get('source') in SOURCE_OUTPUTS})
    return routes, outputs


def _check_target_profile(official, missing, reasons):
    targets = official.get('targetModels')
    if not isinstance(targets, list) or not targets \
            or len(targets) != len(set(targets)) \
            or any(not isinstance(x, str) or not x for x in targets):
        raise ContractError('target-profile-invalid')
    if not isinstance(missing, list) or not missing \
            or len(missing) != len(set(missing)) \
            or any(not isinstance(x, str) or not x for x in missing):
        raise ContractError('missing-models-invalid')
    if not set(missing).issubset(set(targets)):
        raise ContractError('missing-models-not-in-profile')
    routes, expected_outputs = _routes(targets)
    if official.get('sourceRouting') != routes:
        raise ContractError('source-routing-profile-mismatch')
    if sorted(official.get('selectedOutputs') or []) != expected_outputs:
        raise ContractError('selected-outputs-target-mismatch')
    if not isinstance(reasons, dict) or set(reasons) != set(missing):
        raise ContractError('missing-reasons-invalid')
    for model, reason in reasons.items():
        route = routes.get(model) or {}
        allowed = set(_PENDING_REASONS)
        if isinstance(route.get('reason'), str):
            allowed.add(route['reason'])
        if reason not in allowed:
            raise ContractError(f'missing-reason-unsupported:{reason}')
    return routes, expected_outputs


def coverage_pending_proof(official_doc, *, receipt=None, require_receipt=False):
    """Return a sanitised, hash-bound proof of pure coverage pending.

    ``official_doc`` is the private official_batch_status document.  ``receipt``
    is the raw official batch receipt when available; for private monitoring it
    is required so missing/corrupt receipts cannot be treated as pending.
    """
    if not isinstance(official_doc, dict):
        raise ContractError('official-status-missing')
    if official_doc.get('decision') != 'queue-kept-pending-coverage':
        raise ContractError('not-pending-coverage')
    if official_doc.get('pendingCoverage') is not True:
        raise ContractError('pending-coverage-flag-missing')
    stage = official_doc.get('stage')
    if isinstance(stage, bool) or not isinstance(stage, int) or stage not in (1, 2):
        raise ContractError('stage-invalid')
    missing = official_doc.get('missingModels')
    reasons = official_doc.get('coveragePendingReasons')
    _check_target_profile(official_doc, missing, reasons)
    manifest = official_doc.get('outputManifest')
    snapshot_hash = official_doc.get('snapshotHash')
    errors = _manifest_errors(manifest, stage, snapshot_hash)
    if errors:
        raise ContractError(';'.join(errors))
    if not isinstance(official_doc.get('snapshotId'), str) \
            or not official_doc['snapshotId'].strip():
        raise ContractError('snapshot-id-missing')
    if _utc(official_doc.get('lastSuccessAt')) is None:
        raise ContractError('last-success-time-invalid')
    receipt_hash = None
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise ContractError('receipt-invalid')
        if receipt.get('decision') != 'queue-kept-pending-coverage':
            raise ContractError('receipt-decision-mismatch')
        if receipt.get('failureClass') != 'none' or receipt.get('safeToContinue') is not True:
            raise ContractError('receipt-not-clean-pending')
        if receipt.get('coveragePending') is not True or receipt.get('advanced') is not False:
            raise ContractError('receipt-pending-flags')
        if receipt.get('stage') != stage:
            raise ContractError('receipt-stage-mismatch')
        if receipt.get('missingModels') != missing:
            raise ContractError('receipt-missing-models-mismatch')
        if receipt.get('coveragePendingReasons') != reasons:
            raise ContractError('receipt-missing-reasons-mismatch')
        if receipt.get('outputManifest') != manifest:
            raise ContractError('receipt-manifest-mismatch')
        if receipt.get('targetModels') != official_doc.get('targetModels'):
            raise ContractError('receipt-target-mismatch')
        if receipt.get('selectedOutputs') != official_doc.get('selectedOutputs'):
            raise ContractError('receipt-selected-outputs-mismatch')
        if receipt.get('sourceRouting') != official_doc.get('sourceRouting'):
            raise ContractError('receipt-routing-mismatch')
        if not _utc(receipt.get('finishedAt')):
            raise ContractError('receipt-finished-at-invalid')
        receipt_hash = canonical_hash(receipt)
    elif require_receipt:
        raise ContractError('receipt-missing')
    return {
        'schemaVersion': 1,
        'decision': 'queue-kept-pending-coverage',
        'stage': stage,
        'targetModels': list(official_doc['targetModels']),
        'selectedOutputs': list(official_doc['selectedOutputs']),
        'sourceRouting': official_doc['sourceRouting'],
        'missingModels': list(missing),
        'missingReasons': dict(reasons),
        'outputManifest': manifest,
        'snapshotHash': snapshot_hash,
        'snapshotId': official_doc['snapshotId'],
        'lastSuccessAt': official_doc['lastSuccessAt'],
        'receiptHash': receipt_hash,
    }


def proof_hash(proof):
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise ContractError('proof-keys-invalid')
    return canonical_hash(proof)


def validate_pending_summary(summary):
    """Validate a public health summary's partial evidence.

    Returns the normalized proof.  The caller still validates the outer summary
    hashes/age/identity using its existing contract.
    """
    if not isinstance(summary, dict):
        raise ContractError('summary-invalid')
    if summary.get('schemaVersion') != SUMMARY_SCHEMA_VERSION:
        raise ContractError('summary-schema')
    if summary.get('kind') != SUMMARY_KIND:
        raise ContractError('summary-kind')
    if summary.get('ok') is not True or summary.get('status') != 'partial' \
            or summary.get('partial') is not True:
        raise ContractError('summary-not-partial')
    if summary.get('failures'):
        raise ContractError('summary-has-failures')
    warnings = summary.get('warnings')
    if not isinstance(warnings, list) or len(warnings) != 1 \
            or not isinstance(warnings[0], str):
        raise ContractError('summary-warnings')
    proof = summary.get('pending')
    if not isinstance(proof, dict) or set(proof) != PROOF_KEYS:
        raise ContractError('summary-pending-keys')
    if warnings[0] != f'{PARTIAL_REASON}:{len(proof.get("missingModels") or [])}':
        raise ContractError('summary-warning-mismatch')
    if summary.get('pendingEvidenceHash') != proof_hash(proof):
        raise ContractError('summary-proof-hash')
    if not _sha(proof.get('receiptHash')):
        raise ContractError('summary-receipt-hash')
    errs = _manifest_errors(proof.get('outputManifest'), proof.get('stage'),
                            proof.get('snapshotHash'))
    if errs:
        raise ContractError('summary-manifest-invalid:' + ','.join(sorted(set(errs))))
    _check_target_profile(
        {'targetModels': proof.get('targetModels'),
         'selectedOutputs': proof.get('selectedOutputs'),
         'sourceRouting': proof.get('sourceRouting')},
        proof.get('missingModels'), proof.get('missingReasons'))
    if summary.get('officialManifestHash') != proof.get('snapshotHash'):
        raise ContractError('summary-official-manifest-binding')
    sources = summary.get('sources')
    if not isinstance(sources, dict):
        raise ContractError('summary-sources')
    official = sources.get('official') or {}
    if official.get('status') != 'pending' \
            or official.get('safeReason') != 'coverage-pending':
        raise ContractError('summary-official-not-pending')
    for name in ('emsd', 'biggo'):
        block = sources.get(name) or {}
        if block.get('status') != 'ok':
            raise ContractError(f'summary-{name}-not-ok')
    return proof
