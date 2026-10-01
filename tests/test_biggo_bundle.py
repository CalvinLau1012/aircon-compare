# -*- coding: utf-8 -*-
"""BigGo stage bundle（P0 交易）離線回歸：bundle schema／binding／immutability／
write-ahead intent／acquire 單調守門。

全部離線：GitHub Contents API 用 in-memory fake；無 BigGo 網絡。
"""
import base64
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_canonical  # noqa: E402
import biggo_coordinator as coord  # noqa: E402


class FakeGitHubApi:
    """In-memory Contents API；同 GitHub 一樣 blob SHA CAS（409 on mismatch）。"""

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
            new_sha = f'sha{self.sha_counter}'
            self.files[path] = (base64.b64decode(payload['content']), new_sha)
            return 201, json.dumps({'content': {'sha': new_sha}}).encode()
        raise AssertionError(f'unsupported {method}')

    def puts(self):
        return [c for c in self.calls if c[0] == 'PUT']

    def state(self):
        raw = self.files.get('coordinator/state.json')
        return json.loads(raw[0].decode('utf-8')) if raw else None


CONFIG = coord.CoordinatorConfig('owner/coordinator', 'tok', branch='main',
                                 state_path='coordinator/state.json',
                                 snapshot_dir='coordinator/snapshots')


def _client(api, owner='writer-a', now=1_000_000):
    contents = coord.GitHubContentsClient(CONFIG, api=api)
    return coord.CoordinatorClient(contents, owner, now=lambda: now)


def _entry(price='$2,500起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}


def _stage_result(cycle='2026-10-01:1/7', stage=1, base=None, new=None, mode='price-batch'):
    base = {'OLD-1': _entry('$1,000起')} if base is None else base
    new = dict(base)
    new['RA-10RF'] = _entry()
    outcomes = [
        {'model': 'RA-10RF', 'canonicalKey': 'HITACHI|RA10RF',
         'outcome': 'priced', 'price': _entry()},
    ]
    pre_tracking = {'HITACHI|OLD': {'misses': 1, 'batch_id': 'old'}}
    post_tracking = dict(pre_tracking)
    post_tracking.pop('HITACHI|OLD', None)
    pre_black = {}
    post_black = {}
    effects = {
        'trackingUpserts': {}, 'trackingRemovals': ['HITACHI|OLD'],
        'blacklistUpserts': {}, 'blacklistRemovals': [],
    }
    return {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': cycle, 'stage': stage, 'mode': mode,
        'generatedAt': '2026-10-01T00:00:00Z',
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'requestAttempts': {'token': 1, 'search': 2},
        'responseStatusCounts': {'200': 2},
        'outcomes': outcomes,
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'effects': effects,
        'preState': {
            'trackingHash': biggo_canonical.sha256_id(
                biggo_canonical.canonical_json_bytes(pre_tracking)),
            'blacklistHash': biggo_canonical.sha256_id(
                biggo_canonical.canonical_json_bytes(pre_black)),
        },
        'postState': {
            'trackingHash': biggo_canonical.sha256_id(
                biggo_canonical.canonical_json_bytes(post_tracking)),
            'blacklistHash': biggo_canonical.sha256_id(
                biggo_canonical.canonical_json_bytes(post_black)),
        },
        'effectsHash': biggo_canonical.sha256_id(
            biggo_canonical.canonical_json_bytes(effects)),
    }


def _manifest_fields(cycle='2026-10-01:1/7', stage=1, mode='price-batch'):
    return {
        'cycleId': cycle, 'stage': stage, 'mode': mode,
        'writer': 'writer-a', 'writerKind': 'github-actions',
        'commit': 'a' * 40, 'publishedAt': '2026-10-01T00:00:00Z',
    }


def _publish(api, owner='writer-a'):
    client = _client(api, owner)
    client.acquire('2026-10-01:1/7', 1)
    sr = _stage_result()
    base = {'OLD-1': _entry('$1,000起')}
    new = dict(base, **{'RA-10RF': _entry()})
    published = client.publish_bundle(base, new, sr, _manifest_fields())
    return client, published, base, new, sr


def test_publish_import_round_trip_and_immutability():
    api = FakeGitHubApi()
    client, published, base, new, sr = _publish(api)
    assert published['newSnapshotHash'].startswith('sha256:')
    imported = client.import_bundle('2026-10-01:1/7', 1)
    assert imported['newSnapshot'] == new
    assert imported['baseSnapshot'] == base
    assert imported['stageResult']['counters'] == {'got': 1, 'cleanMiss': 0, 'netErrors': 0}
    # 再次 publish 相同 bytes → no-op；不同 bytes → 拒絕覆寫
    client2 = _client(api, 'writer-b')
    client2.state = dict(client.state)
    client2._sha = client._sha
    client2.publish_bundle(base, new, sr, _manifest_fields())
    bad = dict(new, **{'RA-10RF': _entry('$9,999起')})
    with pytest.raises(coord.CoordinatorError):
        client2.publish_bundle(base, bad, sr, _manifest_fields())


def test_manifest_binds_base_and_new_hashes():
    api = FakeGitHubApi()
    client, published, base, new, sr = _publish(api)
    man_path = published['manifestPath']
    raw = api.files[man_path][0]
    manifest = json.loads(raw.decode('utf-8'))
    assert manifest['schemaVersion'] == coord.BUNDLE_MANIFEST_SCHEMA_VERSION
    assert manifest['baseSnapshotHash'] == biggo_canonical.sha256_id(
        biggo_canonical.canonical_json_bytes(base))
    assert manifest['newSnapshotHash'] == biggo_canonical.sha256_id(
        biggo_canonical.canonical_json_bytes(new))
    assert manifest['recordCount'] == len(new)


def test_stage_result_rejects_snapshot_not_derived_from_base():
    base = {'OLD-1': _entry('$1,000起')}
    sr = _stage_result(base=base)
    bad_new = {'OLD-1': _entry('$1,000起')}  # 冇 RA-10RF
    with pytest.raises(coord.CoordinatorStateError):
        coord.validate_stage_result(sr, base, bad_new, cycle_id='2026-10-01:1/7', stage=1,
                                    mode='price-batch')


def test_stage_result_keeps_old_snapshot_keys():
    base = {'OLD-1': _entry('$1,000起'), 'OLD-2': _entry('$700起')}
    new = dict(base, **{'RA-10RF': _entry()})
    sr = _stage_result(base=base, new=new)
    coord.validate_stage_result(sr, base, new, cycle_id='2026-10-01:1/7', stage=1,
                                mode='price-batch')


def test_stage_result_rejects_record_count_mismatch():
    base = {'OLD-1': _entry('$1,000起')}
    new = dict(base, **{'RA-10RF': _entry()})
    sr = _stage_result(base=base, new=new)
    sr['counters']['got'] = 2
    with pytest.raises(coord.CoordinatorStateError):
        coord.validate_stage_result(sr, base, new, cycle_id='2026-10-01:1/7', stage=1,
                                    mode='price-batch')


def test_import_rejects_corrupt_snapshot_hash():
    api = FakeGitHubApi()
    client, published, base, new, sr = _publish(api)
    path = published['snapshotPath']
    data, sha = api.files[path]
    api.files[path] = (data[:-1] + b' ', sha)
    with pytest.raises(coord.SnapshotImportError):
        client.import_bundle('2026-10-01:1/7', 1)


def test_legacy_manifest_rejected_for_auto_apply():
    api = FakeGitHubApi()
    contents = coord.GitHubContentsClient(CONFIG, api=api)
    client = coord.CoordinatorClient(contents, 'writer-a', now=lambda: 1_000_000)
    legacy = {'schemaVersion': 1, 'cycleId': '2026-10-01:1/7', 'stage': 1,
              'snapshotHash': biggo_canonical.sha256_id(
                  biggo_canonical.canonical_json_bytes({'X': _entry()}))}
    contents.write_snapshot('coordinator/snapshots/2026-10-01-1-7/manifest.json',
                            coord.canonical_json_bytes(legacy), 'legacy')
    contents.write_snapshot('coordinator/snapshots/2026-10-01-1-7/biggo_prices.json',
                            coord.canonical_json_bytes({'X': _entry()}), 'legacy')
    with pytest.raises(coord.LegacyBundleError):
        client.import_bundle('2026-10-01:1/7', 1)


# ---------------------------------------------------------------- acquire guard


def test_acquire_stale_writer_no_put():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:3/7', 3)
    puts_before = len(api.puts())
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'blocked-incomplete', '未完成 stage 唔可以跨越（含 expired）'
    assert res['reason'] == 'unfinished-active-stage'
    assert len(api.puts()) == puts_before, 'stale writer 唔可以 PUT overwrite 高 stage'


def test_acquire_completed_ahead_no_put():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:3/7', 3)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64,
             snapshot_path='p')
    puts_before = len(api.puts())
    b = _client(api, 'writer-b')
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'completed-ahead'
    assert len(api.puts()) == puts_before


def test_acquire_any_needs_review_blocks_no_put():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='needs_review', last_review_reason='x')
    puts_before = len(api.puts())
    b = _client(api, 'writer-b')
    res = b.acquire('2026-11-01:1/7', 1)
    assert res['result'] == 'blocked-needs-review'
    assert len(api.puts()) == puts_before


def test_acquire_expired_without_intent_is_winner():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    res = b.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'winner'
    assert res['state']['callsMayHaveStarted'] is False


def test_acquire_expired_with_intent_marks_winner_intent():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    assert a.mark_calls_may_have_started()
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    res = b.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'winner-intent'
    assert res['state']['callsMayHaveStarted'] is True


def test_mark_intent_cas_persists_true():
    api = FakeGitHubApi()
    a = _client(api)
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    state, _ = a._contents.read_state()
    assert state['callsMayHaveStarted'] is True
    assert state['intentAt'] == 1_000_000


# ---------------------------------------------------------------- intent 污染回歸（P0 返修）


def test_two_consecutive_stages_have_fresh_intent_and_attempts():
    """stage N completed 之後，stage N+1 必須 fresh intent=false、attempts=0。"""
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    a.record_attempt('search', 3)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    b = _client(api, 'writer-b')
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'winner'
    assert res['state']['callsMayHaveStarted'] is False
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}
    assert res['state']['responseStatusCounts'] == {}


def test_new_cycle_after_completed_has_fresh_attempts():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:7/7', 7)
    a.mark_calls_may_have_started()
    a.record_attempt('search', 5)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    b = _client(api, 'writer-b')
    res = b.acquire('2026-11-01:1/7', 1)
    assert res['result'] == 'winner'
    assert res['state']['callsMayHaveStarted'] is False
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_expired_same_stage_takeover_preserves_intent_and_attempts():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    a.record_attempt('search', 2)
    a.commit()  # persist attempts 先反映到 remote state
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    res = b.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'winner-intent'
    assert res['state']['callsMayHaveStarted'] is True
    assert res['state']['requestAttempts']['search'] == 2


def test_takeover_preserves_cooldown_but_not_attempts():
    """cooldown 係跨 stage 事實；per-stage attempts 唔可以當 provider window 用量帶過去。"""
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.state['cooldownUntil'] = 2_000_000 + 3600
    a.record_attempt('search', 7)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    b = _client(api, 'writer-b')
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'winner'
    assert res['state']['cooldownUntil'] == 2_000_000 + 3600
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_same_owner_renewed_with_intent_returns_winner_intent():
    """同 owner 續接而已有 durable intent：唔可以再當普通 winner 重跑。"""
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    res = a.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'winner-intent'
    assert res['state']['callsMayHaveStarted'] is True


def test_completed_commit_clears_intent():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    state, _ = a._contents.read_state()
    assert state['callsMayHaveStarted'] is False
    assert state['intentAt'] is None


# ---------------------------------------------------------------- 第二輪：唔可以跨越未完成 stage


def test_stage2_blocked_by_active_stage1_same_owner():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    before = dict(api.files)
    res = a.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'blocked-incomplete'
    assert res['reason'] == 'unfinished-active-stage'
    assert api.files == before, '0 PUT：remote state bytes 不變'


def test_stage2_blocked_by_active_stage1_other_owner_expired():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    # 冇 intent 的 expired takeover：都唔可以跨越去 stage2
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    before = dict(api.files)
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'blocked-incomplete'
    assert api.files == before


def test_stage2_blocked_by_expired_stage1_with_intent():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.mark_calls_may_have_started()
    b = _client(api, 'writer-b', now=1_000_000 + coord.LEASE_SECONDS + 1)
    before = dict(api.files)
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'blocked-incomplete'
    assert api.files == before, '未確定呼叫唔可以被新 stage 清走'


def test_force_cannot_jump_active_stage():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    before = dict(api.files)
    res = a.acquire('force-2026-10-01', 1)
    assert res['result'] == 'blocked-incomplete'
    assert api.files == before

    api2 = FakeGitHubApi()
    b = _client(api2, 'writer-a', now=1_000_000)
    b.acquire('force-2026-10-01', 1)
    before2 = dict(api2.files)
    res2 = b.acquire('2026-10-01:2/7', 2)
    assert res2['result'] == 'blocked-incomplete'
    assert api2.files == before2


def test_stage2_blocked_by_idle_stage1():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.abort_without_calls(clear_intent=True)
    before = dict(api.files)
    res = a.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'blocked-incomplete'
    assert res['reason'] == 'unfinished-idle-stage'
    assert api.files == before


def test_same_stage_idle_no_intent_safe_reentry():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.abort_without_calls(clear_intent=True)
    res = a.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'winner'
    assert res['state']['callsMayHaveStarted'] is False
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_idle_with_intent_blocks():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    a.state['status'] = 'idle'
    a.state['callsMayHaveStarted'] = True
    a.state['leaseOwner'] = None
    a.state['leaseExpiresAt'] = 0
    a.commit()
    before = dict(api.files)
    res = a.acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'blocked-incomplete'
    assert res['reason'] == 'same-stage-idle-with-intent'
    assert api.files == before


def test_stage2_blocked_by_active_stage1_other_owner_valid_lease():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    b = _client(api, 'writer-b', now=1_000_000 + 10)
    before = dict(api.files)
    res = b.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'lost', 'active 他人有效 lease：只可 lost，唔可以跨 stage'
    assert api.files == before


# ---------------------------------------------------------------- 第三輪：stage gap 守門


def test_completed_stage1_to_stage2_passes():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    res = a.acquire('2026-10-01:2/7', 2)
    assert res['result'] == 'winner'
    assert res['state']['callsMayHaveStarted'] is False
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_completed_stage1_to_stage3_blocked_gap():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('2026-10-01:3/7', 3)
    assert res['result'] == 'blocked-stage-gap'
    assert res['reason'] == 'stage-skip'
    assert api.files == before, '0 PUT：唔可以跳段'


def test_new_cycle_with_previous_incomplete_blocked():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:3/7', 3)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('2026-11-01:1/7', 1)
    assert res['result'] == 'blocked-stage-gap'
    assert res['reason'] == 'previous-cycle-incomplete'
    assert api.files == before


def test_new_cycle_after_7_of_7_passes():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:7/7', 7)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    res = a.acquire('2026-11-01:1/7', 1)
    assert res['result'] == 'winner'
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_new_cycle_must_start_at_stage1():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:7/7', 7)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('2026-11-01:2/7', 2)
    assert res['result'] == 'blocked-stage-gap'
    assert res['reason'] == 'new-cycle-must-start-at-1'
    assert api.files == before


def test_completed_older_request_completed_ahead_within_cycle():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:5/7', 5)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('2026-10-01:4/7', 4)
    assert res['result'] == 'completed-ahead'
    assert api.files == before


def test_force_after_incomplete_normal_blocked():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:2/7', 2)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('force-2026-10-01', 1)
    assert res['result'] == 'blocked-force-cycle-incomplete', \
        '未完成 monthly cycle 唔可以被 force 覆寫'
    assert api.files == before


def test_normal_same_day_after_force_blocked():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('force-2026-10-01', 1)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    before = dict(api.files)
    res = a.acquire('2026-10-01:3/7', 3)
    assert res['result'] == 'blocked-stale', '同日 normal 唔可以奪回 force state'
    assert api.files == before


def test_force_cannot_bypass_active_or_needs_review():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a', now=1_000_000)
    a.acquire('2026-10-01:1/7', 1)
    before = dict(api.files)
    res = a.acquire('force-2026-10-01', 1)
    assert res['result'] == 'blocked-incomplete'
    assert api.files == before

    api2 = FakeGitHubApi()
    b = _client(api2, 'writer-a', now=1_000_000)
    b.acquire('2026-10-01:1/7', 1)
    b.commit(status='needs_review', last_review_reason='publish-uncertain')
    before2 = dict(api2.files)
    res2 = b.acquire('force-2026-10-01', 1)
    assert res2['result'] == 'blocked-needs-review'
    assert api2.files == before2


# ---------------------------------------------------------------- 第四輪：force cycle 邊界


def _set_completed(api, cycle_id, stage):
    a = _client(api, 'writer-a')
    a.acquire(cycle_id, stage)
    a.commit(status='completed', snapshot_hash='sha256:' + 'a' * 64, snapshot_path='p')
    return dict(api.files)


def test_force_blocked_by_completed_normal_incomplete():
    api = FakeGitHubApi()
    before = _set_completed(api, '2026-10-01:1/7', 1)
    res = _client(api, 'writer-b').acquire('force-2026-10-02', 1)
    assert res['result'] == 'blocked-force-cycle-incomplete'
    assert res['reason'] == 'normal-cycle-incomplete'
    assert api.files == before, '0 PUT：未完成 normal cycle 唔可以被 force 覆寫'


def test_force_allowed_after_completed_normal_7_of_7():
    api = FakeGitHubApi()
    _set_completed(api, '2026-10-01:7/7', 7)
    res = _client(api, 'writer-b').acquire('force-2026-10-02', 1)
    assert res['result'] == 'winner'
    assert res['state']['requestAttempts'] == {'token': 0, 'search': 0}


def test_force_older_than_completed_normal_7_of_7_blocked_stale():
    api = FakeGitHubApi()
    before = _set_completed(api, '2026-10-01:7/7', 7)
    res = _client(api, 'writer-b').acquire('force-2026-09-30', 1)
    assert res['result'] == 'blocked-stale'
    assert res['reason'] == 'force-older-than-normal-cycle'
    assert api.files == before


def test_force_then_same_day_normal_blocked():
    api = FakeGitHubApi()
    before = _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('2026-10-05:1/7', 1)
    assert res['result'] == 'blocked-stale'
    assert res['reason'] == 'same-day-normal-after-force'
    assert api.files == before


def test_force_then_older_normal_blocked():
    api = FakeGitHubApi()
    before = _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('2026-10-01:1/7', 1)
    assert res['result'] == 'blocked-stale'
    assert res['reason'] == 'request-older-than-force-cycle'
    assert api.files == before


def test_force_then_newer_normal_stage1_passes():
    api = FakeGitHubApi()
    _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('2026-11-01:1/7', 1)
    assert res['result'] == 'winner'


def test_force_then_newer_normal_stage2_blocked():
    api = FakeGitHubApi()
    before = _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('2026-11-01:2/7', 2)
    assert res['result'] == 'blocked-stage-gap'
    assert res['reason'] == 'new-cycle-must-start-at-1'
    assert api.files == before


def test_force_then_older_force_blocked():
    api = FakeGitHubApi()
    before = _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('force-2026-10-04', 1)
    assert res['result'] == 'blocked-stale'
    assert res['reason'] == 'request-older-than-force-cycle'
    assert api.files == before


def test_force_then_newer_force_allowed():
    api = FakeGitHubApi()
    _set_completed(api, 'force-2026-10-05', 1)
    res = _client(api, 'writer-b').acquire('force-2026-10-06', 1)
    assert res['result'] == 'winner'


def test_force_same_cycle_idempotent():
    api = FakeGitHubApi()
    _set_completed(api, 'force-2026-10-05', 1)
    before = dict(api.files)
    res = _client(api, 'writer-b').acquire('force-2026-10-05', 1)
    assert res['result'] == 'completed-idempotent'
    assert api.files == before


def test_first_force_without_state_allowed():
    api = FakeGitHubApi()
    res = _client(api, 'writer-b').acquire('force-2026-10-05', 1)
    assert res['result'] == 'winner'
    assert api.state()['cycleId'] == 'force-2026-10-05'


def test_force_blocked_by_idle_normal_stage():
    api = FakeGitHubApi()
    a = _client(api, 'writer-a')
    a.acquire('2026-10-01:1/7', 1)
    a.abort_without_calls(clear_intent=True)
    before = dict(api.files)
    res = a.acquire('force-2026-10-02', 1)
    assert res['result'] == 'blocked-incomplete'
    assert api.files == before
