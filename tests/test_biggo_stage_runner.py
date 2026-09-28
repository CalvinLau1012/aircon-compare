# -*- coding: utf-8 -*-
"""BigGo stage runner（approved design C orchestration）聚焦測試；完全離線。

- inactive 階段 → 零 token／search／coordinator mutation；
- active 但冇 coordinator 配置 → 保留快照、零 BigGo 呼叫；
- lease winner／loser／idempotent／needs_review／cooldown／budget 全路徑；
- 任何已呼叫但 snapshot 未確定 → needs_review（禁止自動重跑）。
"""
import json
import os
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import batch_utils  # noqa: E402
import biggo_stage_runner as runner  # noqa: E402
from biggo_coordinator import LeaseLostError, SnapshotImportError  # noqa: E402

ACTIVE_META = {'price_batch_start': '2026-10-01', 'price_batch_idx': 0}
CONFIGURED_ENV = {
    'AIRCON_BIGGO_COORDINATOR_REPO': 'owner/coordinator-repo',
    'AIRCON_BIGGO_COORDINATOR_TOKEN': 'tok',
}
SNAPSHOT = {'RA-10RF': {'price': '$2,500', 'merchants': 1,
                        'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}}


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
        self.smoke_calls = 0
        self.batch_calls = 0
        self.force_calls = 0
        self.force_kwargs = []
        self.batch_result = {'status': 'completed'}
        self.models = ['M1', 'M2']

    def load_models(self):
        return list(self.models)

    def run_smoke(self):
        self.smoke_calls += 1
        self.REQUEST_STATS['token'] += 1
        self.REQUEST_STATS['search'] += 1
        return self.smoke_result

    def run_price_batch(self, should_abort=None):
        self.batch_calls += 1
        if should_abort is not None:
            for _ in range(200):
                if should_abort():
                    return {'status': 'aborted', 'leaseLost': True}
                time.sleep(0.001)
        self.REQUEST_STATS['search'] += 2
        return self.batch_result

    def run_force_batch(self, limit=None, *, smoke=True, should_abort=None, exit_on_fail=True):
        self.force_calls += 1
        self.force_kwargs.append({'limit': limit, 'smoke': smoke, 'exit_on_fail': exit_on_fail})
        if should_abort is not None:
            for _ in range(200):
                if should_abort():
                    return {'status': 'aborted', 'leaseLost': True}
                time.sleep(0.001)
        self.REQUEST_STATS['search'] += 2
        return True


class FakeClient:
    def __init__(self, *, acquire='winner', budget=None, import_result=None,
                 import_error=None, cooldown_until=0, search_used=0,
                 renew_error_after=None):
        self.acquire_result = acquire
        self.state = {'cooldownUntil': cooldown_until, 'leaseOwner': 'me',
                      'requestAttempts': {'token': 0, 'search': search_used}}
        self.budget_result = budget or {'allowed': True, 'searchUsed': search_used,
                                        'effectiveSearchCap': 21}
        self.import_result = import_result
        self.import_error = import_error
        self.calls = []
        self.commits = []
        self.published = None
        self.renew_calls = 0
        self.renew_error_after = renew_error_after

    def maybe_renew(self):
        self.renew_calls += 1
        self.calls.append(('renew', self.renew_calls))
        if self.renew_error_after is not None and self.renew_calls > self.renew_error_after:
            raise LeaseLostError('simulated lease loss')
        return None

    def acquire(self, cycle_id, stage):
        self.calls.append(('acquire', cycle_id, stage))
        self.acquired_cycle = cycle_id
        if self.acquire_result == 'lost' and self.import_error is not None:
            return {'result': 'lost', 'state': self.state}
        return {'result': self.acquire_result, 'state': self.state}

    def abort_without_calls(self):
        self.calls.append(('abort',))

    def budget(self, queued):
        self.calls.append(('budget', queued))
        return dict(self.budget_result)

    def record_attempt(self, kind, n=1):
        self.calls.append(('attempt', kind, n))

    def record_response(self, status):
        self.calls.append(('response', status))

    def apply_cooldown(self, status, headers=None):
        self.calls.append(('cooldown', status))
        return {'cooldownUntil': 0, 'evidence': {}, 'basis': None}

    def commit(self, **kwargs):
        self.calls.append(('commit', kwargs))
        self.commits.append(kwargs)

    def publish_snapshot(self, snapshot, manifest):
        self.calls.append(('publish', manifest))
        self.published = {'snapshot': snapshot, 'manifest': manifest}
        return {'snapshotHash': 'sha256:' + 'c' * 64, 'snapshotPath': 'coordinator/p'}

    def import_snapshot(self, cycle_id, stage):
        if self.import_error is not None:
            raise self.import_error
        return self.import_result


def _run(monkeypatch, tmp_path, *, meta=ACTIVE_META, env=None, client=None, fetch=None,
         snapshot_reader=None, snapshot_writer=None, now=1_000_000, force=None,
         heartbeat_interval=None):
    fetch = fetch or FakeFetch(tmp_path)
    client = client or FakeClient()
    factory_calls = []

    def factory(config, owner, now_fn):
        factory_calls.append((config, owner))
        return client

    out = []
    writer = snapshot_writer or (lambda s: out.append(s))
    kwargs = {}
    if heartbeat_interval is not None:
        kwargs['heartbeat_interval'] = heartbeat_interval
    code = runner.run_stage(env=env if env is not None else CONFIGURED_ENV,
                            batch_mod=FakeBatch(meta), fetch_mod=fetch,
                            client_factory=factory, now=lambda: now,
                            print_fn=out.append,
                            snapshot_reader=snapshot_reader or (lambda: SNAPSHOT),
                            snapshot_writer=writer, force=force, **kwargs)
    return code, client, fetch, out, factory_calls


def _status(out):
    for line in out:
        if line.startswith('BIGGO_STAGE_STATUS: '):
            return line.split(': ', 1)[1]
    return None


def test_inactive_stage_zero_coordinator_and_zero_biggo(tmp_path):
    client = FakeClient()
    fetch = FakeFetch(tmp_path)
    calls = []

    def factory(config, owner, now_fn):
        calls.append('factory')
        return client

    out = []
    code = runner.run_stage(env=CONFIGURED_ENV, batch_mod=FakeBatch({}),
                            fetch_mod=fetch, client_factory=factory,
                            now=lambda: 1_000_000, print_fn=out.append,
                            snapshot_reader=lambda: SNAPSHOT,
                            snapshot_writer=lambda s: None)
    assert code == 0
    assert _status(out) == 'skip-not-active'
    assert calls == [] and client.calls == []
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_active_without_coordinator_config_preserves_snapshot_no_calls(tmp_path):
    fetch = FakeFetch(tmp_path)
    out = []
    code = runner.run_stage(env={}, batch_mod=FakeBatch(ACTIVE_META), fetch_mod=fetch,
                            client_factory=None, now=lambda: 1_000_000, print_fn=out.append,
                            snapshot_reader=lambda: SNAPSHOT,
                            snapshot_writer=lambda s: (_ for _ in ()).throw(
                                AssertionError('唔應該寫 snapshot')))
    assert code == 0
    assert _status(out) == 'skip-coordinator-not-configured'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_winner_happy_path_publishes_validated_snapshot(tmp_path):
    client = FakeClient(acquire='winner')
    code, client, fetch, out, _ = _run(None, tmp_path, client=client)
    assert code == 0 and _status(out) == 'completed'
    assert fetch.smoke_calls == 1 and fetch.batch_calls == 1
    assert client.published is not None
    manifest = client.published['manifest']
    assert manifest['cycleId'] == '2026-10-01:1/7' and manifest['stage'] == 1
    assert client.commits[-1]['status'] == 'completed'
    assert client.commits[-1]['snapshot_hash'] == 'sha256:' + 'c' * 64


def test_loser_imports_only_matching_snapshot(tmp_path):
    client = FakeClient(acquire='lost',
                        import_result={'snapshot': SNAPSHOT, 'snapshotHash': 'sha256:' + 'd' * 64})
    out = []
    imported = []
    code = runner.run_stage(env=CONFIGURED_ENV, batch_mod=FakeBatch(ACTIVE_META),
                            fetch_mod=FakeFetch(tmp_path), client_factory=lambda c, o, n: client,
                            now=lambda: 1_000_000, print_fn=out.append,
                            snapshot_reader=lambda: SNAPSHOT,
                            snapshot_writer=imported.append)
    assert code == 0 and _status(out) == 'skip-lost-imported'
    assert imported == [SNAPSHOT]
    assert client.published is None


def test_loser_without_snapshot_makes_no_biggo_calls(tmp_path):
    client = FakeClient(acquire='lost', import_error=SnapshotImportError('冇'))
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-lost-no-snapshot'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0


def test_completed_idempotent_skips_without_calls(tmp_path):
    client = FakeClient(acquire='completed-idempotent')
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'completed-idempotent'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0


def test_needs_review_prohibits_rerun_without_calls(tmp_path):
    client = FakeClient(acquire='needs-review')
    client.state['lastReviewReason'] = 'publish-uncertain'
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert not any(call[0] == 'publish' for call in client.calls)


def test_cooldown_skip_preserves_snapshot_without_calls(tmp_path):
    client = FakeClient(acquire='winner', cooldown_until=2_000_000)
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-cooldown'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert ('abort',) in client.calls


def test_budget_skip_preserves_snapshot_without_calls(tmp_path):
    client = FakeClient(acquire='winner',
                        budget={'allowed': False, 'searchUsed': 21, 'effectiveSearchCap': 21})
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-budget'
    assert fetch.smoke_calls == 0 and fetch.batch_calls == 0
    assert ('abort',) in client.calls


def test_smoke_failure_after_calls_marks_needs_review(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    fetch.smoke_result = False
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.batch_calls == 0
    assert client.commits[-1]['status'] == 'needs_review'
    assert client.commits[-1]['last_review_reason'] == 'smoke-failed'


def test_smoke_failure_without_calls_aborts_safely(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    fetch.smoke_result = False
    fetch.run_smoke = lambda: False  # 零計數（例如測試模式 guard）
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-smoke-no-calls'
    assert ('abort',) in client.calls
    assert not client.commits


def test_batch_partial_marks_needs_review_and_does_not_publish(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    fetch.batch_result = {'status': 'partial-net-errors'}
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert client.published is None
    assert client.commits[-1]['status'] == 'needs_review'
    assert client.commits[-1]['last_review_reason'] == 'batch-partial-net-errors'


def test_batch_no_calls_aborts_safely(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    fetch.run_smoke = lambda: True  # 成功但零計數（防禦性路徑：無實際呼叫）
    fetch.run_price_batch = lambda should_abort=None: {'status': 'cooldown-skip'}
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'skip-batch-no-calls'
    assert ('abort',) in client.calls


def test_publish_uncertainty_marks_needs_review(tmp_path):
    client = FakeClient(acquire='winner')

    def boom(snapshot, manifest):
        raise RuntimeError('network down after calls')

    client.publish_snapshot = boom
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0 and _status(out) == 'needs-review'
    assert client.commits[-1]['status'] == 'needs_review'
    assert client.commits[-1]['last_review_reason'] == 'publish-uncertain'


def test_meta_error_blocks_with_rc2(tmp_path):
    class BadMeta(FakeBatch):
        def load_meta(self):
            raise batch_utils.MetaError('corrupt')

    out = []
    code = runner.run_stage(env=CONFIGURED_ENV, batch_mod=BadMeta({}),
                            fetch_mod=FakeFetch(tmp_path), client_factory=None,
                            now=lambda: 1_000_000, print_fn=out.append,
                            snapshot_reader=lambda: SNAPSHOT,
                            snapshot_writer=lambda s: None)
    assert code == 2 and _status(out) == 'blocked-meta-invalid'


def test_safe_evidence_recorded_into_coordinator_state(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    fetch.batch_result = {'status': 'partial-net-errors'}

    def evidence_batch(should_abort=None):
        fetch.REQUEST_STATS['search'] += 1
        fetch.LAST_RATE_LIMIT_EVIDENCE.update({'status': 429, 'retryAfter': '60'})
        return {'status': 'partial-net-errors'}

    fetch.run_price_batch = evidence_batch
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0
    assert ('response', 429) in client.calls
    assert ('cooldown', 429) in client.calls
    assert fetch.REQUEST_STATS['search'] > 0


# ---------------------------------------------------------------- heartbeat（repair #2）


def test_heartbeat_renews_repeatedly_and_stops_without_orphan(tmp_path):
    """長網絡工作期間必須重複 renew（5 分鐘節流由 client 負責；heartbeat 持續 tick），
    run_stage 返回後唔可以有 orphan heartbeat。"""
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch,
                                       heartbeat_interval=0.005)
    assert code == 0 and _status(out) == 'completed'
    assert client.renew_calls >= 2, 'batch 期間 heartbeat 必須真正重複呼叫 maybe_renew'
    assert fetch.batch_calls == 1 and client.published is not None
    frozen = client.renew_calls
    time.sleep(0.05)
    assert client.renew_calls == frozen, 'run_stage 返回後 heartbeat 必須已停止，冇 orphan'


def test_lease_loss_during_batch_fails_closed_and_no_publish(tmp_path):
    """batch 中途 lease 轉手：should_abort 令 batch 中止、唔發布、標 needs_review。"""
    client = FakeClient(acquire='winner', renew_error_after=1)
    fetch = FakeFetch(tmp_path)
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch,
                                       heartbeat_interval=0.005)
    assert code == 0
    assert _status(out) == 'needs-review'
    assert fetch.batch_calls == 1
    assert client.published is None, 'lease 失效後唔可以發布 snapshot'
    assert any(c[0] == 'commit' and c[1].get('status') == 'needs_review'
               for c in client.calls)
    frozen = client.renew_calls
    time.sleep(0.05)
    assert client.renew_calls == frozen, 'lease lost 後 heartbeat 必須停止'


def test_lease_loss_during_smoke_stops_before_batch(tmp_path):
    client = FakeClient(acquire='winner', renew_error_after=0)
    fetch = FakeFetch(tmp_path)

    def slow_smoke():
        fetch.REQUEST_STATS['search'] += 1
        time.sleep(0.05)
        return True

    fetch.run_smoke = slow_smoke
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch,
                                       heartbeat_interval=0.005)
    assert code == 0 and _status(out) == 'needs-review'
    assert fetch.batch_calls == 0, 'smoke 時失去 lease 之後唔可以再跑 batch'
    assert client.published is None


def test_heartbeat_stop_is_guaranteed_even_on_snapshot_reader_exception(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)

    def boom():
        raise RuntimeError('snapshot read failed')

    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch,
                                       snapshot_reader=boom, heartbeat_interval=0.005)
    assert code == 0 and _status(out) == 'needs-review'
    frozen = client.renew_calls
    time.sleep(0.05)
    assert client.renew_calls == frozen, '異常路徑亦要 finally 停 heartbeat'


# ---------------------------------------------------------------- coordinated force（repair #1）


def test_force_mode_acquires_lease_before_force_batch_and_publishes(tmp_path):
    client = FakeClient(acquire='winner')
    fetch = FakeFetch(tmp_path)
    out = []
    code = runner.run_stage(
        env={**CONFIGURED_ENV, 'AIRCON_BIGGO_FORCE_STAGE': 'true'},
        batch_mod=FakeBatch({}), fetch_mod=fetch,
        client_factory=lambda config, owner, now_fn: client,
        now=lambda: 1_000_000, print_fn=out.append,
        snapshot_reader=lambda: SNAPSHOT, snapshot_writer=lambda s: None)
    assert code == 0 and _status(out) == 'completed'
    acquire_idx = next(i for i, c in enumerate(client.calls) if c[0] == 'acquire')
    publish_idx = next(i for i, c in enumerate(client.calls) if c[0] == 'publish')
    assert acquire_idx < publish_idx
    assert fetch.force_calls == 1 and fetch.batch_calls == 0
    assert fetch.force_kwargs[0]['smoke'] is False, 'runner 已經做 smoke，唔可以重複'
    assert fetch.force_kwargs[0]['exit_on_fail'] is False
    assert client.published['manifest']['mode'] == 'force'
    assert client.acquired_cycle.startswith('force-'), 'force 用獨立 cycleId 但仍受 lease 約束'


def test_force_mode_without_coordinator_config_makes_zero_calls(tmp_path):
    fetch = FakeFetch(tmp_path)
    out = []
    code = runner.run_stage(
        env={'AIRCON_BIGGO_FORCE_STAGE': '1'}, batch_mod=FakeBatch({}),
        fetch_mod=fetch, client_factory=None, now=lambda: 1_000_000,
        print_fn=out.append, snapshot_reader=lambda: SNAPSHOT,
        snapshot_writer=lambda s: None)
    assert code == 0 and _status(out) == 'skip-coordinator-not-configured'
    assert fetch.smoke_calls == 0 and fetch.force_calls == 0
    assert fetch.REQUEST_STATS == {'token': 0, 'search': 0}


def test_force_mode_losing_lease_never_runs_force_batch(tmp_path):
    client = FakeClient(acquire='lost', import_error=SnapshotImportError('no snapshot'))
    fetch = FakeFetch(tmp_path)
    out = []
    code = runner.run_stage(
        env={**CONFIGURED_ENV, 'AIRCON_BIGGO_FORCE_STAGE': 'true'},
        batch_mod=FakeBatch({}), fetch_mod=fetch,
        client_factory=lambda config, owner, now_fn: client,
        now=lambda: 1_000_000, print_fn=out.append,
        snapshot_reader=lambda: SNAPSHOT, snapshot_writer=lambda s: None)
    assert code == 0 and _status(out) == 'skip-lost-no-snapshot'
    assert fetch.smoke_calls == 0 and fetch.force_calls == 0


def test_runner_source_has_no_coordinator_bypass_and_heartbeat_in_finally():
    src = open(os.path.join(BASE, 'scripts', 'biggo_stage_runner.py'),
               encoding='utf-8').read()
    assert 'LeaseHeartbeat' in src and 'maybe_renew()' in src
    assert 'heartbeat.stop()' in src
    assert 'finally:' in src
    assert 'run_force_batch(' in src and 'smoke=False' in src
    assert 'should_abort=lambda: heartbeat.lost' in src or \
           'should_abort=lambda: heartbeat.lost' in src.replace(' ', '')
    assert src.index('client.acquire(cycle_id') < src.index('run_force_batch('), \
        'force path 一樣要先去 acquire'
