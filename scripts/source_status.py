#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Versioned, sanitised source-status receipts.

The UI must not infer "today" from the deployment clock.  This module records
separate facts:

  - EMSD attempt / acquisition / apply (package) timestamps and snapshot hash;
  - BigGo latest successful batch snapshot and apply time (or legacy UNKNOWN);
  - publish build / deployTime / payload hash.

No hostnames, filesystem paths, tokens, provider response bodies or private repo
identities are written.  Missing facts stay null/unknown; dates are never
fabricated.  Receipts are append-only by content hash.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

SCHEMA_VERSION = 1
STATUSES = ('ok', 'pending', 'degraded', 'blocked', 'unknown')
SAFE_REASONS = {
    'fresh', 'no-fetch-needed', 'acquired', 'applied', 'legacy-unknown',
    'awaiting-source', 'sources-degraded', 'price-snapshot-preserved',
    'price-apply-failed', 'needs-review', 'quota-or-lease-blocked',
    'receipt-missing', 'receipt-invalid', 'metadata-missing',
    'official-snapshot-untrusted', 'official-success-unverified',
    'official-no-verified-snapshot',
}
SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


class SourceStatusError(ValueError):
    pass


def _valid_ts(value):
    if value is None:
        return True
    if not isinstance(value, str) or not UTC_RE.match(value):
        return False
    try:
        _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return True
    except ValueError:
        return False


def _valid_date(value):
    if value is None:
        return True
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        _dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _source_block(status='unknown', *, last_attempt_at=None, last_acquired_at=None,
                  last_applied_at=None, last_applied_date=None, snapshot_id=None,
                  snapshot_hash=None, safe_reason='legacy-unknown', extra=None):
    block = {
        'status': status if status in STATUSES else 'unknown',
        'lastAttemptAt': last_attempt_at if _valid_ts(last_attempt_at) else None,
        'lastAcquiredAt': last_acquired_at if _valid_ts(last_acquired_at) else None,
        'lastAppliedAt': last_applied_at if _valid_ts(last_applied_at) else None,
        'lastAppliedDate': last_applied_date if _valid_date(last_applied_date) else None,
        'snapshotId': snapshot_id if isinstance(snapshot_id, str) and snapshot_id else None,
        'snapshotHash': snapshot_hash if isinstance(snapshot_hash, str)
                        and SHA_RE.match(snapshot_hash) else None,
        'safeReason': safe_reason if safe_reason in SAFE_REASONS else 'legacy-unknown',
    }
    if extra:
        for k, v in extra.items():
            if k in ('cycleId', 'stage', 'mode') and (
                    isinstance(v, (str, int)) and not isinstance(v, bool)):
                block[k] = v
    return block


def validate_status(status):
    """Return a list of human-readable errors; empty means valid."""
    errors = []
    if not isinstance(status, dict):
        return ['sourceStatus 必須係 object']
    if status.get('schemaVersion') != SCHEMA_VERSION:
        errors.append('sourceStatus.schemaVersion 必須係 1')
    for name in ('emsd', 'biggo', 'official'):
        block = status.get(name)
        if not isinstance(block, dict):
            errors.append(f'sourceStatus.{name} 必須係 object')
            continue
        if block.get('status') not in STATUSES:
            errors.append(f'sourceStatus.{name}.status 唔合法')
        for field in ('lastAttemptAt', 'lastAcquiredAt', 'lastAppliedAt'):
            if not _valid_ts(block.get(field)):
                errors.append(f'sourceStatus.{name}.{field} 必須係 UTC Z 或 null')
        if not _valid_date(block.get('lastAppliedDate')):
            errors.append(f'sourceStatus.{name}.lastAppliedDate 必須係 date 或 null')
        if block.get('snapshotHash') is not None and not (
                isinstance(block['snapshotHash'], str)
                and SHA_RE.match(block['snapshotHash'])):
            errors.append(f'sourceStatus.{name}.snapshotHash 唔合法')
        if block.get('safeReason') not in SAFE_REASONS:
            errors.append(f'sourceStatus.{name}.safeReason 唔合法')
        for field in ('snapshotId',):
            if block.get(field) is not None and not isinstance(block[field], str):
                errors.append(f'sourceStatus.{name}.{field} 必須係 string 或 null')
        for field in ('cycleId',):
            if block.get(field) is not None and not isinstance(block[field], str):
                errors.append(f'sourceStatus.{name}.{field} 必須係 string 或 null')
        if block.get('stage') is not None and (
                isinstance(block['stage'], bool) or not isinstance(block['stage'], int)):
            errors.append(f'sourceStatus.{name}.stage 必須係整數或 null')
    publish = status.get('publish')
    if publish is not None:
        if not isinstance(publish, dict):
            errors.append('sourceStatus.publish 必須係 object 或 null')
        else:
            for field in ('packagedAt', 'deployedAt'):
                if not _valid_ts(publish.get(field)):
                    errors.append(f'sourceStatus.publish.{field} 必須係 UTC Z 或 null')
            for field in ('build', 'releasePayloadHash', 'commit', 'deploymentReceipt'):
                if publish.get(field) is not None and not isinstance(publish[field], str):
                    errors.append(f'sourceStatus.publish.{field} 必須係 string 或 null')
    return errors


def deployment_matches(metadata, receipt):
    """True only when a deployment receipt is bound to this exact release.

    All four identity facts are mandatory and must be equal.  An old receipt
    that merely has correctly shaped liveHashes is NOT proof for a new build.
    """
    if not isinstance(receipt, dict) or receipt.get('success') is not True:
        return False
    if not _valid_ts(receipt.get('deployedAt')):
        return False
    metadata = metadata if isinstance(metadata, dict) else {}
    meta_build = metadata.get('build')
    meta_payload = metadata.get('releasePayloadHash')
    rec_build = receipt.get('build')
    rec_payload = receipt.get('releasePayloadHash')
    for value in (meta_build, meta_payload, rec_build, rec_payload):
        if not isinstance(value, str) or not value.strip():
            return False
    if rec_build != meta_build or rec_payload != meta_payload:
        return False
    rec_id = receipt.get('receiptId')
    if not isinstance(rec_id, str) or not rec_id.strip():
        return False
    live = receipt.get('liveHashes')
    required = {'index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv', 'metadata.json'}
    if not isinstance(live, dict) or set(live) != required:
        return False
    for v in live.values():
        if not (isinstance(v, str) and SHA_RE.match(v)):
            return False
    return True


def build_status(*, metadata=None, emsd_receipt=None, prices_meta=None,
                 biggo_status=None, official_status=None, deployment_receipt=None,
                 biggo_snapshot_hash=None, now=None):
    """Build a sanitised receipt from local facts.  Pure and side-effect free."""
    metadata = metadata if isinstance(metadata, dict) else {}
    receipt = emsd_receipt if isinstance(emsd_receipt, dict) else {}
    prices_meta = prices_meta if isinstance(prices_meta, dict) else {}
    biggo = biggo_status if isinstance(biggo_status, dict) else {}
    official = official_status if isinstance(official_status, dict) else {}
    deployed_at = None
    if deployment_matches(metadata, deployment_receipt):
        deployed_at = deployment_receipt.get('deployedAt')

    emsd_success = receipt.get('success') is True and receipt.get('aborted') is False \
        and not receipt.get('error')
    emsd_hash = receipt.get('datasetHash') if isinstance(receipt.get('datasetHash'), str) \
        and SHA_RE.match(receipt.get('datasetHash')) else metadata.get('datasetHash')
    emsd_acquired = receipt.get('retrievedAt') if emsd_success else metadata.get('datasetRetrievedAt')
    emsd_status = 'ok' if emsd_success or metadata.get('datasetHash') else 'unknown'
    if receipt and not emsd_success:
        emsd_status = 'degraded'
    emsd_reason = 'acquired' if emsd_success else (
        'legacy-unknown' if not receipt and not metadata else 'sources-degraded')
    emsd_block = _source_block(
        emsd_status,
        last_attempt_at=receipt.get('retrievedAt') or metadata.get('datasetRetrievedAt'),
        last_acquired_at=emsd_acquired,
        # Only an actual successful deployment receipt may claim this data was
        # applied live; a package/deployTime is not proof of a live write.
        last_applied_at=deployed_at,
        last_applied_date=(deployed_at[:10] if deployed_at else None),
        snapshot_id=metadata.get('datasetSnapshotId') or receipt.get('snapshotId'),
        snapshot_hash=emsd_hash if isinstance(emsd_hash, str) and SHA_RE.match(emsd_hash) else None,
        safe_reason=emsd_reason,
    )

    bstatus = biggo.get('status')
    cycle = biggo.get('cycle') or biggo.get('cycleId')
    stage = biggo.get('stage') if isinstance(biggo.get('stage'), int) \
        and not isinstance(biggo.get('stage'), bool) else None
    if bstatus in ('completed', 'completed-idempotent-applied', 'completed-recovered'):
        bstatus_ui, breason = 'ok', 'applied'
    elif bstatus == 'completed-apply-failed':
        bstatus_ui, breason = 'degraded', 'price-apply-failed'
    elif bstatus in ('skip-not-active',):
        bstatus_ui, breason = ('pending' if prices_meta.get('price_batch_start')
                               else 'unknown', 'no-fetch-needed')
    elif bstatus in ('needs-review', 'blocked-incomplete', 'blocked-needs-review',
                     'blocked-stale', 'blocked-quota-window-unsupported'):
        bstatus_ui, breason = 'blocked', ('needs-review' if 'review' in str(bstatus)
                                          else 'quota-or-lease-blocked')
    elif not bstatus:
        if prices_meta.get('last_run'):
            # A dated snapshot is a real fact, but without a current BigGo
            # attempt/apply receipt it must not be presented as fresh.
            bstatus_ui, breason = 'degraded', 'price-snapshot-preserved'
        else:
            bstatus_ui, breason = 'unknown', 'legacy-unknown'
    else:
        bstatus_ui, breason = 'degraded', 'price-snapshot-preserved'
    applied_at = prices_meta.get('appliedAt') if _valid_ts(prices_meta.get('appliedAt')) else None
    # A failed/pending bundle's snapshotHash is NOT the snapshot currently
    # served.  Prefer the locally applied snapshot hash; only use the bundle
    # hash when the status is a successful completed apply.
    actual_snapshot = biggo_snapshot_hash or prices_meta.get('appliedSnapshotHash')
    if not biggo_snapshot_hash and bstatus_ui == 'ok' and biggo.get('snapshotHash'):
        actual_snapshot = biggo.get('snapshotHash')
    biggo_block = _source_block(
        bstatus_ui,
        last_attempt_at=biggo.get('at'),
        last_acquired_at=biggo.get('acquiredAt') or biggo.get('generatedAt'),
        last_applied_at=applied_at,
        last_applied_date=(applied_at[:10] if applied_at else None),
        snapshot_id=biggo.get('snapshotId') or None,
        snapshot_hash=actual_snapshot,
        safe_reason=breason,
        extra={'cycleId': cycle, 'stage': stage} if cycle is not None or stage is not None else None,
    )
    decision = official.get('decision')
    now_dt = _dt.datetime.fromtimestamp(now if now is not None else time.time(),
                                        _dt.timezone.utc)
    def _trusted_ts(value):
        if not _valid_ts(value):
            return None
        try:
            dt = _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
        if dt > now_dt + _dt.timedelta(minutes=5):
            return None
        return value
    attempt_at = _trusted_ts(official.get('generatedAt'))
    last_success = _trusted_ts(official.get('lastSuccessAt'))
    current_success = last_success or (attempt_at if decision == 'advanced' else None)
    snap_hash = official.get('snapshotHash')
    snap_id = official.get('snapshotId')
    hash_ok = isinstance(snap_hash, str) and bool(SHA_RE.match(snap_hash))
    id_ok = isinstance(snap_id, str) and bool(snap_id.strip())
    success_time_ok = current_success is not None
    if decision == 'advanced':
        if hash_ok and id_ok and success_time_ok:
            ostatus, oreason = 'ok', 'applied'
        else:
            ostatus = 'degraded'
            oreason = ('official-snapshot-untrusted' if not hash_ok or not id_ok
                       else 'official-success-unverified')
    elif decision == 'queue-kept-pending-coverage':
        ostatus, oreason = 'degraded', 'sources-degraded'
    elif decision == 'queue-kept-fail-closed':
        ostatus, oreason = 'degraded', 'sources-degraded'
    else:
        ostatus, oreason = 'unknown', 'legacy-unknown'
    # Failed/pending attempts may keep a previously verified snapshot; never
    # use this deployment's deployedAt as an official apply fact.
    selected_hash = snap_hash if hash_ok else None
    selected_id = snap_id if id_ok else None
    official_applied = official.get('lastAppliedAt')
    applied_hash = official.get('appliedSnapshotHash')
    official_applied = (_trusted_ts(official_applied)
                        if selected_hash and applied_hash == selected_hash
                        and (ostatus == 'ok' or decision != 'advanced') else None)
    if decision == 'advanced':
        official_acquired = current_success if ostatus == 'ok' else None
    else:
        official_acquired = last_success
    official_block = _source_block(
        ostatus,
        last_attempt_at=attempt_at,
        last_acquired_at=official_acquired,
        last_applied_at=official_applied,
        last_applied_date=(official_applied[:10] if official_applied else None),
        snapshot_id=selected_id,
        snapshot_hash=selected_hash,
        safe_reason=oreason,
    )
    publish = {
        'build': metadata.get('build'),
        'commit': metadata.get('commit'),
        'packagedAt': metadata.get('deployTime') if _valid_ts(metadata.get('deployTime')) else None,
        'deployedAt': deployed_at,
        'deploymentReceipt': (deployment_receipt.get('receiptId')
                              if isinstance(deployment_receipt, dict) else None),
        'releasePayloadHash': metadata.get('releasePayloadHash'),
    }
    out = {
        'schemaVersion': SCHEMA_VERSION,
        'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                     time.gmtime(now if now is not None else time.time())),
    }
    # Explicit assignments (not a literal with any chance of duplicate keys).
    out['emsd'] = emsd_block
    out['biggo'] = biggo_block
    out['official'] = official_block
    out['publish'] = publish
    return out


def finalize_status(status, metadata):
    """Attach publish/apply facts from the core metadata without inventing dates."""
    import copy
    out = copy.deepcopy(status)
    metadata = metadata if isinstance(metadata, dict) else {}
    # EMSD data is applied to this site at deployTime.  BigGo is a separate
    # batch snapshot; never fill its apply time with the deployment clock.
    emsd = out.get('emsd')
    if isinstance(emsd, dict):
        if emsd.get('snapshotId') is None:
            emsd['snapshotId'] = metadata.get('datasetSnapshotId')
        if emsd.get('snapshotHash') is None:
            h = metadata.get('datasetHash')
            if isinstance(h, str) and SHA_RE.match(h):
                emsd['snapshotHash'] = h
        if emsd.get('lastAcquiredAt') is None and _valid_ts(metadata.get('datasetRetrievedAt')):
            emsd['lastAcquiredAt'] = metadata['datasetRetrievedAt']
    publish = out.get('publish') if isinstance(out.get('publish'), dict) else {}
    publish.update({
        'build': metadata.get('build'),
        'commit': metadata.get('commit'),
        'packagedAt': metadata.get('deployTime') if _valid_ts(metadata.get('deployTime')) else None,
        # deployedAt/deploymentReceipt only from an actual successful deployment receipt.
        'deployedAt': publish.get('deployedAt'),
        'deploymentReceipt': publish.get('deploymentReceipt'),
        'releasePayloadHash': metadata.get('releasePayloadHash'),
    })
    out['publish'] = publish
    return out


def _atomic_write(path, data):
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_receipt(path, status, *, history_dir=None, allow_replace=True):
    errors = validate_status(status)
    if errors:
        raise SourceStatusError('; '.join(errors))
    raw = json.dumps(status, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8') + b'\n'
    if not allow_replace and os.path.exists(path):
        raise SourceStatusError('receipt 已存在（append-only）')
    _atomic_write(path, raw)
    if history_dir:
        os.makedirs(history_dir, exist_ok=True)
        digest = hashlib.sha256(raw).hexdigest()[:16]
        stamp = str(status.get('generatedAt') or '').replace(':', '').replace('-', '')
        hist = os.path.join(history_dir, f'source-status-{stamp}-{digest}.json')
        if not os.path.exists(hist):
            try:
                fd = os.open(hist, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(fd, 'wb') as f:
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
            except FileExistsError:
                pass
    return raw


def _load_json(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description='Build/validate sanitised source status receipt')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('--metadata')
    b.add_argument('--emsd-receipt')
    b.add_argument('--prices-meta')
    b.add_argument('--biggo-status')
    b.add_argument('--official-status')
    b.add_argument('--deployment-receipt')
    b.add_argument('--biggo-snapshot-file')
    b.add_argument('--out', required=True)
    b.add_argument('--history-dir')
    v = sub.add_parser('validate')
    v.add_argument('path')
    args = ap.parse_args(argv)
    if args.cmd == 'build':
        snap_hash = None
        if args.biggo_snapshot_file:
            try:
                from biggo_canonical import sha256_json
                snap_hash = sha256_json(_load_json(args.biggo_snapshot_file))
            except (OSError, ValueError):
                snap_hash = None
        status = build_status(metadata=_load_json(args.metadata),
                              emsd_receipt=_load_json(args.emsd_receipt),
                              prices_meta=_load_json(args.prices_meta),
                              biggo_status=_load_json(args.biggo_status),
                              official_status=_load_json(args.official_status),
                              deployment_receipt=_load_json(args.deployment_receipt),
                              biggo_snapshot_hash=snap_hash)
        write_receipt(args.out, status, history_dir=args.history_dir)
        print(f'✅ source status：{args.out}')
        return 0
    try:
        with open(args.path, encoding='utf-8') as f:
            status = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ 讀取 source status 失敗：{e}', file=sys.stderr)
        return 1
    errors = validate_status(status)
    if errors:
        print('❌ source status 驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1
    print('✅ source status 有效')
    return 0


if __name__ == '__main__':
    sys.exit(main())
