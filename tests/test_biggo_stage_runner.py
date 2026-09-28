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

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import batch_utils  # noqa: E402
import biggo_stage_runner as runner  # noqa: E402
from biggo_coordinator import SnapshotImportError  # noqa: E402

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
        self.batch_result = {'status': 'completed'}
        self.models = ['M1', 'M2']

    def load_models(self):
        return list(self.models)

    def run_smoke(self):
        self.smoke_calls += 1
        self.REQUEST_STATS['token'] += 1
        self.REQUEST_STATS['search'] += 1
        return self.smoke_result

    def run_price_batch(self):
        self.batch_calls += 1
        self.REQUEST_STATS['search'] += 2
        return self.batch_result


class FakeClient:
    def __init__(self, *, acquire='winner', budget=None, import_result=None,
                 import_error=None, cooldown_until=0, search_used=0):
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

    def acquire(self, cycle_id, stage):
        self.calls.append(('acquire', cycle_id, stage))
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
         snapshot_reader=None, snapshot_writer=None, now=1_000_000):
    fetch = fetch or FakeFetch(tmp_path)
    client = client or FakeClient()
    factory_calls = []
    if client is not None:
        def factory(config, owner, now_fn):
            factory_calls.append((config, owner))
            return client
    else:
        factory = None
    out = []
    writer = snapshot_writer or (lambda s: out.append(s))
    code = runner.run_stage(env=env if env is not None else CONFIGURED_ENV,
                            batch_mod=FakeBatch(meta), fetch_mod=fetch,
                            client_factory=factory, now=lambda: now,
                            print_fn=out.append,
                            snapshot_reader=snapshot_reader or (lambda: SNAPSHOT),
                            snapshot_writer=writer)
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
    fetch.run_price_batch = lambda: {'status': 'cooldown-skip'}
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

    def evidence_batch():
        fetch.REQUEST_STATS['search'] += 1
        fetch.LAST_RATE_LIMIT_EVIDENCE.update({'status': 429, 'retryAfter': '60'})
        return {'status': 'partial-net-errors'}

    fetch.run_price_batch = evidence_batch
    code, client, fetch, out, _ = _run(None, tmp_path, client=client, fetch=fetch)
    assert code == 0
    assert ('response', 429) in client.calls
    assert ('cooldown', 429) in client.calls
    assert fetch.REQUEST_STATS['search'] > 0
