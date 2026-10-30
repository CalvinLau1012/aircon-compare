#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared BigGo acquisition-time witness contract.

The witness is recorded when a provider response has been received and its
price payload validated.  It is not the batch report clock, the stage-result
generation clock, the bundle publication clock, the local apply clock or the
deployment clock.

The block is carried by the immutable stage-result (and therefore covered by
stageResultHash/manifest/bundleHash).  It binds the current cycle/stage,
base/new snapshot hashes and each valid outcome's exact result hash.  A block
that exists but is incomplete, malformed, future-dated or inconsistent fails
closed; an absent block is legacy/unknown, not a success witness.

Both public and private candidates import this same module.  No hostnames,
paths, tokens or provider response bodies are stored here.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time

KIND = 'biggo-provider-acquisition-v1'
SCHEMA_VERSION = 1
BASIS = 'provider-response-validated'
PRECISION = '1s'
VALID_OUTCOMES = ('priced', 'clean_miss')
_BLOCK_KEYS = {
    'schemaVersion', 'kind', 'basis', 'precision', 'cycleId', 'stage',
    'baseSnapshotHash', 'newSnapshotHash', 'batchStartedAt', 'lastValidResultAt',
    'pricedCount', 'cleanMissCount', 'results',
}
_ENTRY_KEYS = {'model', 'outcome', 'resultHash', 'acquiredAt'}
_UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
_SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')


class AcquisitionError(ValueError):
    pass


def utc_stamp(now=None):
    """Format an explicit realtime sample at the witness's one-second precision.

    Always pass a timestamp to gmtime: its implicit clock can lag time.time()
    at a second boundary and make a response appear older than batch start.
    """
    return time.strftime('%Y-%m-%dT%H:%M:%SZ',
                         time.gmtime(time.time() if now is None else now))


def _parse(value):
    if not isinstance(value, str) or not _UTC_RE.match(value):
        return None
    try:
        return _dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def is_sha(value):
    return isinstance(value, str) and bool(_SHA_RE.match(value))


def result_hash(price):
    """Canonical hash of the extracted provider result (None for clean miss)."""
    from biggo_canonical import sha256_json
    return sha256_json(price)


def validate_window(window, generated_at):
    """A present collector marker must have a complete chronological window."""
    if not isinstance(window, dict):
        raise AcquisitionError('acquisition window must be an object')
    started = _parse(window.get('startedAt'))
    finished = _parse(window.get('finishedAt'))
    generated = _parse(generated_at)
    if started is None or finished is None or generated is None:
        raise AcquisitionError('acquisition window time missing/invalid')
    if started > finished or finished > generated + _dt.timedelta(minutes=5):
        raise AcquisitionError('acquisition window chronology invalid')
    return window


def build_block(*, cycle_id, stage, base_snapshot, new_snapshot, outcomes,
                batch_started_at, generated_at, require_complete=False):
    """Build a witness block, or return None for legacy/incomplete evidence.

    Callers may pass mock outcomes without acquisition records (legacy tests or
    old imports); those must remain unknown rather than inventing a time.
    Production collectors attach ``acquiredAt``/``resultHash`` to every valid
    outcome before the stage result is published.
    """
    from biggo_canonical import sha256_json

    def invalid(reason):
        if require_complete:
            raise AcquisitionError(reason)
        return None
    if not isinstance(cycle_id, str) or not cycle_id:
        return invalid('acquisition cycle invalid')
    if isinstance(stage, bool) or not isinstance(stage, int) or stage < 1:
        return invalid('acquisition stage invalid')
    if not isinstance(outcomes, list) or not outcomes:
        return invalid('acquisition outcomes missing')
    if _parse(batch_started_at) is None or _parse(generated_at) is None:
        return invalid('acquisition batch/generation time invalid')
    started = _parse(batch_started_at)
    generated = _parse(generated_at)
    entries = []
    priced = clean = 0
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return invalid('acquisition outcome invalid')
        kind = outcome.get('outcome')
        if kind not in VALID_OUTCOMES:
            continue
        model = outcome.get('model')
        acquired = outcome.get('acquiredAt')
        rhash = outcome.get('resultHash')
        when = _parse(acquired)
        if not isinstance(model, str) or not model.strip() or when is None:
            return invalid('valid outcome missing/invalid acquisition timestamp')
        if not is_sha(rhash):
            return invalid('valid outcome missing/invalid result hash')
        expected_hash = result_hash(outcome.get('price') if kind == 'priced' else None)
        if rhash != expected_hash:
            return invalid('acquisition result hash mismatch')
        if when < started or when > generated + _dt.timedelta(minutes=5):
            return invalid('acquisition result outside batch window')
        entries.append({'model': model, 'outcome': kind,
                        'resultHash': rhash, 'acquiredAt': acquired})
        if kind == 'priced':
            priced += 1
        else:
            clean += 1
    if not entries:
        return invalid('no valid acquisition outcomes')
    last = max(entry['acquiredAt'] for entry in entries)
    block = {
        'schemaVersion': SCHEMA_VERSION,
        'kind': KIND,
        'basis': BASIS,
        'precision': PRECISION,
        'cycleId': cycle_id,
        'stage': stage,
        'baseSnapshotHash': sha256_json(base_snapshot),
        'newSnapshotHash': sha256_json(new_snapshot),
        'batchStartedAt': batch_started_at,
        'lastValidResultAt': last,
        'pricedCount': priced,
        'cleanMissCount': clean,
        'results': entries,
    }
    validate_block(block, outcomes=outcomes, cycle_id=cycle_id, stage=stage,
                   base_snapshot=base_snapshot, new_snapshot=new_snapshot,
                   generated_at=generated_at, now=_parse(generated_at).timestamp())
    return block


def validate_block(block, *, outcomes, cycle_id=None, stage=None,
                   base_snapshot=None, new_snapshot=None, generated_at=None,
                   now=None):
    """Validate a witness against the exact stage result it accompanies."""
    from biggo_canonical import sha256_json
    if not isinstance(block, dict) or set(block) != _BLOCK_KEYS:
        raise AcquisitionError('acquisition block keys invalid')
    if block.get('schemaVersion') != SCHEMA_VERSION or block.get('kind') != KIND:
        raise AcquisitionError('acquisition schema/kind invalid')
    if block.get('basis') != BASIS or block.get('precision') != PRECISION:
        raise AcquisitionError('acquisition basis/precision invalid')
    if not isinstance(block.get('cycleId'), str) or not block['cycleId']:
        raise AcquisitionError('acquisition cycleId invalid')
    if isinstance(block.get('stage'), bool) or not isinstance(block.get('stage'), int) \
            or block['stage'] < 1:
        raise AcquisitionError('acquisition stage invalid')
    if cycle_id is not None and block['cycleId'] != cycle_id:
        raise AcquisitionError('acquisition cycle mismatch')
    if stage is not None and block['stage'] != stage:
        raise AcquisitionError('acquisition stage mismatch')
    for field in ('baseSnapshotHash', 'newSnapshotHash'):
        if not is_sha(block.get(field)):
            raise AcquisitionError(f'acquisition {field} invalid')
    if base_snapshot is not None and block['baseSnapshotHash'] != sha256_json(base_snapshot):
        raise AcquisitionError('acquisition base snapshot mismatch')
    if new_snapshot is not None and block['newSnapshotHash'] != sha256_json(new_snapshot):
        raise AcquisitionError('acquisition new snapshot mismatch')
    started = _parse(block.get('batchStartedAt'))
    last = _parse(block.get('lastValidResultAt'))
    if started is None or last is None or started > last:
        raise AcquisitionError('acquisition time window invalid')
    generated = _parse(generated_at) if generated_at is not None else None
    if generated_at is not None and generated is None:
        raise AcquisitionError('acquisition generatedAt invalid')
    if generated is not None and last > generated + _dt.timedelta(minutes=5):
        raise AcquisitionError('acquisition result after generation')
    if now is not None:
        now_dt = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        if last > now_dt + _dt.timedelta(minutes=5):
            raise AcquisitionError('acquisition time is in the future')
    if not isinstance(block.get('results'), list) or not block['results']:
        raise AcquisitionError('acquisition results missing')
    valid = [o for o in outcomes if isinstance(o, dict)
             and o.get('outcome') in VALID_OUTCOMES]
    expected = {}
    for outcome in valid:
        model = outcome.get('model')
        if not isinstance(model, str) or not model:
            raise AcquisitionError('outcome model invalid')
        if model in expected:
            raise AcquisitionError('duplicate valid outcome')
        expected[model] = outcome
    if len(block['results']) != len(expected):
        raise AcquisitionError('acquisition result coverage mismatch')
    seen = set()
    priced = clean = 0
    times = []
    for entry in block['results']:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise AcquisitionError('acquisition result entry invalid')
        model = entry.get('model')
        if not isinstance(model, str) or model in seen or model not in expected:
            raise AcquisitionError('acquisition result model mismatch')
        seen.add(model)
        outcome = expected[model]
        if entry.get('outcome') != outcome.get('outcome'):
            raise AcquisitionError('acquisition result outcome mismatch')
        want = result_hash(outcome.get('price')
                           if outcome.get('outcome') == 'priced' else None)
        if entry.get('resultHash') != want:
            raise AcquisitionError('acquisition result hash mismatch')
        when = _parse(entry.get('acquiredAt'))
        if when is None:
            raise AcquisitionError('acquisition timestamp invalid')
        if when < started or (generated is not None
                              and when > generated + _dt.timedelta(minutes=5)):
            raise AcquisitionError('acquisition timestamp outside batch window')
        if now is not None and when > _dt.datetime.fromtimestamp(
                now, _dt.timezone.utc) + _dt.timedelta(minutes=5):
            raise AcquisitionError('acquisition timestamp is in the future')
        times.append(when)
        if entry['outcome'] == 'priced':
            priced += 1
        else:
            clean += 1
    if not times or max(times) != last:
        raise AcquisitionError('acquisition lastValidResultAt mismatch')
    if block.get('pricedCount') != priced or block.get('cleanMissCount') != clean:
        raise AcquisitionError('acquisition counts mismatch')
    return block


def from_stage_result(stage_result, *, now=None):
    """Return a verified witness summary or an explicit unknown result.

    A present but invalid block raises AcquisitionError.  An absent block is
    legacy evidence and must stay unknown; it is never back-filled from any
    other clock.
    """
    if not isinstance(stage_result, dict):
        raise AcquisitionError('stage-result must be an object')
    block = stage_result.get('acquisition')
    if block is None:
        return {'acquiredAt': None, 'provenance': 'unknown', 'blockHash': None,
                'basis': None, 'precision': None}
    validate_block(block, outcomes=stage_result.get('outcomes') or [],
                   cycle_id=stage_result.get('cycleId'),
                   stage=stage_result.get('stage'),
                   generated_at=stage_result.get('generatedAt'), now=now)
    from biggo_canonical import sha256_json
    return {
        'acquiredAt': block['lastValidResultAt'],
        'provenance': 'recorded',
        'blockHash': sha256_json(block),
        'basis': block['basis'],
        'precision': block['precision'],
        'windowStart': block['batchStartedAt'],
        'windowEnd': block['lastValidResultAt'],
        'pricedCount': block['pricedCount'],
        'cleanMissCount': block['cleanMissCount'],
    }


def baseline_acquisition(source, stage_result=None):
    """Return a baseline witness only if it matches the immutable bundle.

    ``stage_result`` is optional for format-only callers, but receiver/baseline
    validation must pass it.  A source cannot invent ``recorded`` without the
    exact stage-result block/hash/time; a legacy bundle without a block cannot
    claim a recorded time.
    """
    if not isinstance(source, dict):
        return {'acquiredAt': None, 'provenance': 'unknown', 'blockHash': None}
    provenance = source.get('acquisitionProvenance')
    block = None
    if stage_result is not None:
        block = stage_result.get('acquisition') if isinstance(stage_result, dict) else None
    if provenance != 'recorded':
        if provenance not in (None, 'unknown'):
            raise AcquisitionError('baseline acquisition provenance invalid')
        if block is not None:
            raise AcquisitionError('baseline has witness but source claims unknown')
        return {'acquiredAt': None, 'provenance': 'unknown', 'blockHash': None}
    acquired = source.get('acquiredAt')
    claimed_hash = source.get('acquisitionHash')
    if _parse(acquired) is None or not is_sha(claimed_hash):
        raise AcquisitionError('baseline acquisition evidence invalid')
    if stage_result is None:
        return {'acquiredAt': acquired, 'provenance': 'recorded',
                'blockHash': claimed_hash}
    if not isinstance(block, dict):
        raise AcquisitionError('baseline claims recorded without immutable witness')
    try:
        validate_block(block, outcomes=stage_result.get('outcomes') or [],
                       cycle_id=stage_result.get('cycleId'),
                       stage=stage_result.get('stage'),
                       generated_at=stage_result.get('generatedAt'))
    except AcquisitionError as exc:
        raise AcquisitionError(f'baseline immutable witness invalid: {exc}') from exc
    from biggo_canonical import sha256_json
    if block.get('lastValidResultAt') != acquired \
            or sha256_json(block) != claimed_hash:
        raise AcquisitionError('baseline witness does not match stage-result')
    return {'acquiredAt': acquired, 'provenance': 'recorded',
            'blockHash': claimed_hash}


def canonical_receiver_facts(report):
    """Validate/normalize producer receiver facts.

    The verified inner post-image is canonical.  Optional outer redundant
    fields are accepted only when exactly equal to it; an outer clock can never
    override a verified inner witness.
    """
    if not isinstance(report, dict):
        raise AcquisitionError('receiver report must be object')
    verified = report.get('verifiedPost')
    if not isinstance(verified, dict):
        raise AcquisitionError('receiver verifiedPost missing')
    acquired = verified.get('acquiredAt')
    provenance = verified.get('acquiredAtProvenance')
    block_hash = verified.get('acquisitionHash')
    if provenance == 'recorded':
        if _parse(acquired) is None or not is_sha(block_hash):
            raise AcquisitionError('receiver recorded witness incomplete')
    elif provenance == 'unknown':
        if acquired is not None or block_hash is not None:
            raise AcquisitionError('receiver unknown witness has time/hash')
    elif provenance is None:
        if acquired is not None or block_hash is not None:
            raise AcquisitionError('receiver witness missing provenance')
    else:
        raise AcquisitionError('receiver witness provenance invalid')
    for field in ('acquiredAt', 'acquiredAtProvenance', 'acquisitionHash'):
        if field in report and report.get(field) != verified.get(field):
            raise AcquisitionError(f'receiver outer {field} conflicts with verifiedPost')
    return {'acquiredAt': acquired, 'acquiredAtProvenance': provenance,
            'acquisitionHash': block_hash}
