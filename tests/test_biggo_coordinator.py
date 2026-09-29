# -*- coding: utf-8 -*-
"""BigGo coordinator（approved design C）聚焦測試：CAS、lease、quota、安全證據、import。

全部離線：GitHub Contents API 用 in-memory fake HTTP；BigGo 網絡完全唔存在。
"""
import base64
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_coordinator as coord  # noqa: E402


class FakeGitHubApi:
    """In-memory Contents API（同 GitHub 一樣用 blob SHA CAS；409 on mismatch）。"""

    def __init__(self):
        self.files = {}
        self.sha_counter = 0
        self.calls = []
        self.fail_next_put = None

    def __call__(self, method, url, body=None, headers=None):
        self.calls.append((method, url, body))
        path = url.split('/contents/', 1)[1].split('?', 1)[0]
        path = path.replace('%2F', '/') if '%2F' in path else path
        import urllib.parse
        path = urllib.parse.unquote(path)
        if self.fail_next_put is not None and method == 'PUT':
            status = self.fail_next_put
            self.fail_next_put = None
            return status, b''
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
            new_sha = f'sha{self.sha_counter}'
            self.files[path] = (base64.b64decode(payload['content']), new_sha)
            return 201, json.dumps({'content': {'sha': new_sha}}).encode()
        raise AssertionError(f'unsupported {method}')


CONFIG = coord.CoordinatorConfig('owner/coordinator', 'tok', branch='main',
                                 state_path='coordinator/state.json',
                                 snapshot_dir='coordinator/snapshots')


def _client(api, owner, now):
    contents = coord.GitHubContentsClient(CONFIG, api=api)
    return coord.CoordinatorClient(contents, owner, now=now)


def _clock(start=1_000_000):
    box = {'t': start}
    return box, lambda: box['t']


def _snapshot():
    return {'RA-10RF': {'price': '$2,500-3,680', 'merchants': 2,
                        'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}}


# ---------------------------------------------------------------- CAS／lease


def test_lease_cas_one_winner_then_loser():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    b = _client(api, 'writer-b', now)
    assert a.acquire('2026-10-01:1/7', 1)['result'] == 'winner'
    assert b.acquire('2026-10-01:1/7', 1)['result'] == 'lost'
    # loser 冇寫過 state
    state = json.loads(api.files['coordinator/state.json'][0].decode())
    assert state['leaseOwner'] == 'writer-a'


def test_acquire_cas_409_then_retries_and_wins_expired_lease():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    box['t'] += coord.LEASE_SECONDS + 1  # lease expired
    api.fail_next_put = 409  # 模擬 takeover 寫入時輸一場 CAS
    b = _client(api, 'writer-b', now)
    result = b.acquire('2026-10-01:1/7', 1)
    assert result['result'] == 'winner', '409 後重讀，expired lease 可以 takeover'
    assert result['state']['leaseOwner'] == 'writer-b'


def test_renewal_only_after_interval_and_extends_expiry():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    writes_before = len([c for c in api.calls if c[0] == 'PUT'])
    assert a.maybe_renew() is None, '不足 5 分鐘唔應該 renew'
    assert len([c for c in api.calls if c[0] == 'PUT']) == writes_before
    box['t'] += coord.RENEW_INTERVAL_SECONDS
    renewed = a.maybe_renew()
    assert renewed is not None
    assert renewed['leaseRenewedAt'] == box['t']
    assert renewed['leaseExpiresAt'] == box['t'] + coord.LEASE_SECONDS


def test_expired_lease_takeover_by_new_owner():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    box['t'] += coord.LEASE_SECONDS + 1
    b = _client(api, 'writer-b', now)
    result = b.acquire('2026-10-01:1/7', 1)
    assert result['result'] == 'winner'
    assert result['state']['leaseOwner'] == 'writer-b'


def test_completed_cycle_is_idempotent_and_no_rerun():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64,
             snapshot_path='coordinator/snapshots/2026-10-01-1-7/biggo_prices.json')
    b = _client(api, 'writer-b', now)
    assert b.acquire('2026-10-01:1/7', 1)['result'] == 'completed-idempotent'


def test_needs_review_prohibits_automatic_rerun():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='needs_review', last_review_reason='publish-uncertain')
    b = _client(api, 'writer-b', now)
    result = b.acquire('2026-10-01:1/7', 1)
    assert result['result'] == 'needs-review'
    assert result['state']['lastReviewReason'] == 'publish-uncertain'


def test_new_cycle_starts_after_completed_previous_cycle():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='completed', snapshot_hash='sha256:' + 'b' * 64)
    b = _client(api, 'writer-b', now)
    result = b.acquire('2026-10-01:2/7', 2)
    assert result['result'] == 'winner'
    assert result['state']['stage'] == 2


# ---------------------------------------------------------------- budget／quota


def test_computed_local_cap_is_one_smoke_plus_two_per_model():
    assert coord.compute_local_cap(10) == 21
    assert coord.compute_local_cap([]) == 1
    assert coord.compute_local_cap(range(7)) == 15


def test_quota_80_percent_cap_and_budget_minimum():
    assert coord.compute_quota_cap(1000) == 800
    assert coord.compute_quota_cap(None) is None
    with pytest.raises(coord.CoordinatorConfigError):
        coord.compute_quota_cap(0)
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    state = coord.initial_state('2026-10-01:1/7', 1, 'writer-a', box['t'],
                                provider_quota_limit=10)
    state['requestAttempts']['search'] = 8
    assert coord.compute_local_cap(100) == 201
    assert coord.compute_quota_cap(state['providerQuotaLimit']) == 8
    assert state['requestAttempts']['search'] >= coord.compute_quota_cap(10)
    # budget（有效上限 = min(local, quota80)）
    api.files['coordinator/state.json'] = (coord.canonical_json_bytes(state), 'sha-x')
    a.state = state
    a._sha = 'sha-x'
    b = a.budget(100)
    assert b['effectiveSearchCap'] == 8
    assert b['allowed'] is False


def test_cooldown_honours_retry_after_else_project_48h():
    now = 1_000_000
    until, evidence, basis = coord.cooldown_until_for(429, {'Retry-After': '120'}, now)
    assert until == now + 120 and basis == 'retry-after'
    assert evidence == {'retry-after': '120'}
    until, evidence, basis = coord.cooldown_until_for(403, {}, now)
    assert until == now + coord.PROJECT_COOLDOWN_SECONDS
    assert basis == 'project-48h', '冇 Retry-After 用項目 48h fallback'
    assert coord.PROJECT_COOLDOWN_SECONDS == 48 * 3600
    assert coord.cooldown_until_for(200, {}, now) == (0, {}, None)


def test_safe_rate_limit_evidence_allowlist_only():
    headers = {
        'Retry-After': '90', 'X-RateLimit-Remaining': '7',
        'Authorization': 'Bearer SECRET', 'Cookie': 'session=SECRET',
        'X-Auth-Token': 'SECRET', 'Content-Type': 'application/json',
    }
    evidence = coord.safe_rate_limit_evidence(headers)
    assert evidence == {'retry-after': '90', 'x-ratelimit-remaining': '7'}
    serialized = json.dumps(evidence)
    assert 'SECRET' not in serialized and 'authorization' not in serialized.lower()


# ---------------------------------------------------------------- snapshot publish/import


def test_publish_and_import_snapshot_round_trip():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    out = a.publish_snapshot(_snapshot(), {'cycleId': '2026-10-01:1/7', 'stage': 1})
    assert out['snapshotHash'].startswith('sha256:')
    assert out['manifestPath'].endswith('manifest.json') and out['manifestPath'] in api.files
    b = _client(api, 'writer-b', now)
    imported = b.import_snapshot('2026-10-01:1/7', 1)
    assert imported['snapshot'] == _snapshot()
    assert imported['snapshotHash'] == out['snapshotHash']


def test_import_rejects_hash_mismatch_and_wrong_cycle():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    a.publish_snapshot(_snapshot(), {'cycleId': '2026-10-01:1/7', 'stage': 1})
    # 篡改 snapshot bytes：hash 對唔上 manifest
    path = 'coordinator/snapshots/2026-10-01-1-7/biggo_prices.json'
    tampered = json.dumps({'RA-10RF': {'price': '$1', 'merchants': 1,
                                       'url': 'https://biggo.hk/x', 'updated': '2026-09-28'}})
    api.files[path] = (tampered.encode(), 'sha-tampered')
    b = _client(api, 'writer-b', now)
    with pytest.raises(coord.SnapshotImportError, match='sha256'):
        b.import_snapshot('2026-10-01:1/7', 1)
    # manifest 存在但 stage 唔 match → 唔可以匯入
    man_path = 'coordinator/snapshots/2026-10-01-1-7/manifest.json'
    manifest = json.loads(api.files[man_path][0].decode())
    manifest['stage'] = 2
    api.files[man_path] = (json.dumps(manifest).encode(), 'sha-man')
    with pytest.raises(coord.SnapshotImportError, match='match'):
        b.import_snapshot('2026-10-01:1/7', 1)


def test_import_rejects_invalid_snapshot_schema():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    bad = {'RA-10RF': {'price': 2500, 'merchants': 0, 'url': 'ftp://x', 'updated': 'x'}}
    with pytest.raises(coord.CoordinatorStateError):
        a.publish_snapshot(bad, {'cycleId': '2026-10-01:1/7', 'stage': 1})


def test_publish_refuses_same_cycle_different_snapshot():
    api = FakeGitHubApi()
    box, now = _clock()
    a = _client(api, 'writer-a', now)
    a.acquire('2026-10-01:1/7', 1)
    a.publish_snapshot(_snapshot(), {'cycleId': '2026-10-01:1/7', 'stage': 1})
    other = dict(_snapshot())
    other['RA-10RF'] = dict(other['RA-10RF'], price='$9,999')
    with pytest.raises(coord.CoordinatorError, match='拒絕覆蓋'):
        a.publish_snapshot(other, {'cycleId': '2026-10-01:1/7', 'stage': 1})


def test_state_schema_rejects_malformed_states():
    good = coord.initial_state('2026-10-01:1/7', 1, 'w', 100)
    coord.validate_state(good)
    for mutate in (
        lambda s: s.update({'status': 'weird'}),
        lambda s: s.update({'stage': 0}),
        lambda s: s['requestAttempts'].update({'search': -1}),
        lambda s: s.update({'snapshotHash': 'sha256:XYZ'}),
        lambda s: s.update({'providerQuotaLimit': -5}),
        lambda s: s['responseStatusCounts'].update({'abc': 1}),
    ):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        with pytest.raises(coord.CoordinatorStateError):
            coord.validate_state(bad)


def test_config_from_env_requires_no_hardcoded_private_identifiers():
    assert coord.CoordinatorConfig.from_env({}) is None
    with pytest.raises(coord.CoordinatorConfigError):
        coord.CoordinatorConfig.from_env({'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/repo'})
    cfg = coord.CoordinatorConfig.from_env({
        'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/repo',
        'AIRCON_BIGGO_COORDINATOR_TOKEN': 'tok',
    })
    assert cfg.state_path == coord.STATE_PATH_DEFAULT
    assert cfg.provider_quota_limit is None, 'provider quota 未有事實證據前 = None'
    assert coord.config_status({}) == {'configured': False,
                                       'reason': 'coordinator-not-configured'}
    source = open(os.path.join(BASE, 'scripts', 'biggo_coordinator.py'),
                  encoding='utf-8').read()
    assert 'github.com/Calvin' not in source
    for line in source.splitlines():
        if 'github.com/' in line:
            assert 'api.github.com' in line, f'只准 GitHub API host：{line.strip()}'


def test_retry_after_parses_delta_seconds_and_http_date():
    import email.utils
    now = 1_700_000_000
    assert coord.parse_retry_after('120', now=now) == 120
    http_date = email.utils.formatdate(now + 300, usegmt=True)
    assert coord.parse_retry_after(http_date, now=now) == 300
    assert coord.parse_retry_after('not-a-date', now=now) is None
    assert coord.parse_retry_after(None, now=now) is None
    until, _evidence, basis = coord.cooldown_until_for(
        429, {'Retry-After': http_date}, now)
    assert until == now + 300 and basis == 'retry-after'
    until2, _e2, basis2 = coord.cooldown_until_for(
        429, {'Retry-After': 'not-a-date'}, now)
    assert until2 == now + coord.PROJECT_COOLDOWN_SECONDS
    assert basis2 == 'project-48h', '無法解析 reset 時只可用項目 fallback，唔猜 provider window'
