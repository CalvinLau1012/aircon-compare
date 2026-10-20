#!/usr/bin/env python3
"""Create a run-bound BigGo failure receipt without claiming provider success.

Existing status is preserved only when it has a small, producer-compatible
schema.  A corrupt existing object is never treated as evidence; an upstream
fallback is refused, while an actually attempted provider phase may be
replaced by an explicit UNKNOWN receipt after preserving the original bytes
in a sibling `.invalid` file (not uploaded as trusted status).
"""
import argparse
import json
import os
import re
import sys
import time

_ALERT_STATUSES = {
    'needs-review', 'blocked-intent-persist', 'completed-apply-failed',
    'completed-legacy-manual', 'completed-bundle-unavailable',
    'blocked-incomplete', 'blocked-diagnostic-pending',
    'blocked-quota-window-unsupported', 'blocked-stage-gap',
    'blocked-force-cycle-incomplete', 'blocked-stale',
    'blocked-upstream', 'blocked-provider-unknown',
}
_KNOWN_STATUSES = _ALERT_STATUSES | {
    'skip-not-active', 'skip-cooldown', 'skip-budget', 'skip-completed-ahead',
    'skip-smoke-no-calls', 'skip-batch-no-calls', 'skip-coordinator-not-configured',
    'skip-coordinator-config-invalid', 'skip-lost-active', 'skip-no-eligible-candidate',
    'skip-stale-writer', 'skipped-no-provider', 'completed', 'completed-recovered',
    'completed-idempotent-applied', 'blocked-candidate-selection',
    'blocked-coordinator-error', 'blocked-meta-invalid', 'error', 'unknown',
}
_EVENTS = {'schedule', 'workflow_dispatch'}
_STATUS_RE = re.compile(r'^[a-z][a-z0-9-]{0,79}$')
_HEAD_RE = re.compile(r'^[0-9a-f]{40}$')
_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')


def _valid_identity(run_id, head, event):
    return (isinstance(run_id, str) and re.fullmatch(r'[0-9]+', run_id)
            and isinstance(head, str) and _HEAD_RE.fullmatch(head)
            and event in _EVENTS)


def _valid_int(value):
    return type(value) is int and value >= 0


def _valid_requests(value):
    return (isinstance(value, dict) and set(value) == {'token', 'search'}
            and _valid_int(value['token']) and _valid_int(value['search']))


def _validate_bound_identity(data):
    present = [k for k in ('sourceRunId', 'headSha', 'sourceEvent') if k in data]
    if not present:
        return True, 'unbound'
    if len(present) != 3:
        return False, 'partial-identity'
    run_id, head, event = data['sourceRunId'], data['headSha'], data['sourceEvent']
    if type(run_id) is not int or run_id <= 0:
        return False, 'run-id-type'
    if not isinstance(head, str) or not _HEAD_RE.fullmatch(head):
        return False, 'head-type'
    if event not in _EVENTS:
        return False, 'event-type'
    return True, 'bound'


def validate_existing_status(data):
    """Return (ok, reason) for a producer-compatible existing BigGo status.

    This deliberately accepts the old runner's lack of source identity while
    rejecting empty/arbitrary objects, unknown statuses, malformed alert or
    request counters, and mismatched/invalid identity fields.
    """
    if not isinstance(data, dict):
        return False, 'not-object'
    if type(data.get('schemaVersion')) is not int or data.get('schemaVersion') != 1:
        return False, 'schema'
    status = data.get('status')
    if status == 'blocked-provider-unknown':
        if data.get('mode') != 'provider-phase-failure' \
                or data.get('failureScope') != 'provider-phase' \
                or data.get('alert') is not True:
            return False, 'unknown-receipt'
        if (data.get('providerPhaseEntered') is not None
                or data.get('leaseAcquired') is not None
                or data.get('requests') is not None):
            return False, 'unknown-facts'
        ok, why = _validate_bound_identity(data)
        return (True, 'ok') if ok else (False, why)
    if not isinstance(status, str) or status not in _KNOWN_STATUSES:
        return False, 'status'
    if type(data.get('alert')) is not bool:
        return False, 'alert'
    if data.get('alert') is not (status in _ALERT_STATUSES):
        return False, 'alert-consistency'
    if not _valid_requests(data.get('requests')):
        return False, 'requests'
    if 'generatedAt' in data and (not isinstance(data['generatedAt'], str)
                                  or not _TS_RE.fullmatch(data['generatedAt'])):
        return False, 'generatedAt'
    ok, why = _validate_bound_identity(data)
    if not ok:
        return False, why
    return True, 'ok'


def validate_upstream_receipt(data, expected_run_id=None, expected_head=None,
                              expected_event=None):
    """Validate a fallback receipt before any trusted zero-request message."""
    if not isinstance(data, dict):
        return False, 'not-object'
    if type(data.get('schemaVersion')) is not int or data.get('schemaVersion') != 1:
        return False, 'schema'
    if data.get('status') != 'blocked-upstream':
        return False, 'status'
    if data.get('mode') != 'upstream-workflow-failure':
        return False, 'mode'
    if data.get('failureScope') != 'upstream-workflow':
        return False, 'failure-scope'
    if data.get('alert') is not True:
        return False, 'alert'
    if data.get('reason') != 'upstream-workflow-failed':
        return False, 'reason'
    if data.get('providerPhaseEntered') is not False \
            or data.get('leaseAcquired') is not False:
        return False, 'provider-or-lease'
    if not _valid_requests(data.get('requests')) or data['requests'] != {'token': 0, 'search': 0}:
        return False, 'requests'
    if data.get('published') is not False or data.get('imported') is not False:
        return False, 'published-or-imported'
    if data.get('localApply') is not None:
        return False, 'local-apply'
    ok, why = _validate_bound_identity(data)
    if not ok:
        return False, why
    if expected_run_id is not None and str(data.get('sourceRunId')) != str(expected_run_id):
        return False, 'expected-run-id'
    if expected_head is not None and data.get('headSha') != expected_head:
        return False, 'expected-head'
    if expected_event is not None and data.get('sourceEvent') != expected_event:
        return False, 'expected-event'
    if expected_run_id is None or expected_head is None:
        return False, 'missing-expected-identity'
    return True, 'ok'


def validate_provider_unknown_receipt(data, expected_run_id=None, expected_head=None):
    if not isinstance(data, dict):
        return False, 'not-object'
    if type(data.get('schemaVersion')) is not int or data.get('schemaVersion') != 1:
        return False, 'schema'
    if data.get('status') != 'blocked-provider-unknown':
        return False, 'status'
    if data.get('mode') != 'provider-phase-failure':
        return False, 'mode'
    if data.get('failureScope') != 'provider-phase':
        return False, 'failure-scope'
    if data.get('alert') is not True:
        return False, 'alert'
    if data.get('providerPhaseEntered') is not None \
            or data.get('leaseAcquired') is not None \
            or data.get('requests') is not None:
        return False, 'unknown-facts'
    ok, why = _validate_bound_identity(data)
    if not ok:
        return False, why
    if expected_run_id is not None and str(data.get('sourceRunId')) != str(expected_run_id):
        return False, 'expected-run-id'
    if expected_head is not None and data.get('headSha') != expected_head:
        return False, 'expected-head'
    if expected_run_id is None or expected_head is None:
        return False, 'missing-expected-identity'
    return True, 'ok'


def _atomic_write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def build(kind, run_id, head, event, generated_at):
    if not _valid_identity(run_id, head, event):
        raise ValueError('invalid run identity')
    if kind == 'upstream-skipped':
        return {'schemaVersion': 1, 'status': 'blocked-upstream', 'alert': True,
                'mode': 'upstream-workflow-failure', 'failureScope': 'upstream-workflow',
                'reason': 'upstream-workflow-failed', 'sourceRunId': int(run_id),
                'sourceEvent': event, 'headSha': head,
                'providerPhaseEntered': False, 'leaseAcquired': False,
                'published': False, 'imported': False, 'localApply': None,
                'requests': {'token': 0, 'search': 0}, 'generatedAt': generated_at}
    if kind == 'provider-phase-unknown':
        return {'schemaVersion': 1, 'status': 'blocked-provider-unknown', 'alert': True,
                'mode': 'provider-phase-failure', 'failureScope': 'provider-phase',
                'reason': 'provider-status-missing-or-invalid',
                'sourceRunId': int(run_id), 'sourceEvent': event, 'headSha': head,
                'providerPhaseEntered': None, 'leaseAcquired': None,
                'published': None, 'imported': None, 'localApply': None,
                'requests': None, 'generatedAt': generated_at}
    raise ValueError('invalid failure kind')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--kind', choices=('upstream-skipped', 'provider-phase-unknown'),
                    required=True)
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--head', required=True)
    ap.add_argument('--event', required=True)
    ap.add_argument('--generated-at', default=None)
    a = ap.parse_args(argv)
    if not _valid_identity(a.run_id, a.head, a.event):
        print('❌ invalid run identity', file=sys.stderr)
        return 2
    generated = a.generated_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if os.path.lexists(a.out):
        raw = None
        try:
            with open(a.out, 'rb') as f:
                raw = f.read()
            existing = json.loads(raw.decode('utf-8'))
        except (OSError, ValueError, UnicodeDecodeError):
            existing = None
        valid, why = validate_existing_status(existing)
        if valid:
            # Existing genuine producer evidence is authoritative.
            return 0
        if a.kind == 'upstream-skipped':
            print(f'❌ existing status is not valid ({why}); refusing upstream fallback',
                  file=sys.stderr)
            return 2
        # Provider was actually attempted.  Preserve original diagnostic bytes
        # beside the status, then emit explicit UNKNOWN; never turn it into 0.
        try:
            if raw is None:
                raw = open(a.out, 'rb').read()
            with open(a.out + '.invalid', 'wb') as f:
                f.write(raw)
                f.flush(); os.fsync(f.fileno())
            _atomic_write(a.out, build(a.kind, a.run_id, a.head, a.event, generated))
        except OSError as e:
            print(f'❌ failure status write failed: {type(e).__name__}', file=sys.stderr)
            return 2
        return 0
    try:
        _atomic_write(a.out, build(a.kind, a.run_id, a.head, a.event, generated))
    except OSError as e:
        print(f'❌ failure status write failed: {type(e).__name__}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
