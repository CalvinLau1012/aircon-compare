# -*- coding: utf-8 -*-
"""Bounded BigGo diagnostic 聚焦測試（完全離線；冇真實 provider／coordinator）。

覆蓋 preview 零流量、execute preflight（憑證／local stage／coordinator／remote
state）、共享 lease＋reserved upper bound、CAS 衝突／歧義、replay guard、crash
後 same-stage takeover、budget 共享、lease lost between phases、finalize 永不
清 successor lease、provider taxonomy（loopback）、limiter 還原、op cap、
sanitized output。全部都只用 in-memory fake Contents client 同 loopback HTTP。
"""
import base64
import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_candidate as candidate_mod  # noqa: E402
import biggo_coordinator as coord  # noqa: E402
import biggo_smoke_diagnostic as diag  # noqa: E402
import fetch_biggo  # noqa: E402

STATE_PATH = 'coordinator/state.json'
CONFIG = coord.CoordinatorConfig('owner/coordinator', 'tok', branch='main',
                                 state_path=STATE_PATH,
                                 snapshot_dir='coordinator/snapshots')
ENV_FULL = {
    'GITHUB_RUN_ID': '777',
    'GITHUB_RUN_ATTEMPT': '1',
    'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/coordinator-repo',
    'AIRCON_BIGGO_COORDINATOR_TOKEN': 'tok',
    'AIRCON_BIGGO_COORDINATOR_BRANCH': 'main',
    'AIRCON_BIGGO_COORDINATOR_STATE_PATH': STATE_PATH,
    'AIRCON_BIGGO_COORDINATOR_SNAPSHOT_DIR': 'coordinator/snapshots',
}
NOW = 2_000_000
ACTIVE_META = {'price_batch_start': '2026-10-01', 'price_batch_idx': 0}
NO_PRICE_RESULT = {'reason': 'no-price', 'ok': True, 'reachable': True,
                   'responseValid': True, 'priceAvailable': False,
                   'token': 1, 'search': 1}
PRICED_RESULT = {**NO_PRICE_RESULT, 'reason': 'priced', 'priceAvailable': True}
_DEFAULT = object()


# ---------------------------------------------------------------- fakes


class FakeGitHubApi:
    """In-memory Contents API（blob SHA CAS；可注入 PUT 失敗／GET transform）。"""

    def __init__(self):
        self.files = {}
        self.sha_counter = 0
        self.calls = []
        self.fail_next_put = None
        self.fail_puts = {}
        self.put_exceptions = {}
        self.get_exceptions = {}
        self.conflict_puts = False
        self.put_attempts = 0
        self.get_attempts = 0
        self.transform_get = None

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body))
        path = urllib.parse.unquote(url.split('/contents/', 1)[1].split('?', 1)[0])
        if method == 'GET':
            self.get_attempts += 1
            if self.get_attempts in self.get_exceptions:
                raise self.get_exceptions[self.get_attempts]
            if path not in self.files:
                return 404, b''
            data, sha = self.files[path]
            if self.transform_get is not None:
                data, sha = self.transform_get(path, data, sha)
            return 200, json.dumps({
                'content': base64.b64encode(data).decode('ascii'),
                'sha': sha}).encode()
        if method == 'PUT':
            self.put_attempts += 1
            if self.put_attempts in self.put_exceptions:
                raise self.put_exceptions[self.put_attempts]
            status = self.fail_next_put
            self.fail_next_put = None
            if status is None:
                status = self.fail_puts.get(self.put_attempts)
            if self.conflict_puts:
                status = 409
            if status is not None:
                return status, b''
            payload = json.loads(body.decode('utf-8'))
            current = self.files.get(path)
            want_sha = payload.get('sha')
            if want_sha and (current is None or current[1] != want_sha):
                return 409, b'conflict'
            if not want_sha and current is not None:
                return 409, b'exists'
            self.sha_counter += 1
            sha = f'sha{self.sha_counter}'
            self.files[path] = (base64.b64decode(payload['content']), sha)
            return 201, json.dumps({'content': {'sha': sha}}).encode()
        raise AssertionError(f'unsupported {method}')

    def state(self):
        raw = self.files.get(STATE_PATH)
        return json.loads(raw[0].decode('utf-8')) if raw else None

    def put_count(self):
        return sum(1 for c in self.calls if c[0] == 'PUT')


class FakeBatch:
    PRICE_BATCH_DAYS = 7
    MetaError = Exception

    def __init__(self, meta=None):
        self.meta = dict(ACTIVE_META if meta is None else meta)

    def load_meta(self):
        return dict(self.meta)


class FakeFetch:
    def __init__(self, smoke=None, stats=(1, 1), credentials='full',
                 auth_fact='verified', stage_idx=0):
        self.REQUEST_STATS = {'token': 0, 'search': 0}
        self.LAST_AUTH_FACT = auth_fact
        self.LAST_RATE_LIMIT_EVIDENCE = {}
        self.smoke_result = dict(smoke or NO_PRICE_RESULT)
        self.smoke_stats = tuple(stats)
        self.credentials = credentials
        self.stage_idx = stage_idx
        self.smoke_calls = []
        self.request_limiter = None
        self.abort_check = None
        self.limiter_at_smoke = None
        self.abort_at_smoke = None

    def _credentials_state(self):
        return self.credentials

    def stage_workload(self, meta=None):
        return {'batch': ['M1', 'M2'], 'review': [], 'idx': self.stage_idx,
                'total': 2, 'queuedModels': 2}

    def set_request_limiter(self, limiter):
        self.request_limiter = limiter

    def set_abort_check(self, check):
        self.abort_check = check

    def run_smoke(self, candidates=None):
        self.smoke_calls.append(list(candidates or []))
        self.limiter_at_smoke = self.request_limiter
        self.abort_at_smoke = self.abort_check
        self.REQUEST_STATS['token'] += self.smoke_stats[0]
        self.REQUEST_STATS['search'] += self.smoke_stats[1]
        return dict(self.smoke_result)


class CrashFetch(FakeFetch):
    def run_smoke(self, candidates=None):
        raise SystemExit('simulated process termination after reservation')


class Loopback:
    """Minimal loopback HTTP provider（可設定 exact route／fallback）。"""

    def __init__(self):
        self.calls = []
        self.routes = {}
        self.fallback = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _dispatch(self):
                path = urllib.parse.urlparse(self.path).path
                outer.calls.append((self.command, path))
                responder = outer.routes.get(path, outer.fallback)
                if responder is None:
                    _respond(self, 404, b'{}')
                else:
                    responder(self, path)

            def do_GET(self):
                self._dispatch()

            def do_POST(self):
                self._dispatch()

            def log_message(self, *args):
                pass

        self._server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def url(self, path):
        return f'http://127.0.0.1:{self._server.server_address[1]}{path}'

    def count(self, prefix):
        return sum(1 for _m, p in self.calls if p.startswith(prefix))


def _respond(handler, status, body=b'', headers=None):
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


@pytest.fixture
def loopback(monkeypatch):
    lb = Loopback().start()
    monkeypatch.setattr(fetch_biggo, 'AUTH_URL', lb.url('/auth/v1/token'))
    monkeypatch.setattr(fetch_biggo, 'API_URL', lb.url('/api/v1/spa/search/{q}/product'))
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})
    monkeypatch.setenv('BIGGO_CLIENT_ID', 'cid')
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', 'csecret')
    fetch_biggo.reset_request_stats()
    yield lb
    lb.stop()


# ---------------------------------------------------------------- helpers


def _selector(model='M1'):
    def select():
        return ({'model': model, 'brand': 'BRAND', 'canonicalKey': f'BRAND|{model}',
                 'reason': 'fixture deterministic'}, {'active': 2, 'blacklisted': 0,
                                                      'eligible': 2})
    return select


def _state(**overrides):
    state = coord.initial_state('2026-10-01:1/7', 1, 'business-writer', NOW - 10_000)
    state.update(overrides)
    return state


def _seed(api, state):
    api.files[STATE_PATH] = (coord.canonical_json_bytes(state), 'seed')


def _contents_factory(api, cap=None):
    def factory(config, now_fn):
        wrapped = diag._CappedApi(api, cap) if cap is not None else api
        return coord.GitHubContentsClient(config, api=wrapped)
    return factory


def _run(*, state=None, api=None, fetch=None, env=None, meta=None, now=NOW,
         selector=_DEFAULT, cap=None, execute=True, contents_factory=None):
    api = FakeGitHubApi() if api is None else api
    if state is not None:
        _seed(api, state)
    fetch = FakeFetch() if fetch is None else fetch
    merged_env = dict(ENV_FULL)
    if env is not None:
        merged_env.update(env)
    kwargs = {}
    if selector is not _DEFAULT:
        kwargs['selector'] = selector
    report = diag.run_diagnostic(
        execute=execute, env=merged_env, fetch=fetch,
        batch_mod=FakeBatch(meta), now=lambda: now,
        contents_factory=contents_factory or _contents_factory(api, cap), **kwargs)
    return report, fetch, api


def _marker(state):
    return state.get(diag.DIAGNOSTIC_FIELD)


# ---------------------------------------------------------------- preview


def test_preview_reports_selection_and_planned_bounds_with_zero_traffic():
    report, fetch, api = _run(execute=False, selector=_selector())
    assert report['status'] == 'preview' and report['exitCode'] == 0
    assert report['providersCalled'] is False
    assert report['candidate']['model'] == 'M1'
    assert report['planned']['tokenLimit'] == 1
    assert report['planned']['searchLimit'] == 1
    assert report['planned']['coordinatorOpsCap'] == diag.MAX_COORDINATOR_REQUESTS
    assert report['reservedUpperBound'] is None
    assert report['actual'] == {'token': 0, 'search': 0}
    assert api.calls == []
    assert fetch.smoke_calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_preview_no_candidate_is_local_skipped_zero_traffic():
    report, fetch, api = _run(execute=False,
                              selector=lambda: (None, {'reason': 'no-active-eligible-model',
                                                       'active': 0, 'blacklisted': 0}))
    assert report['status'] == 'preview-local-skipped' and report['exitCode'] == 0
    assert report['providersCalled'] is False and report['reservedUpperBound'] is None
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_preview_needs_no_run_identity_or_coordinator_config():
    api = FakeGitHubApi()
    report = diag.run_diagnostic(execute=False, env={}, fetch=FakeFetch(),
                                 batch_mod=FakeBatch(), now=lambda: NOW,
                                 selector=_selector())
    assert report['status'] == 'preview' and report['exitCode'] == 0
    assert api.calls == []


def test_preview_selection_failure_is_nonzero():
    def boom():
        raise RuntimeError('malformed-dataset')

    report, _fetch, api = _run(execute=False, selector=boom)
    assert report['status'] == 'blocked-candidate-selection' and report['exitCode'] == 2
    assert api.calls == []


def test_preview_uses_shared_selector_by_default(monkeypatch):
    called = {'n': 0}

    def spy():
        called['n'] += 1
        selection, info = _selector()()
        return selection, info

    monkeypatch.setattr(candidate_mod, 'select_candidate', spy)
    report, _fetch, api = _run(execute=False)  # 唔傳 selector → 用共享 selector
    assert called['n'] == 1
    assert report['candidate']['model'] == 'M1'
    assert api.calls == []


# ---------------------------------------------------------------- execute preflight


def test_execute_missing_run_identity_blocks_with_zero_traffic():
    env = {'GITHUB_RUN_ID': '', 'GITHUB_RUN_ATTEMPT': ''}
    report, fetch, api = _run(state=_state(), env=env)
    assert report['status'] == 'blocked-run-identity' and report['exitCode'] == 2
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_partial_credentials_block_before_any_coordinator_op():
    report, fetch, api = _run(state=_state(), fetch=FakeFetch(credentials='partial'))
    assert report['status'] == 'blocked-credentials'
    assert report['reason'] == 'provider-credentials-partial'
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_no_credentials_block_before_any_coordinator_op():
    report, fetch, api = _run(state=_state(), fetch=FakeFetch(credentials='none'))
    assert report['status'] == 'blocked-credentials'
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_test_mode_blocks_zero_traffic():
    report, fetch, api = _run(state=_state(), env={'AIRCON_BIGGO_TEST_MODE': '1'})
    assert report['status'] == 'blocked-test-mode' and report['exitCode'] == 2
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_inactive_local_stage_blocks_zero_traffic():
    report, fetch, api = _run(state=_state(), meta={})
    assert report['status'] == 'blocked-no-active-local-stage' and report['exitCode'] == 2
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_local_stage_out_of_range_blocks():
    report, _fetch, api = _run(state=_state(),
                               meta={'price_batch_start': '2026-10-01',
                                     'price_batch_idx': 7})
    assert report['status'] == 'blocked-no-active-local-stage'
    assert api.calls == []


def test_execute_missing_coordinator_config_blocks_zero_traffic():
    report, fetch, api = _run(state=_state(), env={
        'AIRCON_BIGGO_COORDINATOR_REPO': '', 'AIRCON_BIGGO_COORDINATOR_TOKEN': ''})
    assert report['status'] == 'blocked-coordinator-config' and report['exitCode'] == 2
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_provider_quota_config_blocks_unsupported_zero_traffic():
    report, fetch, api = _run(state=_state(),
                              env={'AIRCON_BIGGO_PROVIDER_QUOTA_LIMIT': '100'})
    assert report['status'] == 'blocked-quota-window-unsupported'
    assert report['reason'] == 'quota-window-accounting-unsupported'
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_no_eligible_candidate_is_local_skipped_zero_traffic():
    report, fetch, api = _run(state=_state(),
                              selector=lambda: (None, {'reason': 'no-active-eligible-model'}))
    assert report['status'] == 'local-skipped' and report['exitCode'] == 0
    assert api.calls == [] and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_missing_remote_state_blocks_no_bootstrap_no_write():
    report, fetch, api = _run(state=None)
    assert report['status'] == 'blocked-no-remote-state' and report['exitCode'] == 2
    assert api.put_count() == 0
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


# ---------------------------------------------------------------- remote state gates


def test_execute_incompatible_cycle_stage_blocks_no_write():
    api = FakeGitHubApi()
    _seed(api, coord.initial_state('2026-10-01:2/7', 2, 'business-writer', NOW - 10_000))
    report, fetch, api = _run(api=api)
    assert report['status'] == 'blocked-incompatible-cycle-stage'
    assert report['remoteStage'] == 2 and report['cycleStageMatches'] is False
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_completed_remote_blocks_no_write():
    report, fetch, api = _run(state=_state(status='completed', leaseOwner=None,
                                           leaseExpiresAt=0))
    assert report['status'] == 'blocked-remote-completed'
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_needs_review_remote_blocks_no_write():
    report, fetch, api = _run(state=_state(status='needs_review', leaseOwner=None,
                                           leaseExpiresAt=0,
                                           lastReviewReason='publish-uncertain'))
    assert report['status'] == 'blocked-remote-needs_review'
    assert api.state()['lastReviewReason'] == 'publish-uncertain'
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_business_intent_blocks_and_is_preserved():
    seeded = _state(callsMayHaveStarted=True, intentAt=NOW - 500)
    report, fetch, api = _run(state=seeded)
    assert report['status'] == 'blocked-business-intent'
    remote = api.state()
    assert remote['callsMayHaveStarted'] is True and remote['intentAt'] == NOW - 500
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_cooldown_blocks_no_write():
    report, fetch, api = _run(state=_state(cooldownUntil=NOW + 3600))
    assert report['status'] == 'blocked-cooldown'
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_foreign_valid_lease_blocks_and_preserves_owner():
    seeded = _state(leaseOwner='writer-a', leaseExpiresAt=NOW + 100)
    report, fetch, api = _run(state=seeded)
    assert report['status'] == 'blocked-foreign-lease'
    remote = api.state()
    assert remote['leaseOwner'] == 'writer-a' and remote['leaseExpiresAt'] == NOW + 100
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_remote_inactive_blocks_no_write():
    report, fetch, api = _run(state=_state(status='idle', leaseOwner=None,
                                           leaseExpiresAt=0))
    assert report['status'] == 'blocked-remote-inactive'
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_unresolved_diagnostic_marker_blocks_replay():
    marker = diag._reservation_marker('aircon-diag-999-1', '999-1', '2026-10-01:1/7',
                                      1, NOW - 500)
    marker.update({'state': 'reserved', 'actual': {'token': 1, 'search': 0}})
    seeded = _state(requestAttempts={'token': 1, 'search': 1},
                    **{diag.DIAGNOSTIC_FIELD: marker})
    report, fetch, api = _run(state=seeded)
    assert report['status'] == 'blocked-diagnostic-pending'
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_uncertain_diagnostic_marker_blocks_replay():
    marker = diag._reservation_marker('aircon-diag-999-1', '999-1', '2026-10-01:1/7',
                                      1, NOW - 500)
    marker['state'] = 'uncertain'
    report, _fetch, api = _run(state=_state(**{diag.DIAGNOSTIC_FIELD: marker}))
    assert report['status'] == 'blocked-diagnostic-pending'
    assert api.put_count() == 0


def test_execute_budget_insufficient_blocks_no_write():
    report, fetch, api = _run(state=_state(requestAttempts={'token': 1, 'search': 0}))
    assert report['status'] == 'blocked-budget' and report['exitCode'] == 2
    assert api.put_count() == 0 and fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_execute_search_budget_insufficient_blocks_no_write():
    report, _fetch, api = _run(state=_state(requestAttempts={'token': 0, 'search': 5}))
    assert report['status'] == 'blocked-budget'
    assert api.put_count() == 0


# ---------------------------------------------------------------- reservation / happy path

SNAPSHOT_HASH = 'sha256:' + 'a' * 64
STAGE_RESULT_HASH = 'sha256:' + 'b' * 64
BUNDLE_HASH = 'sha256:' + 'c' * 64


def _business_state(**overrides):
    base = _state(
        snapshotHash=SNAPSHOT_HASH, snapshotPath='coordinator/snapshots/x/biggo_prices.json',
        stageResultHash=STAGE_RESULT_HASH, stageResultPath='coordinator/snapshots/x/stage-result.json',
        bundleHash=BUNDLE_HASH, lastReviewReason='old-review', cooldownUntil=0)
    base.update(overrides)
    return base


def test_execute_healthy_without_price_reserves_upper_bound_and_preserves_business():
    seeded = _business_state()
    report, fetch, api = _run(state=seeded)
    assert report['status'] == 'healthy-without-price' and report['exitCode'] == 0
    assert report['providersCalled'] is True
    assert report['reservedUpperBound'] == {'token': 1, 'search': 1}
    assert report['actual'] == {'token': 1, 'search': 1}
    state = api.state()
    assert state['status'] == 'active', 'business status 必須保留'
    assert state['cycleId'] == '2026-10-01:1/7' and state['stage'] == 1
    # 只加一次 reserved upper bound，唔會事後再加 actual。
    assert state['requestAttempts'] == {'token': 1, 'search': 1}
    assert state['callsMayHaveStarted'] is False, 'diagnostic 唔可以設 business intent'
    assert state['intentAt'] is None
    assert state['snapshotHash'] == SNAPSHOT_HASH
    assert state['snapshotPath'] == seeded['snapshotPath']
    assert state['stageResultHash'] == STAGE_RESULT_HASH
    assert state['bundleHash'] == BUNDLE_HASH
    assert state['lastReviewReason'] == 'old-review'
    assert state['leaseOwner'] is None and state['leaseExpiresAt'] == 0
    marker = _marker(state)
    assert marker['state'] == 'completed'
    assert marker['actual'] == {'token': 1, 'search': 1}
    assert marker['reserved'] == {'token': 1, 'search': 1}
    assert marker['reason'] == 'no-price' and marker['priceAvailable'] is False
    assert fetch.request_limiter is None and fetch.abort_check is None
    caps = fetch.limiter_at_smoke.snapshot()
    assert caps['searchCap'] == 1 and caps['tokenCap'] == 1


def test_execute_healthy_with_price_reports_price_available():
    report, fetch, api = _run(state=_business_state(),
                              fetch=FakeFetch(smoke=PRICED_RESULT))
    assert report['status'] == 'healthy-with-price' and report['exitCode'] == 0
    assert report['price'] == {'available': True}
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_execute_auth_failure_reserves_both_but_records_actual_token_only():
    smoke = {'reason': 'auth', 'ok': False, 'reachable': True, 'responseValid': False,
             'priceAvailable': False, 'token': 1, 'search': 0}
    report, fetch, api = _run(state=_business_state(),
                              fetch=FakeFetch(smoke=smoke, stats=(1, 0),
                                              auth_fact='failed'))
    assert report['status'] == 'probe-auth-failed' and report['exitCode'] == 1
    assert report['actual'] == {'token': 1, 'search': 0}
    state = api.state()
    assert state['requestAttempts'] == {'token': 1, 'search': 1}, \
        'failed auth 仍然保留 reserved search slot'
    assert _marker(state)['actual'] == {'token': 1, 'search': 0}
    assert _marker(state)['state'] == 'completed'


def test_execute_rate_limit_applies_factual_retry_after_cooldown():
    smoke = {'reason': 'rate-limit', 'ok': False, 'reachable': True,
             'responseValid': False, 'priceAvailable': False, 'token': 1, 'search': 1}
    fetch = FakeFetch(smoke=smoke)
    fetch.LAST_RATE_LIMIT_EVIDENCE = {'status': 429, 'retryAfter': '45'}
    report, fetch, api = _run(state=_business_state(), fetch=fetch)
    assert report['status'] == 'probe-rate-limited' and report['exitCode'] == 1
    state = api.state()
    assert state['cooldownUntil'] == NOW + 45, 'honour factual Retry-After'
    assert state['requestAttempts'] == {'token': 1, 'search': 1}
    assert state['responseStatusCounts'].get('429') == 1


def test_execute_403_without_retry_after_uses_project_fallback():
    smoke = {'reason': 'auth', 'ok': False, 'reachable': True, 'responseValid': False,
             'priceAvailable': False, 'token': 1, 'search': 0}
    fetch = FakeFetch(smoke=smoke, stats=(1, 0))
    fetch.LAST_RATE_LIMIT_EVIDENCE = {'status': 403}
    report, _fetch, api = _run(state=_business_state(), fetch=fetch)
    assert report['status'] == 'probe-auth-failed'
    assert api.state()['cooldownUntil'] == NOW + coord.PROJECT_COOLDOWN_SECONDS


def test_execute_transport_and_invalid_response_are_explicit_nonzero():
    for reason, stats in (('transport', (1, 0)), ('invalid-response', (1, 1))):
        smoke = {'reason': reason, 'ok': False, 'reachable': False,
                 'responseValid': False, 'priceAvailable': False,
                 'token': stats[0], 'search': stats[1]}
        report, _fetch, api = _run(state=_business_state(),
                                   fetch=FakeFetch(smoke=smoke, stats=stats))
        expected = 'probe-' + reason
        assert report['status'] == expected and report['exitCode'] == 1, reason
        assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_execute_malformed_structured_result_fails_closed(monkeypatch):
    for malformed in ({'ok': False}, None, {'ok': True},
                      {**NO_PRICE_RESULT, 'priceAvailable': True}):
        fetch = FakeFetch(smoke=NO_PRICE_RESULT)

        def run_smoke(candidates=None, _m=malformed, _f=fetch):
            _f.smoke_calls.append(candidates)
            return dict(_m) if isinstance(_m, dict) else _m

        fetch.run_smoke = run_smoke
        report, _fetch, api = _run(state=_business_state(), fetch=fetch)
        assert report['status'] == 'probe-invalid-response', malformed
        assert report['exitCode'] == 1
        assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


@pytest.mark.parametrize('raw', [True, False])
def test_execute_rejects_legacy_bool_results_as_invalid(raw):
    """Diagnostic 邊界唔接受 legacy bool；每日 runner 兼容路由另測。"""
    fetch = FakeFetch()

    def run_smoke(candidates=None, _raw=raw, _f=fetch):
        _f.smoke_calls.append(candidates)
        _f.REQUEST_STATS.update(token=1, search=1)
        return _raw

    fetch.run_smoke = run_smoke
    report, _fetch, api = _run(state=_business_state(), fetch=fetch)
    assert report['exitCode'] == 1
    assert report['status'] == 'probe-invalid-response'
    assert report['reason'] == 'smoke-invalid-result'
    assert report['response'] == {'valid': False}
    assert report['actual'] == {'token': 1, 'search': 1}
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}
    assert _marker(api.state())['reason'] == 'smoke-invalid-result'


def test_execute_uses_shared_validator(monkeypatch):
    monkeypatch.setattr(diag, 'validate_smoke_result',
                        lambda raw, **kwargs: (False, 'smoke-invalid-result', {}))
    report, _fetch, _api = _run(state=_business_state())
    assert report['status'] == 'probe-invalid-response' and report['exitCode'] == 1


def test_execute_passes_shared_candidate_through_exactly():
    selection = {'model': 'M9', 'brand': 'BRAND', 'canonicalKey': 'BRAND|M9',
                 'reason': 'fixture'}
    report, fetch, _api = _run(state=_business_state(),
                               selector=lambda: (selection, {'eligible': 1}))
    assert report['candidate']['model'] == 'M9'
    assert fetch.smoke_calls[0][0] == selection, 'candidate 要原樣傳入 run_smoke'


# ---------------------------------------------------------------- replay / crash / sharing


def test_execute_replay_after_success_blocked_by_reserved_budget():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    report, _fetch, api = _run(state=_business_state(), api=api)
    assert report['status'] == 'healthy-without-price'
    puts_after_first = api.put_count()
    report2, fetch2, api = _run(api=api, fetch=FakeFetch(), env={'GITHUB_RUN_ID': '777',
                                                                 'GITHUB_RUN_ATTEMPT': '2'})
    assert report2['status'] == 'blocked-budget' and report2['exitCode'] == 2
    assert api.put_count() == puts_after_first, 'replay 唔可以再寫／再預留'
    assert fetch2.REQUEST_STATS == {'token': 0, 'search': 0}
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_crash_after_reservation_preserves_upper_bound_for_same_stage_takeover():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    with pytest.raises(SystemExit):
        _run(api=api, fetch=CrashFetch())
    reserved = api.state()
    assert reserved['requestAttempts'] == {'token': 1, 'search': 1}
    assert _marker(reserved)['state'] == 'reserved'
    assert reserved['leaseOwner'] == 'aircon-diag-777-1'
    # 普通 same-stage takeover（lease 過期）：preserve attempts，唔會 reset 成 0。
    business = coord.CoordinatorClient(
        coord.GitHubContentsClient(CONFIG, api=api), 'business-x',
        now=lambda: NOW + coord.LEASE_SECONDS + 1)
    acquired = business.acquire('2026-10-01:1/7', 1)
    assert acquired['result'] == 'winner'
    assert acquired['state']['requestAttempts'] == {'token': 1, 'search': 1}
    assert acquired['state']['callsMayHaveStarted'] is False


def test_simultaneous_reservations_only_one_wins_other_sees_foreign_lease():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    with pytest.raises(SystemExit):
        _run(api=api, fetch=CrashFetch())
    puts_before = api.put_count()
    report, fetch, api = _run(api=api, fetch=FakeFetch(),
                              env={'GITHUB_RUN_ID': '888', 'GITHUB_RUN_ATTEMPT': '1'})
    assert report['status'] == 'blocked-foreign-lease' and report['exitCode'] == 2
    assert api.put_count() == puts_before, '輸家唔可以寫'
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}
    assert api.state()['leaseOwner'] == 'aircon-diag-777-1'


def test_reservation_cas_conflict_retries_then_wins():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.fail_next_put = 409
    report, _fetch, api = _run(api=api)
    assert report['status'] == 'healthy-without-price'
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_reservation_cas_conflict_exhausted_fails_closed_zero_provider():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.conflict_puts = True
    before = api.files[STATE_PATH][0]
    report, fetch, api = _run(api=api)
    assert report['status'] == 'blocked-reserve-conflict' and report['exitCode'] == 2
    assert api.files[STATE_PATH][0] == before, 'CAS 全輸唔應該改到 state'
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_reservation_write_ambiguous_is_explicit_uncertain():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    before = api.files[STATE_PATH][0]
    api.fail_next_put = 500
    report, fetch, api = _run(api=api)
    assert report['status'] == 'uncertain-reservation' and report['exitCode'] == 1
    assert report['uncertainty']
    assert api.files[STATE_PATH][0] == before
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_reservation_network_ambiguity_is_explicit_uncertain():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.put_exceptions = {1: urllib.error.URLError('network lost')}
    report, fetch, api = _run(api=api)
    assert report['status'] == 'uncertain-reservation' and report['exitCode'] == 1
    assert report['uncertainty']
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_initial_coordinator_read_network_error_blocks_nonzero():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.get_exceptions = {1: urllib.error.URLError('dns failure')}
    report, fetch, api = _run(api=api)
    assert report['status'] == 'blocked-coordinator-read' and report['exitCode'] == 2
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_finalize_network_ambiguity_is_explicit_uncertain_and_keeps_lease():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.put_exceptions = {2: urllib.error.URLError('network lost')}
    report, fetch, api = _run(api=api)
    assert report['status'] == 'uncertain-cleanup' and report['exitCode'] == 1
    state = api.state()
    assert state['leaseOwner'] == 'aircon-diag-777-1'
    assert _marker(state)['state'] == 'reserved'
    assert fetch.REQUEST_STATS == {'token': 1, 'search': 1}


# ---------------------------------------------------------------- finalize safety


def test_finalize_cleanup_failure_is_explicit_and_not_silent():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    api.fail_puts = {2: 500}  # 1st PUT = reservation；2nd PUT = finalize release
    report, fetch, api = _run(api=api)
    assert report['status'] == 'uncertain-cleanup' and report['exitCode'] == 1
    assert report['uncertainty']
    state = api.state()
    assert state['leaseOwner'] == 'aircon-diag-777-1', 'cleanup 失敗唔可以當已釋放'
    assert fetch.REQUEST_STATS == {'token': 1, 'search': 1}
    assert _marker(state)['state'] == 'reserved'


def test_finalize_never_clears_successor_lease():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    flip = {'on': False}
    original = api.transform_get

    def transform(path, data, sha):
        if original is not None:
            data, sha = original(path, data, sha)
        if flip['on'] and path == STATE_PATH:
            state = json.loads(data.decode('utf-8'))
            state['leaseOwner'] = 'successor-writer'
            state['leaseExpiresAt'] = NOW + 10 ** 6
            raw = coord.canonical_json_bytes(state)
            api.files[STATE_PATH] = (raw, 'successor-sha')
            return raw, 'successor-sha'
        return data, sha

    api.transform_get = transform
    fetch = FakeFetch()

    def mark_flip(candidates=None):
        result = FakeFetch.run_smoke(fetch, candidates)
        flip['on'] = True
        return result

    fetch.run_smoke = mark_flip
    report, _fetch, api = _run(api=api, fetch=fetch)
    assert report['status'] == 'uncertain-lease-lost' and report['exitCode'] == 1
    state = api.state()
    assert state['leaseOwner'] == 'successor-writer', '唔可以清走 successor 嘅 lease'
    assert state['leaseExpiresAt'] == NOW + 10 ** 6
    assert state['status'] == 'active'
    assert api.put_count() == 1, 'reservation 之後唔應該再有寫入'


# ---------------------------------------------------------------- op cap


def test_capped_api_raises_after_cap():
    api = FakeGitHubApi()
    capped = diag._CappedApi(api, 2)
    assert capped('GET', 'https://api.github.com/repos/x/y/contents/z') is not None
    assert capped('GET', 'https://api.github.com/repos/x/y/contents/z') is not None
    with pytest.raises(coord.CoordinatorApiError):
        capped('GET', 'https://api.github.com/repos/x/y/contents/z')


def test_request_cap_respected_on_success_path():
    report, _fetch, api = _run(state=_business_state(), cap=diag.MAX_COORDINATOR_REQUESTS)
    assert report['status'] == 'healthy-without-price'
    assert report['coordinatorOps'] <= diag.MAX_COORDINATOR_REQUESTS
    assert len(api.calls) <= diag.MAX_COORDINATOR_REQUESTS


def test_coordinator_cap_exhaustion_fails_closed_zero_provider():
    api = FakeGitHubApi()
    _seed(api, _business_state())
    report, fetch, api = _run(api=api, cap=1)
    assert report['status'].startswith('blocked-') and report['exitCode'] == 2
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


# ---------------------------------------------------------------- loopback provider


def test_loopback_healthy_without_price_one_auth_one_search(loopback):
    def auth(handler, path):
        _respond(handler, 200, json.dumps({'access_token': 'tok-1'}).encode())

    def search(handler, path):
        _respond(handler, 200, json.dumps({'list': []}).encode())

    loopback.routes['/auth/v1/token'] = auth
    loopback.fallback = search
    report, _fetch, api = _run(state=_business_state(), fetch=fetch_biggo)
    assert report['status'] == 'healthy-without-price' and report['exitCode'] == 0
    assert loopback.count('/auth/v1/token') == 1
    assert loopback.count('/api/v1/spa/search/') == 1
    assert report['actual'] == {'token': 1, 'search': 1}
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_loopback_auth_401_stops_before_search(loopback):
    def auth(handler, path):
        _respond(handler, 401, b'{}')

    def search(handler, path):
        _respond(handler, 200, json.dumps({'list': []}).encode())

    loopback.routes['/auth/v1/token'] = auth
    loopback.fallback = search
    report, _fetch, api = _run(state=_business_state(), fetch=fetch_biggo)
    assert report['status'] == 'probe-auth-failed' and report['exitCode'] == 1
    assert loopback.count('/auth/v1/token') == 1
    assert loopback.count('/api/v1/spa/search/') == 0, 'auth 失敗唔可以再搜尋'
    assert report['actual'] == {'token': 1, 'search': 0}
    assert api.state()['requestAttempts'] == {'token': 1, 'search': 1}


def test_loopback_429_honours_retry_after_and_stops(loopback):
    def auth(handler, path):
        _respond(handler, 429, b'{}', {'Retry-After': '45'})

    def search(handler, path):
        _respond(handler, 200, json.dumps({'list': []}).encode())

    loopback.routes['/auth/v1/token'] = auth
    loopback.fallback = search
    report, _fetch, api = _run(state=_business_state(), fetch=fetch_biggo)
    assert report['status'] == 'probe-rate-limited' and report['exitCode'] == 1
    assert loopback.count('/api/v1/spa/search/') == 0
    assert api.state()['cooldownUntil'] == NOW + 45


def test_loopback_invalid_json_is_invalid_response(loopback):
    def auth(handler, path):
        _respond(handler, 200, json.dumps({'access_token': 'tok-1'}).encode())

    def search(handler, path):
        _respond(handler, 200, b'{bad json')

    loopback.routes['/auth/v1/token'] = auth
    loopback.fallback = search
    report, _fetch, _api = _run(state=_business_state(), fetch=fetch_biggo)
    assert report['status'] == 'probe-invalid-response' and report['exitCode'] == 1


def test_loopback_redirect_is_not_replayed(loopback):
    followed = {'n': 0}

    def auth(handler, path):
        _respond(handler, 302, b'', {'Location': '/followed'})

    def followed_handler(handler, path):
        followed['n'] += 1
        _respond(handler, 200, b'{}')

    def search(handler, path):
        _respond(handler, 200, json.dumps({'list': []}).encode())

    loopback.routes['/auth/v1/token'] = auth
    loopback.routes['/followed'] = followed_handler
    loopback.fallback = search
    report, _fetch, _api = _run(state=_business_state(), fetch=fetch_biggo)
    assert report['status'] == 'probe-transport' and report['exitCode'] == 1
    assert followed['n'] == 0, 'redirect 唔可以被跟隨 replay'
    assert loopback.count('/api/v1/spa/search/') == 0


def test_lease_lost_between_phases_forbids_search(loopback):
    api = FakeGitHubApi()
    _seed(api, _business_state())
    flip = {'on': False}

    def auth(handler, path):
        flip['on'] = True
        _respond(handler, 200, json.dumps({'access_token': 'tok-1'}).encode())

    def search(handler, path):
        _respond(handler, 200, json.dumps({'list': []}).encode())

    loopback.routes['/auth/v1/token'] = auth
    loopback.fallback = search

    def transform(path, data, sha):
        if flip['on'] and path == STATE_PATH:
            state = json.loads(data.decode('utf-8'))
            state['leaseOwner'] = 'intruder'
            state['leaseExpiresAt'] = NOW + 10 ** 6
            raw = coord.canonical_json_bytes(state)
            api.files[STATE_PATH] = (raw, 'intruder-sha')
            return raw, 'intruder-sha'
        return data, sha

    api.transform_get = transform
    report, _fetch, api = _run(api=api, fetch=fetch_biggo)
    assert loopback.count('/auth/v1/token') == 1
    assert loopback.count('/api/v1/spa/search/') == 0, 'lease 失去後禁止搜尋'
    assert report['exitCode'] == 1
    assert report['status'] in ('uncertain-lease-lost', 'uncertain-lease-not-confirmed')
    assert api.state()['leaseOwner'] == 'intruder', '唔可以清走 intruder lease'


def test_diagnostic_restores_preexisting_hooks_on_real_fetch_success(loopback):
    loopback.routes['/auth/v1/token'] = lambda h, p: _respond(
        h, 200, json.dumps({'access_token': 'fixture'}).encode())
    loopback.fallback = lambda h, p: _respond(h, 200, json.dumps({'list': []}).encode())
    original_limiter = fetch_biggo.get_request_limiter()
    original_abort = fetch_biggo.get_abort_check()
    original_opener = urllib.request._opener
    sentinel_limiter = object()
    sentinel_abort = lambda: False
    fetch_biggo.set_request_limiter(sentinel_limiter)
    fetch_biggo.set_abort_check(sentinel_abort)
    try:
        report, _fetch, _api = _run(state=_business_state(), fetch=fetch_biggo,
                                    selector=_selector('A1'))
        assert report['exitCode'] == 0
        assert fetch_biggo.get_request_limiter() is sentinel_limiter
        assert fetch_biggo.get_abort_check() is sentinel_abort
        assert urllib.request._opener is original_opener
    finally:
        fetch_biggo.set_request_limiter(original_limiter)
        fetch_biggo.set_abort_check(original_abort)
        urllib.request._opener = original_opener


def test_diagnostic_restores_preexisting_hooks_on_provider_error(loopback):
    loopback.routes['/auth/v1/token'] = lambda h, p: _respond(h, 401, b'{}')
    loopback.fallback = lambda h, p: _respond(h, 200, json.dumps({'list': []}).encode())
    original_limiter = fetch_biggo.get_request_limiter()
    original_abort = fetch_biggo.get_abort_check()
    original_opener = urllib.request._opener
    sentinel_limiter = object()
    sentinel_abort = lambda: False
    fetch_biggo.set_request_limiter(sentinel_limiter)
    fetch_biggo.set_abort_check(sentinel_abort)
    try:
        report, _fetch, _api = _run(state=_business_state(), fetch=fetch_biggo,
                                    selector=_selector('A1'))
        assert report['exitCode'] == 1
        assert fetch_biggo.get_request_limiter() is sentinel_limiter
        assert fetch_biggo.get_abort_check() is sentinel_abort
        assert urllib.request._opener is original_opener
    finally:
        fetch_biggo.set_request_limiter(original_limiter)
        fetch_biggo.set_abort_check(original_abort)
        urllib.request._opener = original_opener


def test_diagnostic_restores_hooks_and_opener_on_systemexit():
    class ExitingFetch(FakeFetch):
        def run_smoke(self, candidates=None):
            raise SystemExit('simulated provider-phase termination')

    fetch = ExitingFetch()
    original_opener = urllib.request._opener
    sentinel_limiter = object()
    sentinel_abort = lambda: False
    sentinel_opener = object()
    fetch.request_limiter = sentinel_limiter
    fetch.abort_check = sentinel_abort
    urllib.request._opener = sentinel_opener
    try:
        with pytest.raises(SystemExit):
            _run(state=_business_state(), fetch=fetch, selector=_selector())
        assert fetch.request_limiter is sentinel_limiter
        assert fetch.abort_check is sentinel_abort
        assert urllib.request._opener is sentinel_opener
    finally:
        urllib.request._opener = original_opener


# ---------------------------------------------------------------- sanitization / locality


def test_sanitized_output_has_no_credentials_or_private_coordinator_identity(monkeypatch):
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', 'SUPERSECRET-XYZ-123')
    env = {
        'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/PRIVATE-COORD-REPO',
        'AIRCON_BIGGO_COORDINATOR_TOKEN': 'PRIVATE-TOKEN-456',
    }
    report, _fetch, _api = _run(state=_business_state(),
                                fetch=FakeFetch(credentials='full'), env=env,
                                selector=_selector(model='bad\nmodel<script>'))
    blob = json.dumps(report, ensure_ascii=False) + diag.render_summary(report)
    for secret in ('SUPERSECRET-XYZ-123', 'owner/PRIVATE-COORD-REPO',
                   'PRIVATE-TOKEN-456'):
        assert secret not in blob, secret
    assert 'bad\nmodel' not in blob and '<script>' not in blob
    assert report['candidate']['model'] == '[filtered]'


def test_diagnostic_makes_no_local_snapshot_or_queue_writes():
    src = open(os.path.join(BASE, 'scripts', 'biggo_smoke_diagnostic.py'),
               encoding='utf-8').read()
    for forbidden in ('biggo_prices.json', 'prices_meta.json', 'model_blacklist.json',
                      'update_queue.json', 'model_status.json', 'biggo_apply',
                      'apply_bundle', 'generate_html'):
        assert forbidden not in src, f'diagnostic 唔應該觸碰 {forbidden}'


def test_summary_is_readable_and_distinguishes_preview_from_live():
    preview, _f, _a = _run(execute=False, selector=_selector())
    summary = diag.render_summary(preview)
    assert 'providersCalled: false' in summary
    assert 'status: `preview`' in summary
    assert 'reservedUpperBound' not in summary
    live, _f2, _a2 = _run(state=_business_state())
    live_summary = diag.render_summary(live)
    assert 'status: `healthy-without-price`' in live_summary
    assert 'providersCalled: true' in live_summary
    assert 'reservedUpperBound: token=1, search=1' in live_summary


def test_main_preview_writes_json_and_summary(tmp_path):
    json_out = tmp_path / 'preview.json'
    summary_out = tmp_path / 'preview.md'
    rc = diag.main(['--json-out', str(json_out), '--summary-out', str(summary_out)])
    assert rc == 0
    report = json.loads(json_out.read_text(encoding='utf-8'))
    assert report['mode'] == 'preview' and report['providersCalled'] is False
    summary = summary_out.read_text(encoding='utf-8')
    assert 'BigGo manual diagnostic' in summary and 'providersCalled: false' in summary


def test_main_unexpected_exception_is_sanitized_nonzero(monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise RuntimeError('SECRET-internal-message')

    monkeypatch.setattr(diag, 'run_diagnostic', boom)
    rc = diag.main([])
    out = capsys.readouterr().out
    assert rc == 1
    assert 'SECRET-internal-message' not in out
    payload = json.loads(out)
    assert payload['status'] == 'uncertain-exception'
    assert payload['exitCode'] == 1
