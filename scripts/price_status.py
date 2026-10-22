#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure, versioned price-workflow status receipt (schemaVersion 1).

Why this module exists
----------------------
A monthly BigGo snapshot is allowed to be older than the daily source window.
Age alone is therefore not a pipeline failure.  This receipt distinguishes:

  no-demand        no pending EMSD new-model demand
  month-completed  the once-per-month batch already finished this HKT month
  waiting-eligible pending new-model demand, legal to start (or in a valid cooldown)
  active           the seven-day cycle is in progress
  blocked          malformed evidence, provider failure, hash contradiction,
                   invalid/unbounded cooldown, or an active batch with no progress

The receipt is derived from actual files (prices_meta, price_demand, snapshot,
stage status, completed-receive report, optional monthly anchor) and is
re-derived/re-read before it is published.  It never stores hostnames, private
paths, tokens or provider response bodies.

`verify_receipt` rebuilds the status from the supplied live inputs and requires
byte-identical facts; a caller cannot make it green by hand-writing ok/time and
recomputing a self hash.
"""
from __future__ import annotations

import argparse
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
if os.path.join(BASE, 'scripts') not in sys.path:
    sys.path.insert(0, os.path.join(BASE, 'scripts'))

import price_demand  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402

SCHEMA_VERSION = 1
KIND = 'aircon-price-status'
STATUSES = ('no-demand', 'month-completed', 'waiting-eligible', 'active', 'blocked')
HKT = _dt.timezone(_dt.timedelta(hours=8))
MAX_AGE_HOURS = 36.0
MAX_COOLDOWN_SECONDS = 7 * 24 * 3600
_SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
_REASONS = {
    'no-demand': {'no-new-model'},
    'month-completed': {'month-completed'},
    'waiting-eligible': {'new-model-demand', 'cooldown'},
    'active': {'in-progress', 'cooldown', 'active-no-progress'},
    'blocked': {
        'meta-invalid', 'demand-invalid', 'demand-missing', 'snapshot-missing',
        'snapshot-hash-mismatch', 'provider-failure', 'receive-failure',
        'invalid-cooldown', 'anchor-missing', 'anchor-invalid', 'anchor-mismatch',
        'anchor-not-completed', 'future-evidence', 'invalid-status-input',
    },
}
_ACTIVE_STATUSES = ('completed', 'completed-idempotent-applied', 'completed-recovered')
_FAILURE_STATUSES = {
    'needs-review', 'blocked-incomplete', 'blocked-needs-review', 'blocked-stale',
    'blocked-quota-window-unsupported', 'blocked-diagnostic-pending',
    'blocked-intent-persist', 'completed-apply-failed', 'completed-legacy-manual',
    'completed-bundle-unavailable', 'publish-uncertain', 'blocked-coordinator-error',
}
_RECEIVE_OK_REASONS = {
    'applied', 'already-applied', 'noop', 'remote-not-completed',
    'coordinator-not-configured', 'remote-state-missing', None,
}


class PriceStatusError(ValueError):
    pass


def _utc(now=None):
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, (int, float)) and not isinstance(now, bool):
        now = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    elif isinstance(now, _dt.datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=_dt.timezone.utc)
    else:
        raise ValueError('now must be datetime or epoch seconds')
    return now.astimezone(_dt.timezone.utc)


def _stamp(dt):
    return dt.astimezone(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse(value):
    if not isinstance(value, str) or not _UTC_RE.match(value):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
            _dt.timezone.utc)
    except ValueError:
        return None


def _parse_date_hkt(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        d = _dt.date.fromisoformat(value)
    except ValueError:
        return None
    return _dt.datetime(d.year, d.month, d.day, tzinfo=HKT).astimezone(_dt.timezone.utc)


def _next_month_start(now):
    hkt = now.astimezone(HKT)
    year, month = (hkt.year + 1, 1) if hkt.month == 12 else (hkt.year, hkt.month + 1)
    return _dt.datetime(year, month, 1, tzinfo=HKT).astimezone(_dt.timezone.utc)


def _month_hkt(now):
    return now.astimezone(HKT).strftime('%Y-%m')


def _is_sha(value):
    return isinstance(value, str) and bool(_SHA_RE.match(value))


def _hash_doc(doc):
    if doc is None:
        return None
    return sha256_id(canonical_json_bytes(doc))


def _meta_basic(meta):
    if not isinstance(meta, dict):
        return 'meta-invalid'
    idx = meta.get('price_batch_idx')
    if idx is not None and (isinstance(idx, bool) or not isinstance(idx, int)
                            or not (0 <= idx <= 7)):
        return 'meta-invalid'
    if meta.get('price_batch_start') is not None \
            and _parse_date_hkt(meta.get('price_batch_start')) is None:
        return 'meta-invalid'
    if meta.get('last_price_month') is not None:
        try:
            _dt.datetime.strptime(str(meta.get('last_price_month')), '%Y-%m')
        except ValueError:
            return 'meta-invalid'
    if meta.get('blocked_until') is not None:
        value = meta.get('blocked_until')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return 'invalid-cooldown'
    applied = meta.get('appliedAt')
    if applied is not None and _parse(applied) is None:
        # Legacy date-only apply facts are readable but cannot be progress
        # evidence; this is not by itself a hard invalid meta.
        pass
    return None


def _post_state_hashes(post_state):
    if not isinstance(post_state, dict):
        return None
    tracking = post_state.get('trackingHash')
    blacklist = post_state.get('blacklistHash')
    if not (_is_sha(tracking) and _is_sha(blacklist)):
        return None
    return {'trackingHash': tracking, 'blacklistHash': blacklist}


def _matching_apply_receipt(meta, biggo, completed_sync, snapshot_hash,
                            post_state, cycle_id, stage):
    """Return only a receipt bound to the exact applied stage/post-state.

    ``finishedAt`` is intentionally not used here.  It is a receive clock, not
    proof that a new stage advanced.
    """
    post = _post_state_hashes(post_state)
    if not post or not _is_sha(snapshot_hash):
        return None
    bundle = meta.get('appliedBundleHash')
    candidates = []
    if isinstance(completed_sync, dict) and completed_sync:
        candidates.append(completed_sync)
    local = biggo.get('localReceiver') if isinstance(biggo, dict) else None
    if isinstance(local, dict):
        candidates.append(local)
    for receipt in candidates:
        if receipt.get('ok') is not True \
                or receipt.get('postStateVerified') is not True:
            continue
        if cycle_id is not None and receipt.get('cycleId') != cycle_id:
            continue
        if stage is not None and receipt.get('stage') != stage:
            continue
        if bundle is not None and receipt.get('bundleHash') != bundle:
            continue
        verified = receipt.get('verifiedPost')
        if not isinstance(verified, dict):
            continue
        if verified.get('biggo_prices.json') != snapshot_hash:
            continue
        if verified.get('model_status.json') != post['trackingHash']:
            continue
        if verified.get('model_blacklist.json') != post['blacklistHash']:
            continue
        return receipt
    return None


def _verified_progress(meta, *, snapshot_hash, post_state, apply_receipt,
                       active, idx, cycle_start):
    """Only the current applied stage may advance progress.

    meta.appliedAt is the persisted first successful apply time.  A matching
    apply receipt/post-state is required for that fact; receive finishedAt,
    monitor readAt, anchor.updatedAt and repeated applies never move it.
    """
    if not isinstance(meta, dict):
        return None
    if not active and meta.get('last_price_month') is None:
        return None
    last_stage = idx
    if last_stage <= 0:
        return cycle_start if active else None
    if active:
        start = str(meta.get('price_batch_start') or '')
        expected = f'{start}:{last_stage}/7'
    else:
        expected = meta.get('last_applied_cycle')
    if not isinstance(expected, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}:[1-7]/7', expected):
        return None
    if expected.rsplit(':', 1)[1] != f'{last_stage}/7':
        return None
    if meta.get('last_applied_cycle') != expected:
        return None
    if meta.get('appliedSnapshotHash') != snapshot_hash:
        return None
    if not _is_sha(meta.get('appliedBundleHash')):
        return None
    if apply_receipt is None:
        return None
    applied = _parse(meta.get('appliedAt'))
    if applied is None:
        return None
    return applied


def _cooldown_witness(meta, biggo, now_dt, cycle_id, stage,
                      max_cooldown_seconds):
    """Validate a real skip-cooldown receipt; never trust blocked_until alone."""
    raw = meta.get('blocked_until')
    witness_until = None
    if isinstance(biggo, dict) and biggo.get('status') == 'skip-cooldown':
        w = biggo.get('cooldownUntil')
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
            return None, 'invalid-cooldown'
        if biggo.get('cycle') != cycle_id or biggo.get('stage') != stage:
            return None, 'cooldown-cycle-mismatch'
        observed = _parse(biggo.get('at'))
        if observed is None or observed > now_dt + _dt.timedelta(minutes=5):
            return None, 'cooldown-time-invalid'
        witness_until = _dt.datetime.fromtimestamp(float(w), _dt.timezone.utc)
    if raw is None and witness_until is None:
        return None, None
    if isinstance(raw, bool) or (raw is not None and (
            not isinstance(raw, (int, float)) or raw < 0)):
        return None, 'invalid-cooldown'
    meta_until = (_dt.datetime.fromtimestamp(float(raw), _dt.timezone.utc)
                  if raw is not None else None)
    if witness_until is None:
        return None, 'invalid-cooldown'
    if meta_until is not None and abs((meta_until - witness_until).total_seconds()) > 300:
        return None, 'cooldown-conflict'
    if witness_until > now_dt + _dt.timedelta(seconds=max_cooldown_seconds):
        return None, 'invalid-cooldown'
    return witness_until, None


def build_receipt(*, now=None, meta=None, demand=None, biggo=None,
                  completed_sync=None, snapshot_hash=None, anchor=None,
                  shared=False, post_state=None, max_age_hours=MAX_AGE_HOURS,
                  max_cooldown_seconds=MAX_COOLDOWN_SECONDS):
    """Derive the price status from actual facts.  Pure and deterministic."""
    now_dt = _utc(now)
    determined = _stamp(now_dt)
    meta = meta if isinstance(meta, dict) else {}
    biggo = biggo if isinstance(biggo, dict) else {}
    completed_sync = completed_sync if isinstance(completed_sync, dict) else {}
    failure = _meta_basic(meta)
    if failure:
        status, reason, healthy = 'blocked', failure, False
    elif not isinstance(demand, dict):
        status, reason, healthy = 'blocked', 'demand-missing', False
    else:
        try:
            price_demand.validate(demand)
        except price_demand.DemandError:
            status, reason, healthy = 'blocked', 'demand-invalid', False
        else:
            failure = None
    # Derive only when no structural failure was recorded above.
    if 'status' not in locals():
        status = reason = None
        healthy = True
    if status is None:
        if not _is_sha(snapshot_hash):
            status, reason, healthy = 'blocked', 'snapshot-missing', False
        elif isinstance(meta.get('appliedSnapshotHash'), str) \
                and meta.get('appliedSnapshotHash') != snapshot_hash:
            status, reason, healthy = 'blocked', 'snapshot-hash-mismatch', False
        elif biggo.get('snapshotHash') and biggo.get('snapshotHash') != snapshot_hash:
            status, reason, healthy = 'blocked', 'snapshot-hash-mismatch', False
        elif biggo.get('alert') is True or biggo.get('status') in _FAILURE_STATUSES:
            status, reason, healthy = 'blocked', 'provider-failure', False
        elif completed_sync and completed_sync.get('ok') is not True \
                and completed_sync.get('reason') not in _RECEIVE_OK_REASONS:
            status, reason, healthy = 'blocked', 'receive-failure', False
        elif str(biggo.get('status') or '').startswith('completed-apply-failed'):
            status, reason, healthy = 'blocked', 'provider-failure', False
    start = meta.get('price_batch_start')
    idx = meta.get('price_batch_idx', 0)
    active = bool(start) and isinstance(idx, int) and not isinstance(idx, bool) \
        and 0 <= idx < 7
    cycle_start = _parse_date_hkt(start) if active else None
    cycle_id = (f'{start}:{idx + 1}/7' if active else
                (meta.get('last_applied_cycle')
                 if meta.get('last_price_month') == _month_hkt(now_dt) else None))
    cooldown_until = None
    if status is None:
        cooldown_until, cooldown_error = _cooldown_witness(
            meta, biggo, now_dt, cycle_id, idx, max_cooldown_seconds)
        if cooldown_error:
            status, reason, healthy = 'blocked', cooldown_error, False
    apply_receipt = None
    post = _post_state_hashes(post_state)
    if status is None:
        last_stage = idx
        applied_cycle = (f'{start}:{last_stage}/7' if active and last_stage > 0
                         else (meta.get('last_applied_cycle')
                               if not active and meta.get('last_price_month')
                               == _month_hkt(now_dt) else None))
        if last_stage > 0 or (not active and applied_cycle):
            apply_receipt = _matching_apply_receipt(
                meta, biggo, completed_sync, snapshot_hash, post,
                applied_cycle, last_stage)
        if post is None and not active and last_stage > 0:
            status, reason, healthy = 'blocked', 'post-state-missing', False
        elif (not active and meta.get('last_price_month') == _month_hkt(now_dt)
              and apply_receipt is None):
            status, reason, healthy = 'blocked', 'apply-evidence-missing', False
    progress = _verified_progress(
        meta, snapshot_hash=snapshot_hash, post_state=post,
        apply_receipt=apply_receipt, active=active, idx=idx,
        cycle_start=cycle_start)
    next_eligible = None
    if status is None:
        if active:
            cycle_id = f'{start}:{idx + 1}/7'
            cooldown_applies = cooldown_until is not None and (
                progress is None or progress <= cooldown_until)
            if cooldown_applies:
                # A legal cooldown is not "no progress": the stall deadline
                # starts from its actual recovery time, not from this monitor
                # run and not from a repeated skip.
                deadline = cooldown_until + _dt.timedelta(hours=max_age_hours)
                if now_dt < cooldown_until:
                    status, reason, healthy = 'active', 'cooldown', True
                    next_eligible = cooldown_until
                elif (now_dt - cooldown_until).total_seconds() > max_age_hours * 3600:
                    status, reason, healthy = 'active', 'active-no-progress', False
                    next_eligible = deadline
                else:
                    status, reason, healthy = 'active', 'cooldown', True
                    next_eligible = deadline
            elif progress is None or (now_dt - progress).total_seconds() > max_age_hours * 3600:
                status, reason, healthy = 'active', 'active-no-progress', False
                next_eligible = (progress + _dt.timedelta(hours=max_age_hours)
                                 if progress is not None else cycle_start)
            else:
                status, reason, healthy = 'active', 'in-progress', True
                next_eligible = progress + _dt.timedelta(hours=max_age_hours)
        elif meta.get('last_price_month') == _month_hkt(now_dt):
            status, reason, healthy = 'month-completed', 'month-completed', True
            next_eligible = _next_month_start(now_dt)
        elif price_demand.pending(demand):
            status = 'waiting-eligible'
            healthy = True
            if cooldown_until is not None and cooldown_until > now_dt:
                reason, next_eligible = 'cooldown', cooldown_until
            else:
                reason, next_eligible = 'new-model-demand', now_dt
        else:
            status, reason, healthy = 'no-demand', 'no-new-model', True
    if status is None:
        status, reason, healthy = 'blocked', 'invalid-status-input', False
    # Shared mode requires the actual monthly anchor; mixed/missing authority
    # is never silently treated as a completed month.
    if shared and status != 'blocked':
        if not isinstance(anchor, dict):
            status, reason, healthy = 'blocked', 'anchor-missing', False
        else:
            try:
                import price_cycle
                errs = price_cycle.validate_anchor(anchor)
            except Exception:  # noqa: BLE001
                errs = ['anchor-unreadable']
            if errs:
                status, reason, healthy = 'blocked', 'anchor-invalid', False
            elif active:
                if anchor.get('startedAt') != start or anchor.get('stage') != idx:
                    status, reason, healthy = 'blocked', 'anchor-mismatch', False
            elif status == 'month-completed':
                if anchor.get('status') != 'completed' or anchor.get('stage') != 7:
                    status, reason, healthy = 'blocked', 'anchor-not-completed', False
    if not healthy:
        next_eligible = next_eligible if next_eligible is not None else None
    last_progress = _stamp(progress) if progress is not None else None
    evidence = {
        'snapshotHash': snapshot_hash if _is_sha(snapshot_hash) else None,
        'metaHash': _hash_doc(meta),
        'demandHash': _hash_doc(demand),
        'biggoStatusHash': _hash_doc(biggo),
        'completedSyncHash': _hash_doc(completed_sync) if completed_sync else None,
        'anchorHash': _hash_doc(anchor) if anchor is not None else None,
        'bundleHash': meta.get('appliedBundleHash') if _is_sha(
            meta.get('appliedBundleHash')) else None,
        'stageResultHash': ((completed_sync.get('stageResultHash')
                             if isinstance(completed_sync.get('stageResultHash'), str)
                             else None)
                            or (biggo.get('stageResultHash')
                                if isinstance(biggo.get('stageResultHash'), str)
                                else None)),
        'postStateHash': _hash_doc(post) if post is not None else None,
        'applyReceiptHash': _hash_doc(apply_receipt)
        if isinstance(apply_receipt, dict) else None,
        'cooldownHash': _hash_doc({
            'status': biggo.get('status'), 'cooldownUntil': biggo.get('cooldownUntil'),
            'at': biggo.get('at'), 'cycle': biggo.get('cycle'),
            'stage': biggo.get('stage')}) if cooldown_until is not None else None,
        'pendingDemandCount': len(price_demand.pending(demand))
        if isinstance(demand, dict) and not status == 'blocked' else None,
        'unknownDemandCount': len(price_demand.unknown(demand))
        if isinstance(demand, dict) and not status == 'blocked' else None,
    }
    body = {
        'schemaVersion': SCHEMA_VERSION,
        'kind': KIND,
        'determinedAt': determined,
        'status': status,
        'safeReason': reason,
        'healthy': bool(healthy),
        'cycleId': (f'{start}:{idx + 1}/7' if active else
                    (meta.get('last_applied_cycle')
                     if status == 'month-completed'
                     and isinstance(meta.get('last_applied_cycle'), str) else None)),
        'stage': (idx if active else
                  (idx if status == 'month-completed' else None)),
        'month': _month_hkt(now_dt),
        'lastVerifiedProgressAt': last_progress,
        'nextEligibleAt': _stamp(next_eligible) if next_eligible is not None else None,
        'evidence': evidence,
    }
    body['receiptHash'] = sha256_id(canonical_json_bytes(body))
    return body


def validate_receipt(receipt, *, now=None, max_age_hours=MAX_AGE_HOURS,
                     max_cooldown_seconds=MAX_COOLDOWN_SECONDS):
    """Structural/self-hash validation; use verify_receipt for live facts."""
    if not isinstance(receipt, dict):
        raise PriceStatusError('receipt must be an object')
    expected = {'schemaVersion', 'kind', 'determinedAt', 'status', 'safeReason',
                'healthy', 'cycleId', 'stage', 'month', 'lastVerifiedProgressAt',
                'nextEligibleAt', 'evidence', 'receiptHash'}
    if set(receipt) != expected:
        raise PriceStatusError('receipt keys invalid')
    if receipt.get('schemaVersion') != SCHEMA_VERSION or receipt.get('kind') != KIND:
        raise PriceStatusError('receipt schema/kind invalid')
    if receipt.get('status') not in STATUSES:
        raise PriceStatusError('receipt status invalid')
    if receipt.get('safeReason') not in _REASONS[receipt['status']]:
        raise PriceStatusError('receipt reason/status mismatch')
    if type(receipt.get('healthy')) is not bool:
        raise PriceStatusError('receipt healthy invalid')
    if receipt['status'] == 'blocked' and receipt['healthy'] is not False:
        raise PriceStatusError('blocked receipt cannot be healthy')
    if receipt['status'] == 'active' and receipt.get('safeReason') == 'active-no-progress' \
            and receipt['healthy'] is not False:
        raise PriceStatusError('stalled active receipt cannot be healthy')
    now_dt = _utc(now)
    determined = _parse(receipt.get('determinedAt'))
    if determined is None:
        raise PriceStatusError('determinedAt invalid')
    if determined > now_dt + _dt.timedelta(minutes=5):
        raise PriceStatusError('determinedAt future')
    if not re.fullmatch(r'\d{4}-\d{2}', str(receipt.get('month'))):
        raise PriceStatusError('month invalid')
    for field in ('lastVerifiedProgressAt', 'nextEligibleAt'):
        value = receipt.get(field)
        if value is not None and _parse(value) is None:
            raise PriceStatusError(f'{field} invalid')
        if field == 'nextEligibleAt' and value is not None:
            dt = _parse(value)
            if dt > now_dt + _dt.timedelta(days=62):
                raise PriceStatusError('nextEligibleAt unbounded/future')
    progress = _parse(receipt.get('lastVerifiedProgressAt'))
    if progress is not None and progress > determined + _dt.timedelta(minutes=5):
        raise PriceStatusError('lastVerifiedProgressAt after determination')
    nxt = _parse(receipt.get('nextEligibleAt'))
    if receipt['status'] == 'active':
        if not isinstance(receipt.get('cycleId'), str) \
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}:\d/7', receipt['cycleId']):
            raise PriceStatusError('active cycle invalid')
        if isinstance(receipt.get('stage'), bool) \
                or not isinstance(receipt.get('stage'), int) \
                or not (0 <= receipt['stage'] < 7):
            raise PriceStatusError('active stage invalid')
        if receipt.get('safeReason') == 'active-no-progress':
            if progress is None or (now_dt - progress).total_seconds() <= max_age_hours * 3600:
                raise PriceStatusError('active-no-progress not actually stalled')
            nxt = _parse(receipt.get('nextEligibleAt'))
            if nxt is not None and nxt > now_dt + _dt.timedelta(minutes=5):
                raise PriceStatusError('premature nextEligibleAt for stalled active')
        elif receipt.get('safeReason') == 'cooldown':
            nxt = _parse(receipt.get('nextEligibleAt'))
            if nxt is None or nxt <= now_dt:
                raise PriceStatusError('active cooldown missing legal recovery time')
            if nxt > now_dt + _dt.timedelta(
                    seconds=max_cooldown_seconds + max_age_hours * 3600):
                raise PriceStatusError('active cooldown unbounded')
        elif receipt.get('safeReason') == 'in-progress':
            if progress is None or (now_dt - progress).total_seconds() > max_age_hours * 3600:
                raise PriceStatusError('in-progress active is actually stale')
            nxt = _parse(receipt.get('nextEligibleAt'))
            want = progress + _dt.timedelta(hours=max_age_hours)
            if nxt is None or abs((nxt - want).total_seconds()) > 60:
                raise PriceStatusError('in-progress nextEligibleAt invalid')
    elif receipt['status'] == 'month-completed':
        if not isinstance(receipt.get('cycleId'), str) \
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}:\d/7', receipt['cycleId']):
            raise PriceStatusError('month-completed cycle invalid')
        if receipt.get('stage') != 7:
            raise PriceStatusError('month-completed stage must be 7')
        if receipt['cycleId'][:7] != receipt.get('month'):
            raise PriceStatusError('month-completed cycle month mismatch')
        if progress is None:
            raise PriceStatusError('month-completed progress missing')
        expected_next = _next_month_start(determined)
        if nxt is None or abs((nxt - expected_next).total_seconds()) > 60:
            raise PriceStatusError('month-completed nextEligibleAt invalid')
    elif receipt['status'] == 'waiting-eligible':
        if receipt.get('cycleId') is not None or receipt.get('stage') is not None:
            raise PriceStatusError('waiting receipt must not claim active cycle')
        if receipt.get('safeReason') == 'new-model-demand':
            if nxt is None or nxt > now_dt + _dt.timedelta(minutes=5):
                raise PriceStatusError('waiting-eligible nextEligibleAt invalid')
        else:
            if nxt is None or nxt <= now_dt \
                    or nxt > now_dt + _dt.timedelta(seconds=max_cooldown_seconds):
                raise PriceStatusError('waiting cooldown invalid')
    elif receipt['status'] == 'no-demand':
        if receipt.get('cycleId') is not None or receipt.get('stage') is not None:
            raise PriceStatusError('no-demand receipt must not claim cycle')
        if nxt is not None:
            raise PriceStatusError('no-demand nextEligibleAt must be null')
    else:
        if receipt.get('cycleId') is not None or receipt.get('stage') is not None:
            raise PriceStatusError('idle receipt must not claim active cycle')
    ev = receipt.get('evidence')
    if not isinstance(ev, dict):
        raise PriceStatusError('evidence missing')
    required = {'snapshotHash', 'metaHash', 'demandHash', 'biggoStatusHash',
                'completedSyncHash', 'anchorHash', 'bundleHash', 'stageResultHash',
                'postStateHash', 'applyReceiptHash', 'cooldownHash',
                'pendingDemandCount', 'unknownDemandCount'}
    if set(ev) != required:
        raise PriceStatusError('evidence keys invalid')
    for key in ('metaHash', 'demandHash', 'biggoStatusHash'):
        if not _is_sha(ev.get(key)):
            raise PriceStatusError(f'evidence.{key} invalid')
    if receipt['status'] != 'blocked' and not _is_sha(ev.get('snapshotHash')):
        raise PriceStatusError('evidence.snapshotHash required')
    for key in ('snapshotHash', 'completedSyncHash', 'anchorHash', 'bundleHash',
                'stageResultHash', 'postStateHash', 'applyReceiptHash',
                'cooldownHash'):
        if ev.get(key) is not None and not _is_sha(ev.get(key)):
            raise PriceStatusError(f'evidence.{key} invalid')
    for field in ('pendingDemandCount', 'unknownDemandCount'):
        if ev.get(field) is not None and (
                isinstance(ev[field], bool)
                or not isinstance(ev[field], int) or ev[field] < 0):
            raise PriceStatusError(f'evidence.{field} invalid')
    if receipt['status'] in ('no-demand', 'waiting-eligible') \
            and ev.get('pendingDemandCount') is None:
        raise PriceStatusError('demand evidence missing')
    if receipt['status'] == 'no-demand' and ev.get('pendingDemandCount') != 0:
        raise PriceStatusError('no-demand with pending demand')
    if receipt['status'] == 'waiting-eligible' \
            and not (isinstance(ev.get('pendingDemandCount'), int)
                     and ev['pendingDemandCount'] > 0):
        raise PriceStatusError('waiting without pending demand')
    if receipt['status'] == 'month-completed':
        if not _is_sha(ev.get('bundleHash')):
            raise PriceStatusError('verified progress bundle evidence missing')
        if not _is_sha(ev.get('postStateHash')) \
                or not _is_sha(ev.get('applyReceiptHash')):
            raise PriceStatusError('completed apply/post-state evidence missing')
    if receipt['status'] == 'active' and receipt.get('stage', 0) > 0 \
            and not _is_sha(ev.get('bundleHash')):
        raise PriceStatusError('active stage bundle evidence missing')
    if receipt.get('safeReason') == 'cooldown' and not _is_sha(ev.get('cooldownHash')):
        raise PriceStatusError('cooldown witness evidence missing')
    body = {k: v for k, v in receipt.items() if k != 'receiptHash'}
    if receipt.get('receiptHash') != sha256_id(canonical_json_bytes(body)):
        raise PriceStatusError('receiptHash mismatch')
    return receipt


def verify_receipt(receipt, *, inputs, now=None, max_age_hours=MAX_AGE_HOURS,
                   max_cooldown_seconds=MAX_COOLDOWN_SECONDS):
    """Rebuild from the supplied (caller re-read) inputs and require identity.

    This function cannot know whether the caller reused an in-memory dict; the
    monitor/CLI must perform a second actual disk read and pass that snapshot.
    """
    validate_receipt(receipt, now=now, max_age_hours=max_age_hours,
                     max_cooldown_seconds=max_cooldown_seconds)
    when = _parse(receipt.get('determinedAt'))
    rebuilt = build_receipt(now=when, max_age_hours=max_age_hours,
                            max_cooldown_seconds=max_cooldown_seconds,
                            **(inputs or {}))
    if rebuilt != receipt:
        raise PriceStatusError('receipt does not match actual files')
    return receipt


def receipt_summary(receipt):
    validate_receipt(receipt)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'kind': KIND,
        'status': receipt['status'],
        'safeReason': receipt['safeReason'],
        'healthy': receipt['healthy'],
        'determinedAt': receipt['determinedAt'],
        'lastVerifiedProgressAt': receipt['lastVerifiedProgressAt'],
        'nextEligibleAt': receipt['nextEligibleAt'],
        'cycleId': receipt['cycleId'],
        'stage': receipt['stage'],
        'evidence': dict(receipt['evidence']),
        'receiptHash': receipt['receiptHash'],
    }


def _load(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _snapshot_hash(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            import biggo_canonical
            obj = json.load(f)
        return biggo_canonical.sha256_json(obj)
    except (OSError, ValueError, TypeError):
        return None


def _write_atomic(path, doc):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description='derive/verify price status receipt')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--meta', default=None)
    ap.add_argument('--demand', default=None)
    ap.add_argument('--biggo-status', default=None)
    ap.add_argument('--completed-sync', default=None)
    ap.add_argument('--snapshot', default=None)
    ap.add_argument('--anchor', default=None)
    ap.add_argument('--shared', action='store_true')
    ap.add_argument('--now', default=None, help=argparse.SUPPRESS)
    ap.add_argument('--out', default=None)
    ap.add_argument('--verify', default=None)
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    now = _dt.datetime.fromisoformat(args.now.replace('Z', '+00:00')) \
        if args.now else None
    meta_path = args.meta or os.path.join(repo, 'prices_meta.json')
    demand_path = args.demand or os.path.join(repo, 'price_demand.json')
    biggo_path = args.biggo_status or os.path.join(repo, 'biggo_stage_status.json')
    sync_path = args.completed_sync or os.path.join(repo, 'biggo_completed_sync.json')
    snapshot_path = args.snapshot or os.path.join(repo, 'biggo_prices.json')
    meta = _load(meta_path)
    demand = _load(demand_path)
    if os.path.exists(demand_path) and demand is None:
        print('❌ price_demand.json unreadable', file=sys.stderr)
        return 2
    biggo = _load(biggo_path) or {}
    sync = _load(sync_path) or {}
    anchor = _load(args.anchor) if args.anchor else None
    tracking = _load(os.path.join(repo, 'model_status.json'))
    blacklist = _load(os.path.join(repo, 'model_blacklist.json'))
    models = (blacklist or {}).get('models') if isinstance(blacklist, dict) else None
    post_state = {
        'trackingHash': sha256_json(tracking) if isinstance(tracking, dict) else None,
        'blacklistHash': sha256_json(models) if isinstance(models, dict) else None,
    }
    inputs = {'meta': meta or {}, 'demand': demand,
              'biggo': biggo, 'completed_sync': sync,
              'snapshot_hash': _snapshot_hash(snapshot_path),
              'anchor': anchor, 'shared': args.shared,
              'post_state': post_state}
    if args.verify:
        receipt = _load(args.verify)
        try:
            verify_receipt(receipt, inputs=inputs, now=now)
        except PriceStatusError as e:
            print(f'❌ price status verify failed: {e}', file=sys.stderr)
            return 1
        print('✅ price status receipt matches actual files')
        return 0
    receipt = build_receipt(now=now, **inputs)
    validate_receipt(receipt, now=now)
    if args.out:
        _write_atomic(args.out, receipt)
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0 if receipt['status'] != 'blocked' else 1


if __name__ == '__main__':
    sys.exit(main())
