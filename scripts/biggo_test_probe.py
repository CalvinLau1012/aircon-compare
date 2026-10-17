#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit, bounded manual BigGo test-grant probe.

Only `--grant-id` can select this mode.  The grant ledger is a fixed path
beside the coordinator state; the workflow cannot provide a path or create a
new grant.  Business stage/status/refs/requestAttempts are preserved.  The
provider phase is at most one token and one search, with no retry/redirect.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_candidate as candidate_mod  # noqa: E402
import biggo_coordinator as coord_mod  # noqa: E402
import diagnostic_test_grant as grant  # noqa: E402
import fetch_biggo as fetch_mod  # noqa: E402
from biggo_limiter import RequestLimiter  # noqa: E402
from biggo_smoke_result import validate_smoke_result  # noqa: E402

OWNER_PREFIX = 'aircon-test-grant-'
CAS_ATTEMPTS = 3
DIAGNOSTIC_FIELD = coord_mod.DIAGNOSTIC_FIELD
RUN_RE = re.compile(r'^[A-Za-z0-9._-]{1,80}$')
GRANT_RE = re.compile(r'^sha256:[0-9a-f]{64}$')


class ProbeBlocked(RuntimeError):
    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(status)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _harden_transport():
    previous = getattr(urllib.request, '_opener', None)
    urllib.request.install_opener(urllib.request.build_opener(
        _NoRedirect(), urllib.request.ProxyHandler({})))
    return previous


def _restore_transport(previous):
    urllib.request._opener = previous


def _stat(value):
    data = value if isinstance(value, dict) else {}
    return {'token': int(data.get('token', 0) or 0),
            'search': int(data.get('search', 0) or 0)}


def _delta(after, before):
    return {'token': max(0, after.get('token', 0) - before.get('token', 0)),
            'search': max(0, after.get('search', 0) - before.get('search', 0))}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _credentials(fetch):
    helper = getattr(fetch, '_credentials_state', None)
    try:
        return helper() if callable(helper) else 'unknown'
    except Exception:
        return 'unknown'


def _marker(owner, attempt_id, cycle_id, stage, now):
    return {'schemaVersion': 1, 'state': 'reserved', 'owner': owner,
            'attemptId': attempt_id, 'cycleId': cycle_id, 'stage': stage,
            'reserved': dict(grant.RESERVED), 'actual': {'token': 0, 'search': 0},
            'reservedAt': int(now), 'completedAt': None, 'reason': None,
            'grantId': None, 'httpStatus': None}


def _read_ledger(contents):
    path = grant.fixed_ledger_path(contents.config)
    data, sha = contents.get_file(path)
    if data is None:
        raise ProbeBlocked('blocked-no-grant', 'grant-ledger-missing')
    try:
        ledger = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        raise ProbeBlocked('blocked-grant-invalid', 'grant-ledger-json')
    try:
        grant.validate_ledger(ledger)
    except grant.GrantError as e:
        raise ProbeBlocked('blocked-grant-invalid', 'grant-ledger-schema')
    return path, ledger, sha


def _quota_configured(contents, state):
    cfg = getattr(contents, 'config', None)
    return (getattr(cfg, 'provider_quota_limit', None) is not None
            or getattr(cfg, 'provider_window_end', None) is not None
            or (isinstance(state, dict)
                and (state.get('providerQuotaLimit') is not None
                     or state.get('providerWindowEnd') is not None)))


def _ledger_cas(contents, path, transform):
    """Read ledger payload+SHA once, transform, CAS write; preserve other rows."""
    for _ in range(CAS_ATTEMPTS):
        try:
            raw, sha = contents.get_file(path)
        except Exception:
            return None, 'uncertain-ledger-read'
        if raw is None:
            return None, 'blocked-grant-missing'
        try:
            ledger = json.loads(raw.decode('utf-8'))
            grant.validate_ledger(ledger)
        except Exception:
            return None, 'blocked-grant-invalid'
        try:
            new_ledger = transform(copy.deepcopy(ledger))
            grant.validate_ledger(new_ledger)
        except grant.GrantError:
            return None, 'blocked-grant-transform'
        # Detect a concurrent ledger update before the PUT, then recompute from
        # the fresh payload/SHA.  The final write never combines old rows with a
        # new SHA.
        try:
            raw2, sha2 = contents.get_file(path)
        except Exception:
            return None, 'uncertain-ledger-read'
        if raw2 != raw or sha2 != sha:
            continue
        try:
            contents.put_file(path, grant.canonical_ledger_bytes(new_ledger),
                              'diagnostic: test grant update', sha=sha)
            return new_ledger, None
        except coord_mod.CASConflict:
            continue
        except Exception:
            return None, 'uncertain-ledger-write'
    return None, 'blocked-ledger-cas'

def _abort_check(contents, owner, cycle_id, stage, grant_id, attempt_id, now_fn):
    def check():
        try:
            state, _ = contents.read_state()
            now = int(now_fn())
            if state is None or state.get('leaseOwner') != owner \
                    or state.get('cycleId') != cycle_id \
                    or state.get('stage') != stage \
                    or state.get('status') != 'idle' \
                    or state.get('callsMayHaveStarted') \
                    or state.get('intentAt') is not None \
                    or _int(state.get('leaseExpiresAt')) <= now \
                    or _int(state.get('cooldownUntil')) > now \
                    or _quota_configured(contents, state):
                return True
            marker = state.get(DIAGNOSTIC_FIELD)
            if not isinstance(marker, dict) or marker.get('attemptId') != attempt_id \
                    or marker.get('owner') != owner \
                    or marker.get('cycleId') != cycle_id \
                    or marker.get('stage') != stage \
                    or marker.get('grantId') != grant_id \
                    or marker.get('state') not in ('reserved', 'running'):
                return True
            path, ledger, _sha = _read_ledger(contents)
            row = next((r for r in ledger['reservations']
                        if r.get('attemptId') == attempt_id), None)
            return not (ledger.get('grantId') == grant_id and row
                        and row.get('state') == 'reserved')
        except Exception:
            return True
    return check

def _reserve_business(contents, owner, cycle_id, stage, marker, now_fn):
    """CAS lease + pending marker; every reread revalidates all prerequisites."""
    for _ in range(CAS_ATTEMPTS):
        current, current_sha = contents.read_state()
        now = int(now_fn())
        if current is None:
            raise ProbeBlocked('blocked-remote-state-missing', 'remote-state-missing')
        if current.get('cycleId') != cycle_id or current.get('stage') != stage:
            raise ProbeBlocked('blocked-business-state-changed', 'cycle-stage-changed')
        if current.get('status') != 'idle':
            raise ProbeBlocked('blocked-remote-inactive', 'remote-not-idle')
        if current.get('callsMayHaveStarted') or current.get('intentAt') is not None:
            raise ProbeBlocked('blocked-business-intent', 'business-intent')
        if _int(current.get('cooldownUntil')) > now:
            raise ProbeBlocked('blocked-cooldown', 'cooldown-active')
        if _quota_configured(contents, current):
            raise ProbeBlocked('blocked-quota-window-unsupported',
                               'quota-window-accounting-unsupported')
        old = current.get(DIAGNOSTIC_FIELD)
        if old is not None and (not isinstance(old, dict)
                                or old.get('state') != 'completed'):
            raise ProbeBlocked('blocked-diagnostic-pending', 'unresolved-marker')
        lease_owner = current.get('leaseOwner')
        if lease_owner is not None and _int(current.get('leaseExpiresAt')) > now:
            raise ProbeBlocked('blocked-foreign-lease', 'foreign-lease')
        candidate = copy.deepcopy(current)
        candidate['leaseOwner'] = owner
        candidate['leaseAcquiredAt'] = now
        candidate['leaseRenewedAt'] = now
        candidate['leaseExpiresAt'] = now + coord_mod.LEASE_SECONDS
        candidate['updatedAt'] = now
        candidate[DIAGNOSTIC_FIELD] = copy.deepcopy(marker)
        try:
            contents.write_state(candidate, current_sha)
            return candidate
        except coord_mod.CASConflict:
            continue
        except coord_mod.CoordinatorStateError:
            raise ProbeBlocked('blocked-state-contract', 'state-contract')
        except Exception:
            raise ProbeBlocked('uncertain-reservation', 'business-reservation-ambiguous')
    raise ProbeBlocked('blocked-reservation-conflict', 'business-cas-exhausted')

def _finish_business(contents, owner, cycle_id, stage, base_state, marker,
                     now, release=True):
    for _ in range(CAS_ATTEMPTS):
        try:
            current, sha = contents.read_state()
        except Exception:
            return 'uncertain-cleanup'
        if current is None or current.get('leaseOwner') != owner \
                or current.get('cycleId') != cycle_id or current.get('stage') != stage:
            return 'uncertain-lease-lost'
        current_marker = current.get(DIAGNOSTIC_FIELD)
        if not isinstance(current_marker, dict) \
                or current_marker.get('attemptId') != marker.get('attemptId') \
                or current_marker.get('owner') != owner:
            return 'uncertain-lease-lost'
        final = copy.deepcopy(current)
        final[DIAGNOSTIC_FIELD] = copy.deepcopy(marker)
        counts = dict(current.get('responseStatusCounts') or {})
        for key, value in (base_state.get('responseStatusCounts') or {}).items():
            counts[key] = max(_int(counts.get(key)), _int(value))
        final['responseStatusCounts'] = counts
        final['cooldownUntil'] = max(_int(current.get('cooldownUntil')),
                                     _int(base_state.get('cooldownUntil')))
        if release:
            final['leaseOwner'] = None
            final['leaseExpiresAt'] = 0
        final['updatedAt'] = int(now)
        try:
            contents.write_state(final, sha)
            return None
        except coord_mod.CASConflict:
            continue
        except Exception:
            return 'uncertain-cleanup'
    return 'uncertain-cleanup'


def run_probe(*, env=None, contents, fetch=None, now=None, selector=None,
              grant_id):
    env = os.environ if env is None else env
    fetch = fetch_mod if fetch is None else fetch
    now_fn = now or time.time
    selector = selector or candidate_mod.select_candidate
    report = {'schemaVersion': 1, 'mode': 'test-grant', 'status': None,
              'ok': False, 'grantId': grant_id, 'providersCalled': False,
              'providerPhaseEntered': False, 'actual': {'token': 0, 'search': 0},
              'reservedUpperBound': dict(grant.RESERVED), 'reservation': None,
              'coordinatorOps': 0, 'uncertainty': None}

    if not isinstance(grant_id, str) or not GRANT_RE.fullmatch(grant_id):
        return _finish(report, 'blocked-grant-id', 'grant-id-invalid', 2)
    run_id = str(env.get('GITHUB_RUN_ID') or '')
    run_attempt = str(env.get('GITHUB_RUN_ATTEMPT') or '')
    if not run_id or not run_attempt or not RUN_RE.fullmatch(run_id) \
            or not RUN_RE.fullmatch(run_attempt):
        return _finish(report, 'blocked-run-identity', 'missing-run-identity', 2)
    attempt_id = f'{run_id}-{run_attempt}'
    if str(env.get('AIRCON_BIGGO_TEST_MODE') or '') == '1':
        return _finish(report, 'blocked-test-mode', 'test-mode-forbids-network', 2)
    if _credentials(fetch) != 'full':
        return _finish(report, 'blocked-credentials', 'provider-credentials', 2)
    if _quota_configured(contents, None):
        return _finish(report, 'blocked-quota-window-unsupported',
                       'quota-window-accounting-unsupported', 2)
    try:
        selection, info = selector()
    except Exception:
        return _finish(report, 'blocked-candidate-selection',
                       'candidate-selection-error', 2)
    if selection is None:
        return _finish(report, 'local-skipped', 'no-active-eligible-model', 0)
    report['candidate'] = {'model': selection.get('model'),
                           'brand': selection.get('brand'),
                           'canonicalKey': selection.get('canonicalKey')}
    try:
        path, ledger, sha = _read_ledger(contents)
        if ledger.get('grantId') != grant_id:
            return _finish(report, 'blocked-grant-id', 'grant-id-mismatch', 2)
        grant.validate_ledger(ledger, expected_grant_id=grant_id)
    except ProbeBlocked as e:
        return _finish(report, e.status, e.reason, 2)
    try:
        state, state_sha = contents.read_state()
    except Exception:
        return _finish(report, 'blocked-coordinator-read', 'coordinator-read-error', 2)
    if state is None:
        return _finish(report, 'blocked-remote-state-missing', 'remote-state-missing', 2)
    if state.get('status') != 'idle':
        return _finish(report, 'blocked-remote-inactive', 'remote-not-idle', 2)
    if state.get('callsMayHaveStarted') or state.get('intentAt') is not None:
        return _finish(report, 'blocked-business-intent', 'business-intent', 2)
    if state.get('providerQuotaLimit') is not None or state.get('providerWindowEnd') is not None:
        return _finish(report, 'blocked-quota-window-unsupported',
                       'quota-window-accounting-unsupported', 2)
    if _int(state.get('cooldownUntil')) > int(now_fn()):
        return _finish(report, 'blocked-cooldown', 'cooldown-active', 2)
    old_marker = state.get(DIAGNOSTIC_FIELD)
    if old_marker is not None and (not isinstance(old_marker, dict)
                                   or old_marker.get('state') != 'completed'):
        return _finish(report, 'blocked-diagnostic-pending',
                       'unresolved-marker', 2)
    if state.get('leaseOwner') is not None \
            and _int(state.get('leaseExpiresAt')) > int(now_fn()):
        return _finish(report, 'blocked-foreign-lease', 'foreign-lease', 2)
    if grant.remaining(ledger) < 2:
        return _finish(report, 'blocked-grant-exhausted', 'remaining-less-than-2', 2)

    cycle_id = state.get('cycleId')
    stage = state.get('stage')
    if any(r.get('attemptId') == attempt_id for r in ledger['reservations']):
        return _finish(report, 'blocked-attempt-replay', 'attempt-already-reserved', 2)
    owner = OWNER_PREFIX + attempt_id
    marker = _marker(owner, attempt_id, cycle_id, stage, now_fn())
    marker['grantId'] = grant_id

    try:
        reserved_state = _reserve_business(contents, owner, cycle_id, stage,
                                           marker, now_fn)
    except ProbeBlocked as e:
        code = 1 if e.status.startswith('uncertain') else 2
        return _finish(report, e.status, e.reason, code)
    report['reservation'] = {'state': 'reserved', 'attemptId': attempt_id}
    # Reserve the ledger only after the business lease+marker exists.
    try:
        new_ledger, error = _ledger_cas(
            contents, path,
            lambda current: grant.reserve_attempt(current, attempt_id=attempt_id,
                                                  host='manual', reserved_at=now_fn()))
    except Exception:
        new_ledger, error = None, 'uncertain-grant-reserve'
    if error:
        if error.startswith('uncertain'):
            _finish_business(contents, owner, cycle_id, stage, reserved_state,
                             dict(marker, state='uncertain', reason=error), now_fn(),
                             release=False)
            return _finish(report, 'uncertain-grant-reservation',
                           'ledger-write-ambiguous', 1,
                           uncertainty='business-lease-held; ledger may contain reservation')
        _finish_business(contents, owner, cycle_id, stage, reserved_state,
                         dict(marker, state='uncertain', reason=error), now_fn())
        return _finish(report, error, error, 2)
    report['reservedUpperBound'] = dict(grant.RESERVED)
    report['providerPhaseEntered'] = True

    limiter = RequestLimiter(search_cap=1, token_cap=1)
    previous_limiter = getattr(fetch, 'get_request_limiter', lambda: None)()
    previous_abort = getattr(fetch, 'get_abort_check', lambda: None)()
    previous_opener = _harden_transport()
    before = _stat(getattr(fetch, 'REQUEST_STATS', {}))
    raw = None
    try:
        fetch.set_request_limiter(limiter)
        fetch.set_abort_check(_abort_check(contents, owner, cycle_id, stage,
                                           grant_id, attempt_id, now_fn))
        raw = fetch.run_smoke([selection])
    except Exception:
        raw = {'reason': 'transport', 'ok': False, 'reachable': False,
               'responseValid': False, 'priceAvailable': False, 'token': 0, 'search': 0}
    finally:
        _restore_transport(previous_opener)
        fetch.set_request_limiter(previous_limiter)
        fetch.set_abort_check(previous_abort)
    after = _stat(getattr(fetch, 'REQUEST_STATS', {}))
    actual = _delta(after, before)
    if actual['token'] + actual['search'] > 0:
        report['providersCalled'] = True
    report['actual'] = dict(actual)
    ok_smoke, reason, info = validate_smoke_result(raw, allow_legacy_bool=False)
    info = info if isinstance(info, dict) else {}
    evidence = getattr(fetch, 'LAST_RATE_LIMIT_EVIDENCE', None) or {}
    http_status = evidence.get('status') if isinstance(evidence.get('status'), int) else None
    retry_after = evidence.get('retryAfter')
    # Record actual in the same read payload+SHA transform; never use a stale
    # payload with a newer SHA (that would drop a concurrent reservation).
    ledger_path = grant.fixed_ledger_path(contents.config)
    try:
        final_ledger, error = _ledger_cas(
            contents, ledger_path,
            lambda current: grant.finish_attempt(
                current, attempt_id=attempt_id, actual=actual,
                http_status=http_status, reason=reason,
                retry_after=retry_after))
    except Exception:
        final_ledger, error = None, 'uncertain-grant-finalize'
    if error:
        # Provider already ran.  Any inability to persist the actual outcome is
        # uncertainty, never a clean blocked/success result.  Keep the lease and
        # pending marker so no normal writer can take over.
        _finish_business(contents, owner, cycle_id, stage, reserved_state,
                         dict(marker, state='uncertain', reason='ledger-finish'), now_fn(),
                         release=False)
        return _finish(report, 'uncertain-grant-finalize', 'ledger-finalize',
                       1, uncertainty='provider-outcome-not-finalized')
    marker.update({'state': 'completed', 'completedAt': int(now_fn()),
                   'actual': dict(actual), 'reason': str(reason)[:64],
                   'httpStatus': http_status, 'retryAfter': retry_after})
    # Preserve factual cooldown; no retry.
    if http_status:
        until, _evidence, _basis = coord_mod.cooldown_until_for(
            http_status, {'Retry-After': retry_after}, int(now_fn()))
        if until:
            reserved_state = copy.deepcopy(reserved_state)
            reserved_state['cooldownUntil'] = max(
                _int(reserved_state.get('cooldownUntil')), until)
    error = _finish_business(contents, owner, cycle_id, stage, reserved_state,
                             marker, now_fn())
    if error:
        return _finish(report, 'uncertain-cleanup', error, 1,
                       uncertainty='lease-release-or-marker-cleanup')
    report['reservation'] = {'state': 'completed', 'attemptId': attempt_id}
    if ok_smoke and reason in ('priced', 'no-price'):
        return _finish(report, 'healthy', reason, 0)
    return _finish(report, 'probe-failed', str(reason or 'invalid-result'), 1)


def _finish(report, status, reason, code, uncertainty=None):
    report['status'] = status
    report['reason'] = str(reason)[:120] if reason else None
    report['exitCode'] = code
    report['ok'] = code == 0
    if uncertainty:
        report['uncertainty'] = uncertainty
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--grant-id', required=True)
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)
    # This entrypoint intentionally has no path/credential inputs; env is the
    # only source, as in existing manual diagnostic workflows.
    try:
        config = coord_mod.CoordinatorConfig.from_env()
        if config is None:
            raise ProbeBlocked('blocked-coordinator-config', 'coordinator-not-configured')
        contents = coord_mod.GitHubContentsClient(config)
        report = run_probe(contents=contents, grant_id=args.grant_id)
    except ProbeBlocked as e:
        report = {'schemaVersion': 1, 'mode': 'test-grant', 'ok': False,
                  'status': e.status, 'reason': e.reason, 'exitCode': 2}
    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.flush(); os.fsync(f.fileno())
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return int(report.get('exitCode', 2))


if __name__ == '__main__':
    sys.exit(main())
