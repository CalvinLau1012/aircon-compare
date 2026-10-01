# -*- coding: utf-8 -*-
"""BigGo stage runner（P0 stage bundle 交易）離線聚焦測試；完全離線。

覆蓋：inactive 零 API、無配置、meta 錯、lost active 零寫入、stale/completed-ahead 0 PUT、
needs_review、completed-idempotent import+apply、expired intent adopt／needs_review、
winner publish→commit→apply 次序、publish/commit/apply 失敗、lease lost、
cooldown/budget、intent write-ahead、force 無 bypass。
"""
import base64
import json
import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import batch_utils  # noqa: E402
import biggo_apply  # noqa: E402
import biggo_canonical  # noqa: E402
import biggo_coordinator as coord  # noqa: E402
import biggo_stage_runner as runner  # noqa: E402

ACTIVE_META = {'price_batch_start': '2026-10-01', 'price_batch_idx': 0}
CONFIGURED_ENV = {
    'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/coordinator-repo',
    'AIRCON_BIGGO_COORDINATOR_TOKEN': 'tok',
    'AIRCON_BIGGO_LEASE_OWNER': 'me',
}
CONFIG = coord.CoordinatorConfig('owner/coordinator', 'tok', branch='main',
                                 state_path='coordinator/state.json',
                                 snapshot_dir='coordinator/snapshots')


def _entry(price='$2,500起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}


def _h(obj):
    return biggo_canonical.sha256_json(obj)


def _empty_effects():
    return {'trackingUpserts': {}, 'trackingRemovals': [],
            'blacklistUpserts': {}, 'blacklistRemovals': []}


def completed_payload(model='M1'):
    base = {'OLD-1': _entry('$1,000起')}
    new = dict(base, **{model: _entry()})
    pre = {'trackingHash': _h({}), 'blacklistHash': _h({})}
    return {
        'status': 'completed', 'baseSnapshot': base, 'snapshot': new,
        'outcomes': [{'model': model, 'canonicalKey': 'BRAND|M1',
                      'outcome': 'priced', 'price': new[model]}],
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'preState': pre, 'postState': pre, 'effects': _empty_effects(),
    }


def partial_payload():
    base = {'OLD-1': _entry('$1,000起')}
    return {
        'status': 'partial-net-errors', 'baseSnapshot': base, 'snapshot': base,
        'outcomes': [{'model': 'M2', 'canonicalKey': 'BRAND|M2',
                      'outcome': 'net_error'}],
        'counters': {'got': 0, 'cleanMiss': 0, 'netErrors': 1},
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
    }


class FakeGitHubApi:
    def __init__(self):
        self.files = {}
        self.sha_counter = 0
        self.calls = []

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body))
        path = url.split('/contents/', 1)[1].split('?', 1)[0]
        import urllib.parse
        path = urllib.parse.unquote(path)
        if method == 'GET':
            if path not in self.files:
                return 404, b''
            data, sha = self.files[path]
            return 200, json.dumps({
                'content': base64.b64encode(data).decode('ascii'), 'sha': sha}).encode()
        if method == 'PUT':
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

    def put_count(self):
        return sum(1 for c in self.calls if c[0] == 'PUT')

    def state(self):
        raw = self.files.get(CONFIG.state_path)
        return json.loads(raw[0].decode('utf-8')) if raw else None


def _seed_state(api, state):
    data = coord.canonical_json_bytes(state)
    api.files[CONFIG.state_path] = (data, 'seed')


class FakeBatch:
    MetaError = batch_utils.MetaError

    def __init__(self, meta, todo=('M1', 'M2')):
        self.meta = meta
        self.todo = (list(todo), 0, len(todo))

    def load_meta(self):
        return dict(self.meta)

    def get_batch_todo(self, models, meta):
        return self.todo if self.meta.get('price_batch_start') else None


class FakeFetch:
    def __init__(self, tmp_path):
        self.OUT_PATH = str(tmp_path / 'biggo_prices.json')
        self.REQUEST_STATS = {'token': 0, 'search': 0}
        self.LAST_RATE_LIMIT_EVIDENCE = {}
        self.smoke_result = True
        self.batch_payload = completed_payload()
        self.smoke_calls = 0
        self.batch_calls = 0
        self.force_calls = 0
        self.force_kwargs = []
        self.observed_intent_at_smoke = None
        self.batch_delay = 0.0
        self.request_limiter = None
        self.abort_check = None
        self.limiter_history = []
        self.stage_idx = 0

    def load_models(self):
        return ['M1', 'M2']

    def stage_workload(self, meta=None):
        return {'batch': ['M1', 'M2'], 'review': [], 'idx': self.stage_idx, 'total': 2,
                'queuedModels': 2}

    def force_workload(self, limit=None):
        return {'batch': ['M1', 'M2'], 'review': [], 'idx': 0, 'total': 2,
                'queuedModels': 2}

    def set_request_limiter(self, limiter):
        self.request_limiter = limiter
        self.limiter_history.append(limiter.snapshot() if limiter is not None else None)

    def set_abort_check(self, check):
        self.abort_check = check

    def run_smoke(self):
        self.smoke_calls += 1
        self.REQUEST_STATS['token'] += 1
        self.REQUEST_STATS['search'] += 1
        return self.smoke_result

    def run_price_batch(self, should_abort=None):
        self.batch_calls += 1
        if should_abort is not None:
            for _ in range(int(self.batch_delay / 0.001) + 1):
                if should_abort():
                    return {'status': 'aborted', 'projectCooldown': True, 'leaseLost': True}
                time.sleep(0.001)
        self.REQUEST_STATS['search'] += 2
        return self.batch_payload

    def run_force_batch(self, limit=None, *, smoke=True, should_abort=None, exit_on_fail=True):
        self.force_calls += 1
        self.force_kwargs.append({'limit': limit, 'smoke': smoke, 'exit_on_fail': exit_on_fail})
        if should_abort is not None:
            for _ in range(int(self.batch_delay / 0.001) + 1):
                if should_abort():
                    return {'status': 'aborted', 'projectCooldown': True, 'leaseLost': True}
                time.sleep(0.001)
        self.REQUEST_STATS['search'] += 2
        return self.batch_payload


def _run(tmp_path, *, api=None, meta=ACTIVE_META, env=None, fetch=None, now=2_000_000,
         client_hook=None, apply_fn=None, status_out=None, heartbeat_interval=None):
    api = api or FakeGitHubApi()
    fetch = fetch or FakeFetch(tmp_path)
    out = []
    apply_calls = []
    contents = coord.GitHubContentsClient(CONFIG, api=api)

    def factory(config, owner, now_fn):
        client = coord.CoordinatorClient(contents, owner, now=now_fn)
        if client_hook is not None:
            client_hook(client)
        return client

    def default_apply(bundle, *, repo_root, now=None):
        state = api.state()
        assert state is not None and state.get('status') == 'completed', \
            'local apply 只可以在 CAS completed 之後'
        apply_calls.append(bundle)
        return {'noop': False}

    kwargs = {}
    if heartbeat_interval is not None:
        kwargs['heartbeat_interval'] = heartbeat_interval
    code = runner.run_stage(
        env=env if env is not None else CONFIGURED_ENV,
        batch_mod=FakeBatch(meta), fetch_mod=fetch, client_factory=factory,
        now=lambda: now, print_fn=out.append,
        apply_fn=apply_fn or default_apply, repo_root=str(tmp_path),
        status_out=status_out, **kwargs)
    return code, out, apply_calls, fetch, api


def _status(out):
    for line in out:
        if line.startswith('BIGGO_STAGE_STATUS: '):
            return line.split(': ', 1)[1]
    return None


# ---------------------------------------------------------------- skip paths


def test_inactive_stage_zero_coordinator_and_zero_biggo(tmp_path):
    api = FakeGitHubApi()
    fetch = FakeFetch(tmp_path)
    code, out, _, fetch, api = _run(tmp_path, api=api, meta={}, env=CONFIGURED_ENV,
                                    fetch=fetch)
    assert code == 0 and _status(out) == 'skip-not-active'
    assert api.calls == [] and fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_active_without_coordinator_config_makes_zero_calls(tmp_path):
    fetch = FakeFetch(tmp_path)
    code, out, _, fetch, api = _run(tmp_path, meta=ACTIVE_META, env={}, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-coordinator-not-configured'
    assert api.calls == [] and fetch.smoke_calls == 0 and fetch.batch_calls == 0


def test_meta_error_blocks_with_rc2(tmp_path):
    class BadMeta(FakeBatch):
        def load_meta(self):
            raise batch_utils.MetaError('corrupt')

    out = []
    code = runner.run_stage(env=CONFIGURED_ENV, batch_mod=BadMeta({}),
                            fetch_mod=FakeFetch(tmp_path), client_factory=None,
                            now=lambda: 2_000_000, print_fn=out.append,
                            apply_fn=lambda *a, **k: {'noop': False},
                            repo_root=str(tmp_path))
    assert code == 2 and _status(out) == 'blocked-meta-invalid'


def test_lost_active_zero_local_writes_and_zero_biggo(tmp_path):
    api = FakeGitHubApi()
    _seed_state(api, coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 2_000_000))
    puts_before = api.put_count()
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-lost-active'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert apply_calls == [] and api.put_count() == puts_before


def test_stale_writer_zero_put(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:3/7', 3, 'writer-a', 1_000_000)
    _seed_state(api, state)
    puts_before = api.put_count()
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch, now=3_000_000)
    assert code == 0 and _status(out) == 'blocked-incomplete'
    assert api.put_count() == puts_before and apply_calls == []
    assert fetch.smoke_calls == 0


def test_completed_ahead_zero_put(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:3/7', 3, 'writer-a', 1_000_000)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0,
                  'snapshotHash': 'sha256:' + 'a' * 64, 'snapshotPath': 'p'})
    _seed_state(api, state)
    puts_before = api.put_count()
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, now=3_000_000)
    assert code == 0 and _status(out) == 'skip-completed-ahead'
    assert api.put_count() == puts_before and apply_calls == []


def test_prior_needs_review_blocks_without_calls(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state.update({'status': 'needs_review', 'leaseOwner': None, 'leaseExpiresAt': 0,
                  'lastReviewReason': 'publish-uncertain'})
    _seed_state(api, state)
    puts_before = api.put_count()
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert api.put_count() == puts_before and fetch.smoke_calls == 0
    assert apply_calls == []


def test_cooldown_skip_before_intent(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['cooldownUntil'] = 2_000_000 + 3600
    _seed_state(api, state)
    puts_before = api.put_count()
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-cooldown'
    assert fetch.smoke_calls == 0 and apply_calls == []
    remote = api.state()
    assert remote['callsMayHaveStarted'] is False, 'cooldown skip 前唔可以寫 intent'
    assert api.put_count() > puts_before  # abort_without_calls 釋放 lease


def test_budget_skip_before_intent(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['requestAttempts'] = {'token': 5, 'search': 99}
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-budget'
    assert fetch.smoke_calls == 0 and apply_calls == []
    assert api.state()['callsMayHaveStarted'] is False


# ---------------------------------------------------------------- intent / recovery


def test_expired_without_intent_is_safe_takeover(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch, now=3_000_000)
    assert code == 0 and _status(out) == 'completed'
    assert fetch.smoke_calls == 1 and fetch.batch_calls == 1
    assert len(apply_calls) == 1
    assert api.state()['status'] == 'completed'


def test_expired_with_intent_no_bundle_needs_review_zero_biggo(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state['callsMayHaveStarted'] = True
    state['intentAt'] = 1_000_000
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch, now=3_000_000)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert apply_calls == []
    remote = api.state()
    assert remote['status'] == 'needs_review'
    assert remote['lastReviewReason'] == 'expired-with-intent-no-valid-bundle'


def _publish_bundle(api, *, cycle='2026-10-01:1/7', stage=1, commit_completed=False):
    client = coord.CoordinatorClient(coord.GitHubContentsClient(CONFIG, api=api),
                                     'writer-a', now=lambda: 1_000_000)
    client.acquire(cycle, stage)
    payload = completed_payload()
    sr = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': cycle, 'stage': stage, 'mode': 'price-batch',
        'generatedAt': '2026-10-01T00:00:00Z',
        'counters': payload['counters'],
        'requestAttempts': {'token': 1, 'search': 2},
        'responseStatusCounts': {'200': 2},
        'outcomes': payload['outcomes'],
        'blacklistReview': payload['blacklistReview'],
        'effects': payload['effects'], 'preState': payload['preState'],
        'postState': payload['postState'],
        'effectsHash': _h(payload['effects']),
    }
    published = client.publish_bundle(payload['baseSnapshot'], payload['snapshot'], sr, {
        'cycleId': cycle, 'stage': stage, 'mode': 'price-batch',
        'writer': 'writer-a', 'writerKind': 'github-actions', 'commit': 'a' * 40,
        'publishedAt': '2026-10-01T00:00:00Z'})
    if commit_completed:
        client.commit(status='completed', snapshot_hash=published['newSnapshotHash'],
                      snapshot_path=published['snapshotPath'],
                      stage_result_hash=published['stageResultHash'],
                      bundle_hash=published['bundleHash'])
    return published


def test_expired_with_intent_complete_bundle_adopts_zero_biggo(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state['callsMayHaveStarted'] = True
    _seed_state(api, state)
    _publish_bundle(api)  # 已 publish、未 commit
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch, now=3_000_000)
    assert code == 0 and _status(out) == 'completed-recovered'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert len(apply_calls) == 1
    assert api.state()['status'] == 'completed'


def test_expired_with_intent_corrupt_bundle_needs_review_no_calls(tmp_path):
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state['callsMayHaveStarted'] = True
    _seed_state(api, state)
    published = _publish_bundle(api)
    path = published['paths']['stageResult']
    data, sha = api.files[path]
    api.files[path] = (data[:-1] + b' ', sha)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch, now=3_000_000)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    assert api.state()['lastReviewReason'] == 'expired-with-intent-no-valid-bundle'


def test_completed_idempotent_imports_and_applies(tmp_path):
    api = FakeGitHubApi()
    published = _publish_bundle(api, commit_completed=True)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'completed-idempotent-applied'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert len(apply_calls) == 1
    assert apply_calls[0]['newSnapshotHash'] == published['newSnapshotHash']


def test_completed_idempotent_legacy_bundle_manual(tmp_path):
    api = FakeGitHubApi()
    client = coord.CoordinatorClient(coord.GitHubContentsClient(CONFIG, api=api),
                                     'writer-a', now=lambda: 1_000_000)
    legacy = {'schemaVersion': 1, 'cycleId': '2026-10-01:1/7', 'stage': 1,
              'snapshotHash': _h(completed_payload()['snapshot'])}
    coord.GitHubContentsClient(CONFIG, api=api).write_snapshot(
        'coordinator/snapshots/2026-10-01-1-7/manifest.json',
        coord.canonical_json_bytes(legacy), 'legacy')
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'completed-legacy-manual'
    assert apply_calls == [] and fetch.smoke_calls == 0


# ---------------------------------------------------------------- winner path / faults


def test_winner_publishes_then_commits_then_applies(tmp_path):
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch)
    assert code == 0 and _status(out) == 'completed'
    assert fetch.smoke_calls == 1 and fetch.batch_calls == 1
    assert len(apply_calls) == 1
    state = api.state()
    assert state['status'] == 'completed'
    assert state['callsMayHaveStarted'] is False, 'completed 後 per-stage intent 要清'
    assert state['snapshotHash'] == apply_calls[0]['newSnapshotHash']


def test_winner_publish_failure_marks_needs_review_no_apply(tmp_path):
    def hook(client):
        def boom(base, new, sr, mf):
            raise coord.CoordinatorError('simulated publish failure')
        client.publish_bundle = boom

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, client_hook=hook)
    assert code == 0 and _status(out) == 'needs-review'
    assert apply_calls == []
    assert api.state()['status'] == 'needs_review'
    assert api.state()['lastReviewReason'] == 'publish-uncertain'


def test_winner_commit_failure_marks_needs_review_no_apply(tmp_path):
    def hook(client):
        real = client.commit

        def flaky(**kwargs):
            if kwargs.get('status') == 'completed':
                raise coord.CoordinatorError('simulated completed-commit failure')
            return real(**kwargs)

        client.commit = flaky

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, client_hook=hook)
    assert code == 0 and _status(out) == 'needs-review'
    assert apply_calls == []
    assert api.state()['lastReviewReason'] == 'commit-uncertain'
    assert api.state()['status'] == 'needs_review'


def test_winner_commit_outage_still_no_apply(tmp_path):
    def hook(client):
        def boom(**kwargs):
            raise coord.CoordinatorError('commit backend down')

        client.commit = boom

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, client_hook=hook)
    assert code == 0 and _status(out) == 'needs-review'
    assert apply_calls == []
    state = api.state()
    assert state['status'] != 'completed'
    assert state['callsMayHaveStarted'] is True


def test_winner_apply_failure_alerts_and_remote_completed(tmp_path):
    def failing_apply(bundle, *, repo_root, now=None):
        raise biggo_apply.ApplyError('simulated apply failure')

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, apply_fn=failing_apply)
    assert code == 0 and _status(out) == 'completed-apply-failed'
    assert api.state()['status'] == 'completed'
    assert api.state()['snapshotHash']


def test_winner_lease_lost_during_batch_no_publish_no_apply(tmp_path):
    def hook(client):
        calls = {'n': 0}

        def killing_renew():
            calls['n'] += 1
            if calls['n'] > 1:
                raise coord.LeaseLostError('simulated lease loss')

        client.maybe_renew = killing_renew

    fetch = FakeFetch(tmp_path)
    fetch.batch_delay = 0.05
    code, out, apply_calls, fetch, api = _run(
        tmp_path, fetch=fetch, client_hook=hook, heartbeat_interval=0.005)
    assert code == 0 and _status(out) == 'needs-review'
    assert apply_calls == []
    state = api.state()
    assert state['status'] == 'needs_review'
    assert state.get('snapshotHash') is None


def test_winner_intent_is_persisted_before_smoke(tmp_path):
    observed = {}

    class IntentProbe(FakeFetch):
        def run_smoke(self):
            observed['state'] = api.state()
            return super().run_smoke()

    api = FakeGitHubApi()
    fetch = IntentProbe(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'completed'
    assert observed['state']['callsMayHaveStarted'] is True, \
        '任何真實 BigGo 請求之前 intent 必須已 CAS 持久化'


def test_smoke_failure_after_calls_needs_review_no_apply(tmp_path):
    fetch = FakeFetch(tmp_path)
    fetch.smoke_result = False
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.batch_calls == 0 and apply_calls == []
    assert api.state()['lastReviewReason'] == 'smoke-failed'


def test_batch_partial_needs_review_no_publish_no_apply(tmp_path):
    fetch = FakeFetch(tmp_path)
    fetch.batch_payload = partial_payload()
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert apply_calls == []
    state = api.state()
    assert state.get('snapshotHash') is None
    assert state['status'] == 'needs_review'


def test_status_artifact_written_and_sanitized(tmp_path):
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, status_out=status_path)
    assert code == 0
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['status'] == 'completed'
    assert record['alert'] is False
    assert record['localApply'] == 'applied'
    assert record['requests']['search'] >= 1
    assert 'Bearer' not in json.dumps(record)
    assert 'AIRCON_BIGGO' not in json.dumps(record)


def test_status_artifact_alert_for_needs_review(tmp_path):
    status_path = str(tmp_path / 'biggo-stage-status.json')
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state.update({'status': 'needs_review', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, status_out=status_path)
    assert code == 0
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['status'] == 'needs-review'
    assert record['alert'] is True
    assert record['rerun'] == 'prohibited'


# ---------------------------------------------------------------- force mode


def test_force_mode_acquires_lease_and_publishes(tmp_path):
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(
        tmp_path, meta={}, fetch=fetch,
        env={**CONFIGURED_ENV, 'AIRCON_BIGGO_FORCE_STAGE': 'true'})
    assert code == 0 and _status(out) == 'completed'
    assert fetch.force_calls == 1 and fetch.batch_calls == 0
    assert fetch.force_kwargs[0]['smoke'] is False
    assert fetch.force_kwargs[0]['exit_on_fail'] is False
    assert len(apply_calls) == 1
    assert api.state()['status'] == 'completed'


def test_force_mode_without_coordinator_config_zero_calls(tmp_path):
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(
        tmp_path, meta={}, fetch=fetch, env={'AIRCON_BIGGO_FORCE_STAGE': '1'})
    assert code == 0 and _status(out) == 'skip-coordinator-not-configured'
    assert fetch.smoke_calls == 0 and fetch.force_calls == 0


def test_intent_persist_failure_blocks_requests_rc2(tmp_path):
    def hook(client):
        def boom():
            raise coord.CoordinatorError('intent CAS backend down')

        client.mark_calls_may_have_started = boom

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, client_hook=hook)
    assert code == 2 and _status(out) == 'blocked-intent-persist'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []


def test_intent_lease_lost_makes_no_requests(tmp_path):
    def hook(client):
        def boom():
            raise coord.LeaseLostError('lease gone before intent')

        client.mark_calls_may_have_started = boom

    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, client_hook=hook)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []


def test_inactive_status_artifact_zero_requests(tmp_path):
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, meta={}, fetch=fetch,
                                             status_out=status_path)
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert code == 0 and record['status'] == 'skip-not-active'
    assert record['requests'] == {'token': 0, 'search': 0}
    assert record['alert'] is False
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


# ---------------------------------------------------------------- 首輪返修：renewed intent／status／limiter


def test_renewed_with_intent_no_bundle_needs_review_zero_biggo(tmp_path):
    """同 owner active 而已有 durable intent：唔可以當 winner 重跑。"""
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['callsMayHaveStarted'] = True
    state['intentAt'] = 2_000_000
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    assert api.state()['lastReviewReason'] == 'expired-with-intent-no-valid-bundle'


def test_renewed_with_intent_complete_bundle_adopts_zero_biggo(tmp_path):
    api = FakeGitHubApi()
    _publish_bundle(api)  # writer-a 已 publish、未 commit
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['callsMayHaveStarted'] = True
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'completed-recovered'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert len(apply_calls) == 1
    assert api.state()['status'] == 'completed'


def test_needs_review_status_artifact_has_reason_and_requests(tmp_path):
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    fetch.smoke_result = False
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch, status_out=status_path)
    assert code == 0
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['status'] == 'needs-review'
    assert record['alert'] is True
    assert record['rerun'] == 'prohibited'
    assert record['reason'] == 'smoke-failed'
    assert record['requests'] == {'token': 1, 'search': 1}


def test_request_limiter_cap_from_workload_and_cleared(tmp_path):
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, fetch=fetch)
    assert code == 0
    assert fetch.request_limiter is None and fetch.abort_check is None, 'run 後必須清 limiter'
    installed = fetch.limiter_history[0]
    assert installed == {'searchUsed': 0, 'searchCap': 5, 'tokenUsed': 0, 'tokenCap': 1,
                         'providerCap': None, 'reached': False}


def test_provider_quota_config_blocks_zero_biggo_and_zero_coordinator(tmp_path):
    """provider quota 配置存在、但冇共享 window ledger → fail-closed、零 BigGo。"""
    fetch = FakeFetch(tmp_path)
    env = {**CONFIGURED_ENV, 'AIRCON_BIGGO_PROVIDER_QUOTA_LIMIT': '100'}
    status_path = str(tmp_path / 'biggo-stage-status.json')
    code, out, apply_calls, fetch, api = _run(tmp_path, env=env, fetch=fetch,
                                             status_out=status_path)
    assert code == 0 and _status(out) == 'blocked-quota-window-unsupported'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    assert fetch.request_limiter is None
    assert api.calls == [], '配置檢查在 acquire 之前，零 coordinator mutation'
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['alert'] is True and record['rerun'] == 'prohibited'
    assert record['reason'] == 'quota-window-accounting-unsupported'


def test_provider_window_end_config_blocks_zero_biggo(tmp_path):
    fetch = FakeFetch(tmp_path)
    env = {**CONFIGURED_ENV, 'AIRCON_BIGGO_PROVIDER_WINDOW_END': '9999999999'}
    code, out, apply_calls, fetch, api = _run(tmp_path, env=env, fetch=fetch)
    assert code == 0 and _status(out) == 'blocked-quota-window-unsupported'
    assert fetch.smoke_calls == 0 and api.calls == []


def test_legacy_state_provider_quota_blocks_winner_before_requests(tmp_path):
    """Env 冇 quota，但 legacy state 帶 providerQuotaLimit → winner 路徑 fail-closed。"""
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000,
                                provider_quota_limit=10)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    fetch = FakeFetch(tmp_path)
    fetch.stage_idx = 1  # 請求 stage 2
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch)
    assert code == 0 and _status(out) == 'blocked-quota-window-unsupported'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []


def test_stage2_blocked_by_active_stage1_with_intent_zero_biggo(tmp_path):
    """remote 未完成 stage1（帶 intent）→ stage2 唔可以跨越；alert artifact。"""
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['callsMayHaveStarted'] = True
    _seed_state(api, state)
    puts_before = api.put_count()
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    fetch.stage_idx = 1  # 請求 stage 2
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch,
                                             status_out=status_path)
    assert code == 0 and _status(out) == 'blocked-incomplete'
    assert api.put_count() == puts_before, '0 PUT：唔可以清走未完成 stage 的 intent'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['alert'] is True and record['rerun'] == 'prohibited'
    assert record['reason'] == 'unfinished-active-stage'


def test_blocked_incomplete_guard_passes_but_alert_red(tmp_path, monkeypatch):
    """blocked-incomplete：四檔零 diff → guard pass（daily 保持 success）；alert 紅。"""
    import verify_biggo_stage_artifacts as guard

    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'me', 2_000_000)
    state['callsMayHaveStarted'] = True
    _seed_state(api, state)
    for name in ('biggo_prices.json', 'prices_meta.json', 'model_blacklist.json',
                 'model_status.json'):
        with open(os.path.join(str(tmp_path), name), 'w', encoding='utf-8') as f:
            f.write('{}')
    before = {name: open(os.path.join(str(tmp_path), name), 'rb').read()
              for name in ('biggo_prices.json', 'prices_meta.json',
                           'model_blacklist.json', 'model_status.json')}
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    fetch.stage_idx = 1
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch,
                                             status_out=status_path)
    assert code == 0
    for name, data in before.items():
        assert open(os.path.join(str(tmp_path), name), 'rb').read() == data, name
    monkeypatch.setattr(guard, '_changed_paths', lambda repo: [])
    assert guard.run_guard(str(tmp_path), status_path) == 0, '無 canonical diff → daily 綠'
    assert guard.run_alert(status_path) == 1, 'BigGo 獨立 alert 必須紅'


def test_blocked_stage_gap_zero_biggo_and_alert(tmp_path):
    """completed stage1 → 請求 stage3：唔可以跳段；0 BigGo、紅 alert。"""
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    fetch.stage_idx = 2  # 請求 stage 3
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch,
                                             status_out=status_path)
    assert code == 0 and _status(out) == 'blocked-stage-gap'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['alert'] is True and record['rerun'] == 'prohibited'
    assert record['reason'] == 'stage-skip'
    assert record['remoteStage'] == 1


def test_blocked_force_cycle_incomplete_zero_biggo_and_alert(tmp_path):
    """remote normal 1/7 → force 唔可以覆寫；0 BigGo、紅 alert。"""
    api = FakeGitHubApi()
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', 1_000_000)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(
        tmp_path, api=api, meta={}, fetch=fetch,
        env={**CONFIGURED_ENV, 'AIRCON_BIGGO_FORCE_STAGE': '1'},
        status_out=status_path)
    assert code == 0 and _status(out) == 'blocked-force-cycle-incomplete'
    assert fetch.smoke_calls == 0 and fetch.force_calls == 0 and apply_calls == []
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['alert'] is True and record['rerun'] == 'prohibited'
    assert record['reason'] == 'normal-cycle-incomplete'


def test_blocked_stale_zero_biggo_and_alert(tmp_path):
    """remote completed force 2026-10-05 → 較舊 normal 唔可以奪回；0 BigGo、紅 alert。"""
    api = FakeGitHubApi()
    state = coord.initial_state('force-2026-10-05', 1, 'writer-a', 1_000_000)
    state.update({'status': 'completed', 'leaseOwner': None, 'leaseExpiresAt': 0})
    _seed_state(api, state)
    status_path = str(tmp_path / 'biggo-stage-status.json')
    fetch = FakeFetch(tmp_path)
    code, out, apply_calls, fetch, api = _run(tmp_path, api=api, fetch=fetch,
                                             status_out=status_path)
    assert code == 0 and _status(out) == 'blocked-stale'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0 and apply_calls == []
    with open(status_path, encoding='utf-8') as f:
        record = json.load(f)
    assert record['alert'] is True and record['rerun'] == 'prohibited'
    assert record['reason'] == 'request-older-than-force-cycle'
