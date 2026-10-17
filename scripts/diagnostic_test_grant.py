#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fixed-path, human-authorized, five-request BigGo diagnostic grant ledger.

This is deliberately a small local-test authorization ledger, not a provider
quota service.  It lives beside the coordinator state file, has one immutable
authorization identity, and only records reserved/actual diagnostic requests.
It never reserves the production stage token/search counters.
"""
from __future__ import annotations

import copy
import hashlib
import posixpath

from biggo_canonical import canonical_json_bytes, sha256_id

SCHEMA_VERSION = 1
KIND = 'biggo-diagnostic-test-grant-v1'
AUTH_KIND = 'biggo-diagnostic-test-authorization-v1'
LEDGER_NAME = 'diagnostic-test-budget.json'
HARD_MAXIMUM = 5
RESERVED = {'token': 1, 'search': 1}
STATES = frozenset({'reserved', 'running', 'completed', 'uncertain'})
AUTH_FIELDS = ('authorizationFileHash', 'authorizedAt', 'userInstruction',
               'scope', 'hardMaximum', 'includes', 'newPriceCycle',
               'fullBatch', 'priceOrLifecycleApply',
               'priorCoordinatorIncidentNotRetroactivelyAuthorized')


class GrantError(RuntimeError):
    pass


def fixed_ledger_path(config):
    """Only the state-file directory is accepted; no workflow-selectable path."""
    state_path = str(getattr(config, 'state_path', '') or '')
    if not state_path or state_path.endswith('/'):
        raise GrantError('coordinator state path invalid')
    directory = posixpath.dirname(state_path)
    if directory in ('', '.'):
        return LEDGER_NAME
    return directory + '/' + LEDGER_NAME


def _sha(obj):
    return sha256_id(canonical_json_bytes(obj))


def canonical_ledger_bytes(ledger):
    validate_ledger(ledger)
    return canonical_json_bytes(ledger)


def _int(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GrantError('non-negative integer required')
    return value


def validate_authorization(auth):
    if not isinstance(auth, dict) or auth.get('kind') != AUTH_KIND:
        raise GrantError('authorization kind invalid')
    if auth.get('schemaVersion') != 1:
        raise GrantError('authorization schema invalid')
    if auth.get('hardMaximum') != HARD_MAXIMUM:
        raise GrantError('authorization hardMaximum must equal 5')
    for field in AUTH_FIELDS:
        if field not in auth:
            raise GrantError(f'authorization missing {field}')
    claimed = auth.get('grantId')
    body = {k: v for k, v in auth.items() if k != 'grantId'}
    if claimed != _sha(body):
        raise GrantError('authorization grantId mismatch')
    if not isinstance(auth.get('authorizationFileHash'), str) \
            or not auth['authorizationFileHash'].startswith('sha256:'):
        raise GrantError('authorizationFileHash invalid')
    return claimed


def make_authorization(auth, authorization_file_hash):
    body = copy.deepcopy(auth)
    body.pop('grantId', None)
    body.setdefault('schemaVersion', 1)
    body.setdefault('kind', AUTH_KIND)
    body['authorizationFileHash'] = authorization_file_hash
    body['grantId'] = _sha(body)
    return body


def empty_ledger(auth):
    grant_id = validate_authorization(auth)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'kind': KIND,
        'grantId': grant_id,
        'authorization': copy.deepcopy(auth),
        'reservations': [],
        'hardMaximum': HARD_MAXIMUM,
        'reserved': {'token': 0, 'search': 0},
        'actual': {'token': 0, 'search': 0},
    }


def _reservation_view(row):
    if not isinstance(row, dict):
        raise GrantError('reservation row invalid')
    attempt = row.get('attemptId')
    if not isinstance(attempt, str) or not attempt or len(attempt) > 160:
        raise GrantError('reservation attemptId invalid')
    reserved = row.get('reserved')
    actual = row.get('actual')
    for label, value in (('reserved', reserved), ('actual', actual)):
        if not isinstance(value, dict):
            raise GrantError(f'{label} invalid')
        _int(value.get('token'))
        _int(value.get('search'))
    if reserved != RESERVED:
        raise GrantError('each diagnostic reservation must be exactly 1 token + 1 search')
    if row.get('state') not in STATES:
        raise GrantError('reservation state invalid')
    return row


def validate_ledger(ledger, expected_grant_id=None):
    if not isinstance(ledger, dict) or ledger.get('kind') != KIND \
            or ledger.get('schemaVersion') != SCHEMA_VERSION:
        raise GrantError('ledger schema invalid')
    auth = ledger.get('authorization')
    grant_id = validate_authorization(auth)
    if ledger.get('grantId') != grant_id:
        raise GrantError('ledger grantId mismatch')
    if expected_grant_id is not None and expected_grant_id != grant_id:
        raise GrantError('grant id mismatch')
    if ledger.get('hardMaximum') != HARD_MAXIMUM:
        raise GrantError('ledger hardMaximum invalid')
    rows = ledger.get('reservations')
    if not isinstance(rows, list):
        raise GrantError('reservations must be a list')
    seen = set()
    reserved = {'token': 0, 'search': 0}
    actual = {'token': 0, 'search': 0}
    for row in rows:
        row = _reservation_view(row)
        if row['attemptId'] in seen:
            raise GrantError('duplicate attemptId')
        seen.add(row['attemptId'])
        for kind in ('token', 'search'):
            reserved[kind] += row['reserved'][kind]
            actual[kind] += row['actual'][kind]
    if reserved != ledger.get('reserved') or actual != ledger.get('actual'):
        raise GrantError('ledger totals mismatch')
    if reserved['token'] + reserved['search'] > HARD_MAXIMUM:
        raise GrantError('reserved total exceeds hard maximum')
    if actual['token'] + actual['search'] > reserved['token'] + reserved['search']:
        raise GrantError('actual exceeds reserved')
    return ledger


def remaining(ledger):
    validate_ledger(ledger)
    used = ledger['reserved']['token'] + ledger['reserved']['search']
    return HARD_MAXIMUM - used


def reserve_attempt(ledger, *, attempt_id, host, reserved_at):
    validate_ledger(ledger)
    if any(r.get('attemptId') == attempt_id for r in ledger['reservations']):
        raise GrantError('attempt already reserved')
    if remaining(ledger) < RESERVED['token'] + RESERVED['search']:
        raise GrantError('test grant exhausted or remaining < 2')
    row = {'attemptId': attempt_id, 'host': host, 'state': 'reserved',
           'reservedAt': int(reserved_at), 'reserved': dict(RESERVED),
           'actual': {'token': 0, 'search': 0}, 'httpStatus': None,
           'reason': None, 'retryAfter': None}
    new = copy.deepcopy(ledger)
    new['reservations'].append(row)
    for kind in ('token', 'search'):
        new['reserved'][kind] += RESERVED[kind]
    validate_ledger(new)
    return new


def finish_attempt(ledger, *, attempt_id, actual, http_status=None, reason=None,
                   retry_after=None):
    validate_ledger(ledger)
    rows = ledger['reservations']
    matches = [r for r in rows if r.get('attemptId') == attempt_id]
    if len(matches) != 1:
        raise GrantError('attempt not found')
    row = matches[0]
    if row.get('state') != 'reserved':
        raise GrantError('attempt is not reserved')
    for kind in ('token', 'search'):
        _int(actual.get(kind))
        if actual[kind] > RESERVED[kind]:
            raise GrantError('actual exceeds per-attempt reserve')
    new = copy.deepcopy(ledger)
    target = next(r for r in new['reservations'] if r.get('attemptId') == attempt_id)
    target.update({'state': 'completed', 'actual': dict(actual),
                   'httpStatus': http_status, 'reason': reason,
                   'retryAfter': retry_after})
    for kind in ('token', 'search'):
        new['actual'][kind] += actual[kind]
    validate_ledger(new)
    return new


def mark_uncertain(ledger, *, attempt_id, reason):
    validate_ledger(ledger)
    new = copy.deepcopy(ledger)
    target = next((r for r in new['reservations']
                   if r.get('attemptId') == attempt_id), None)
    if target is None:
        raise GrantError('attempt not found')
    if target.get('state') == 'completed':
        raise GrantError('completed attempt cannot become uncertain')
    target['state'] = 'uncertain'
    target['reason'] = str(reason)[:80]
    validate_ledger(new)
    return new
