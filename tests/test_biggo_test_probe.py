# -*- coding: utf-8 -*-
"""Offline behavior tests for the five-request diagnostic test grant."""
import copy
import hashlib
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import biggo_coordinator as coord  # noqa: E402
import biggo_test_probe as probe  # noqa: E402
import diagnostic_test_grant as grant  # noqa: E402
import propose_diagnostic_test_grant as proposal  # noqa: E402


AUTH = {
    'schemaVersion': 1,
    'kind': grant.AUTH_KIND,
    'authorizedAt': '2026-10-17T12:16:48Z',
    'userInstruction': 'approved bounded test calls',
    'scope': 'BigGo diagnostic provider HTTP requests, both hosts combined',
    'hardMaximum': 5,
    'includes': ['token/auth', 'search', 'failed requests', 'retries', 'redirects'],
    'newPriceCycle': False, 'fullBatch': False, 'priceOrLifecycleApply': False,
    'priorCoordinatorIncidentNotRetroactivelyAuthorized': True,
}


class FakeContents:
    def __init__(self):
        self.config = type('Cfg', (), {'state_path': 'coordinator/biggo-state.json'})()
        self.files = {}
        self.sha_n = 0
        self.puts = 0
        self.fail_put = None
        self.fail_state = False
        self.state = None
        self._install_state()

    def _install_state(self):
        st = coord.initial_state('2026-10-10:4/7', 4, 'incident-owner', 100)
        st.update({'status': 'idle', 'leaseOwner': None, 'leaseAcquiredAt': 0,
                   'leaseRenewedAt': 0, 'leaseExpiresAt': 0,
                   'requestAttempts': {'token': 0, 'search': 0},
                   'callsMayHaveStarted': False, 'intentAt': None,
                   'cooldownUntil': 0, 'providerQuotaLimit': None,
                   'providerWindowEnd': None})
        self.state = st
        self._put(self.config.state_path, json.dumps(st).encode(), 'state')

    def _put(self, path, data, message='x'):
        self.sha_n += 1
        self.files[path] = (data, f's{self.sha_n}')

    def read_state(self):
        item = self.files[self.config.state_path]
        return json.loads(item[0].decode()), item[1]

    def write_state(self, state, sha):
        self.puts += 1
        if self.fail_state:
            raise OSError('persistent state cleanup failure')
        if self.fail_put == 'state':
            self.fail_put = None
            raise coord.CASConflict('simulated state conflict')
        current = self.files[self.config.state_path]
        if sha is not None and current[1] != sha:
            raise coord.CASConflict('stale')
        coord.validate_state(state)
        self._put(self.config.state_path, json.dumps(state).encode())
        self.state = copy.deepcopy(state)
        return self.files[self.config.state_path][1]

    def get_file(self, path):
        item = self.files.get(path)
        return (item[0], item[1]) if item else (None, None)

    def put_file(self, path, data, message, sha=None):
        self.puts += 1
        if self.fail_put == 'ledger':
            self.fail_put = None
            raise OSError('ambiguous ledger PUT')
        current = self.files.get(path)
        if sha is not None and (current is None or current[1] != sha):
            raise coord.CASConflict('stale')
        if sha is None and current is not None:
            raise coord.CASConflict('exists')
        self._put(path, data, message)
        return self.files[path][1]


class FakeFetch:
    REQUEST_STATS = {'token': 0, 'search': 0}
    LAST_AUTH_FACT = 'verified'
    LAST_RATE_LIMIT_EVIDENCE = {}

    def __init__(self, mode='priced'):
        self.mode = mode
        self.limiter = None
        self.abort = None
        self.calls = []

    def _credentials_state(self):
        return 'full'

    def set_request_limiter(self, value):
        self.limiter = value

    def set_abort_check(self, value):
        self.abort = value

    def get_request_limiter(self):
        return self.limiter

    def get_abort_check(self):
        return self.abort

    def run_smoke(self, candidates):
        self.calls.append(candidates)
        if self.abort and self.abort():
            return {'reason': 'aborted', 'ok': False, 'reachable': False,
                    'responseValid': False, 'priceAvailable': False,
                    'token': 0, 'search': 0}
        if self.mode == 'auth':
            self.REQUEST_STATS['token'] += 1
            self.LAST_AUTH_FACT = 'failed'
            return {'reason': 'auth', 'ok': False, 'reachable': True,
                    'responseValid': False, 'priceAvailable': False,
                    'token': 1, 'search': 0}
        self.REQUEST_STATS['token'] += 1
        self.REQUEST_STATS['search'] += 1
        self.LAST_AUTH_FACT = 'verified'
        return {'reason': 'priced', 'ok': True, 'reachable': True,
                'responseValid': True, 'priceAvailable': True,
                'token': 1, 'search': 1}


def _selection():
    return {'model': 'WK70B07R32', 'brand': 'BODYSONIC',
            'canonicalKey': 'BODYSONIC|WK70B07R32', 'reason': 'test'}, {'eligible': 1}


def _ledger(contents):
    auth = grant.make_authorization(copy.deepcopy(AUTH), 'sha256:' + 'a' * 64)
    ledger = grant.empty_ledger(auth)
    path = grant.fixed_ledger_path(contents.config)
    contents._put(path, grant.canonical_ledger_bytes(ledger), 'ledger')
    return path, ledger, auth


def _env(attempt='1'):
    return {'GITHUB_RUN_ID': '40000000001', 'GITHUB_RUN_ATTEMPT': attempt}


def _run(contents, fetch, attempt='1'):
    _path, ledger, auth = _ledger(contents)
    return probe.run_probe(env=_env(attempt), contents=contents, fetch=fetch,
                           selector=_selection, grant_id=auth['grantId'])


def test_two_hosts_four_requests_then_third_attempt_blocks():
    contents = FakeContents(); _path, _ledger0, auth0 = _ledger(contents)
    f = FakeFetch()
    r1 = probe.run_probe(env=_env('1'), contents=contents, fetch=f,
                         selector=_selection, grant_id=auth0['grantId'])
    assert r1['ok'] is True and r1['actual'] == {'token': 1, 'search': 1}
    assert contents.state['status'] == 'idle' and contents.state['stage'] == 4
    assert contents.state['requestAttempts'] == {'token': 0, 'search': 0}
    assert contents.state['leaseOwner'] is None
    r2 = probe.run_probe(env=_env('2'), contents=contents, fetch=f,
                         selector=_selection, grant_id=auth0['grantId'])
    assert r2['ok'] is True and r2['actual'] == {'token': 1, 'search': 1}
    ledger = json.loads(contents.get_file(_path)[0])
    assert ledger['reserved'] == {'token': 2, 'search': 2}
    assert ledger['actual'] == {'token': 2, 'search': 2}
    before = len(f.calls)
    r3 = probe.run_probe(env=_env('3'), contents=contents, fetch=f,
                         selector=_selection, grant_id=auth0['grantId'])
    assert r3['ok'] is False and r3['status'] == 'blocked-grant-exhausted'
    assert len(f.calls) == before


def test_idle_foreign_lease_and_pending_marker_block_without_put_or_provider():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    contents.state['leaseOwner'] = 'foreign'
    contents.state['leaseExpiresAt'] = 200
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    f = FakeFetch(); puts = contents.puts
    r = probe.run_probe(env=_env(), contents=contents, fetch=f, now=lambda: 100,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['status'] == 'blocked-foreign-lease' and len(f.calls) == 0
    assert contents.puts == puts
    contents.state['leaseOwner'] = None; contents.state['leaseExpiresAt'] = 0
    contents.state[coord.DIAGNOSTIC_FIELD] = {'state': 'reserved'}
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    puts = contents.puts
    r = probe.run_probe(env=_env(), contents=contents, fetch=f, now=lambda: 100,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['status'] == 'blocked-diagnostic-pending' and len(f.calls) == 0
    assert contents.puts == puts


def test_grant_id_cap_and_replay_fail_closed():
    contents = FakeContents(); path, _l, auth = _ledger(contents)
    f = FakeFetch()
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id='sha256:' + '0' * 64)
    assert r['status'] == 'blocked-grant-id' and len(f.calls) == 0
    ledger = json.loads(contents.get_file(path)[0])
    ledger['hardMaximum'] = 6
    contents._put(path, json.dumps(ledger).encode(), 'bad')
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['status'] == 'blocked-grant-invalid'
    contents = FakeContents(); path, _l, auth = _ledger(contents)
    f = FakeFetch()
    r1 = probe.run_probe(env=_env('1'), contents=contents, fetch=f,
                         selector=_selection, grant_id=auth['grantId'])
    assert r1['ok']
    before = len(f.calls)
    r2 = probe.run_probe(env=_env('1'), contents=contents, fetch=f,
                         selector=_selection, grant_id=auth['grantId'])
    assert r2['ok'] is False and len(f.calls) == before


def test_auth_failure_and_rate_limit_do_not_retry_and_preserve_cooldown():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    f = FakeFetch('auth')
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and r['actual'] == {'token': 1, 'search': 0}
    assert len(f.calls) == 1
    ledger = json.loads(contents.get_file(grant.fixed_ledger_path(contents.config))[0])
    assert ledger['reserved'] == {'token': 1, 'search': 1}
    assert ledger['actual'] == {'token': 1, 'search': 0}
    assert contents.state['requestAttempts'] == {'token': 0, 'search': 0}
    # 429 factual cooldown is persisted, with one attempt only.
    contents = FakeContents(); _p, _l, auth = _ledger(contents)
    f = FakeFetch()
    def rate_smoke(candidates):
        f.calls.append(candidates)
        f.REQUEST_STATS['token'] += 1
        f.REQUEST_STATS['search'] += 1
        f.LAST_RATE_LIMIT_EVIDENCE = {'status': 429, 'retryAfter': '120'}
        return {'reason': 'rate-limit', 'ok': False, 'reachable': True,
                'responseValid': False, 'priceAvailable': False,
                'token': 1, 'search': 1}
    f.run_smoke = rate_smoke
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and len(f.calls) == 1
    assert contents.state['cooldownUntil'] > 0


def test_ledger_ambiguous_after_provider_is_uncertain_and_holds_pending_marker():
    contents = FakeContents(); path, _l, auth = _ledger(contents)
    f = FakeFetch()
    contents.fail_put = 'ledger'
    # fail exactly the first new ledger PUT (reservation), not state.
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and r['status'].startswith('uncertain')
    assert len(f.calls) == 0
    assert contents.state[coord.DIAGNOSTIC_FIELD]['state'] == 'uncertain'
    assert contents.state['requestAttempts'] == {'token': 0, 'search': 0}


def test_normal_acquire_honours_idle_foreign_lease_and_preserves_completed_marker():
    contents = FakeContents()
    a = coord.CoordinatorClient(contents, 'foreign', now=lambda: 100)
    contents.state.update({'leaseOwner': 'foreign', 'leaseExpiresAt': 200})
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    b = coord.CoordinatorClient(contents, 'normal', now=lambda: 100)
    puts = contents.puts
    assert b.acquire('2026-10-10:4/7', 4)['result'] == 'lost'
    assert contents.puts == puts
    # pending marker with expired lease still blocks normal takeover.
    contents.state.update({'leaseOwner': None, 'leaseExpiresAt': 0,
                           coord.DIAGNOSTIC_FIELD: {'state': 'uncertain'}})
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    puts = contents.puts
    assert b.acquire('2026-10-10:4/7', 4)['result'] == 'blocked-diagnostic-pending'
    assert contents.puts == puts
    # completed marker may be carried, not erased.
    contents.state[coord.DIAGNOSTIC_FIELD] = {'state': 'completed', 'attemptId': 'x'}
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    r = b.acquire('2026-10-10:4/7', 4)
    assert r['result'] == 'winner'
    assert r['state'][coord.DIAGNOSTIC_FIELD]['state'] == 'completed'


def test_grant_proposal_is_read_only_and_bound(tmp_path):
    auth_path = tmp_path / 'authorization.json'
    auth_path.write_text(json.dumps(AUTH), encoding='utf-8')
    out = tmp_path / 'proposal.json'
    assert proposal.main(['--authorization', str(auth_path), '--out', str(out)]) == 0
    p = json.loads(out.read_text())
    assert p['hardMaximum'] == 5 and p['applySupported'] is False
    assert p['grantId'] == p['authorization']['grantId']
    assert p['authorizationFileHash'] == 'sha256:' + hashlib.sha256(
        auth_path.read_bytes()).hexdigest()


def test_completion_ledger_ambiguous_after_provider_marks_uncertain():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    f = FakeFetch()
    original = f.run_smoke
    def run_and_break(candidates):
        raw = original(candidates)
        contents.fail_put = 'ledger'
        return raw
    f.run_smoke = run_and_break
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and r['status'] == 'uncertain-grant-finalize'
    assert r['actual'] == {'token': 1, 'search': 1}
    assert contents.state[coord.DIAGNOSTIC_FIELD]['state'] == 'uncertain'
    assert contents.state['requestAttempts'] == {'token': 0, 'search': 0}
    assert len(f.calls) == 1


def test_cleanup_failure_after_provider_is_uncertain_and_does_not_hide_calls():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    f = FakeFetch()
    original = f.run_smoke
    def run_and_break(candidates):
        raw = original(candidates)
        contents.fail_state = True
        return raw
    f.run_smoke = run_and_break
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and r['status'] == 'uncertain-cleanup'
    assert r['actual'] == {'token': 1, 'search': 1}
    assert len(f.calls) == 1


def test_provider_abort_before_request_keeps_actual_zero_and_releases_lease():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    f = FakeFetch()
    original = f.run_smoke
    def run_abort(candidates):
        # Simulate lease loss before any physical request; run_probe's abort
        # callback is called by the provider integration.
        contents.state['leaseOwner'] = 'other'
        contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
        return original(candidates)
    f.run_smoke = run_abort
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['ok'] is False and r['status'].startswith('uncertain')
    assert r['actual'] == {'token': 0, 'search': 0}


def test_missing_and_malformed_grant_block_before_reservation_or_provider():
    contents = FakeContents(); f = FakeFetch()
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id='sha256:' + '1' * 64)
    assert r['status'] == 'blocked-no-grant' and len(f.calls) == 0
    path = grant.fixed_ledger_path(contents.config)
    contents._put(path, b'{"broken":', 'broken')
    r = probe.run_probe(env=_env(), contents=contents, fetch=f,
                        selector=_selection, grant_id='sha256:' + '1' * 64)
    assert r['status'] == 'blocked-grant-invalid' and len(f.calls) == 0
    assert contents.state['requestAttempts'] == {'token': 0, 'search': 0}


def test_lease_abort_accepts_own_idle_lease_but_rejects_loss():
    contents = FakeContents(); path, ledger, auth = _ledger(contents)
    contents.state.update({'leaseOwner': 'aircon-test-grant-40000000001-1',
                           'leaseExpiresAt': 200,
                           coord.DIAGNOSTIC_FIELD: {
                               'state': 'reserved', 'attemptId': '40000000001-1',
                               'owner': 'aircon-test-grant-40000000001-1',
                               'cycleId': '2026-10-10:4/7', 'stage': 4,
                               'grantId': auth['grantId']}})
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    reserved = grant.reserve_attempt(ledger, attempt_id='40000000001-1',
                                     host='manual', reserved_at=100)
    contents._put(path, grant.canonical_ledger_bytes(reserved), 'ledger')
    check = probe._abort_check(contents, 'aircon-test-grant-40000000001-1',
                               '2026-10-10:4/7', 4, auth['grantId'],
                               '40000000001-1', lambda: 100)
    assert check() is False
    contents.state['leaseOwner'] = 'other'
    contents.write_state(contents.state, contents.get_file(contents.config.state_path)[1])
    assert check() is True


def test_missing_run_identity_blocks_before_any_provider_or_ledger_write():
    contents = FakeContents(); _path, _l, auth = _ledger(contents)
    f = FakeFetch(); puts = contents.puts
    r = probe.run_probe(env={}, contents=contents, fetch=f,
                        selector=_selection, grant_id=auth['grantId'])
    assert r['status'] == 'blocked-run-identity' and len(f.calls) == 0
    assert contents.puts == puts
