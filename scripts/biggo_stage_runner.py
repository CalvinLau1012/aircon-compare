#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo 價錢批次階段閘門（approved design C 嘅 orchestration 層）。

順序（任何情況都唔可以調亂）：
  1. 先讀本地 `prices_meta.json` 批次階段；inactive／已完成 → 即回，零 BigGo
     token／search，亦零 coordinator 讀寫；
  2. active 但冇 coordinator 配置 → 保留現有價錢快照，回清晰非秘密 status，
     **唔會**呼叫 BigGo（避免兩個 writer 重複呼叫）；
  3. active 或 coordinated force intent → CAS acquire lease：
       - completed cycle+stage → idempotent skip；
       - needs_review → 禁止自動重跑；
       - lost → 只可以按 cycle／stage／schema／hash 匯入 winner snapshot；
       - winner → 檢查 cooldown／budget（per-stage cap = 1 smoke + 2×queued models），
         先行一次 bounded smoke，再跑本地價格批次；
  4. winner 全部成功 → schema-validated snapshot + manifest + SHA-256 發佈到
     coordinator repo，狀態 completed；
  5. 任何已發出呼叫但 snapshot 發布唔確定 → needs_review，唔會自動重跑；
  6. force intent（`AIRCON_BIGGO_FORCE_STAGE=1`／`--force`）只可以改變要處理嘅
     型號集合，**唔可以**繞過 coordinator：同樣要 acquire lease、cooldown、budget、
     idempotency、needs_review 同 publication 檢查。

Lease heartbeat（repair #2）：
  - 45 分鐘 lease；背景 heartbeat 定期呼叫 `client.maybe_renew()`（client 內部 5 分鐘
    節流），覆蓋 smoke、batch、publication 全程；
  - lease 失效即 fail-closed：停止提交新工作（`should_abort` callback）、取消未開始
    futures、唔會發布 snapshot；
  - `finally` 保證停止同 join heartbeat，唔會留低 orphan thread；
  - 所有時鐘經 `now_fn` 注入，測試可完全 deterministic。

依賴（HTTP／fetch／時鐘）全部可注入，測試完全離線。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BASE, SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import batch_utils as _batch_utils  # noqa: E402
import fetch_biggo as _fetch_biggo  # noqa: E402
import biggo_coordinator as coord_mod  # noqa: E402

PRICE_BATCH_DAYS = _batch_utils.PRICE_BATCH_DAYS
DEFAULT_HEARTBEAT_INTERVAL = 30.0  # 秒；client.maybe_renew() 內部再按 5 分鐘節流


class LeaseHeartbeat:
    """Coordinator lease 背景心跳（可 deterministic 注入 client／時鐘）。

    - `start()` 開 daemon thread；每 `interval` 秒 tick 一次，呼叫
      `client.maybe_renew()`（實際 CAS 由 client 按 5 分鐘節流）。
    - `lost` 一旦 True 即永久 fail-closed；runner 會停止提交新工作同發布。
    - `stop()` 設 stop event + join；join timeout 仍生存即當 lease lost，
      確保唔會喺 heartbeat 仍跑嘅情況下繼續。
    """

    def __init__(self, client, *, interval=DEFAULT_HEARTBEAT_INTERVAL, now_fn=None):
        self._client = client
        self._interval = max(0.005, float(interval))
        self._now_fn = now_fn or time.time
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._lost_error = None
        self._thread = None
        self.ticks = 0

    def _run(self):
        while not self._stop.wait(self._interval):
            if not self.tick():
                break

    def tick(self):
        """單次心跳；回 False 代表已失去 lease（心跳停止）。"""
        try:
            self._client.maybe_renew()
        except coord_mod.LeaseLostError as e:
            with self._lock:
                self._lost_error = e
            return False
        except Exception as e:  # noqa: BLE001 - heartbeat 任何錯誤都 fail-closed
            with self._lock:
                self._lost_error = coord_mod.LeaseLostError(
                    f'heartbeat error: {type(e).__name__}')
            return False
        with self._lock:
            self.ticks += 1
        return True

    @property
    def lost(self):
        with self._lock:
            return self._lost_error is not None

    @property
    def lost_reason(self):
        with self._lock:
            return type(self._lost_error).__name__ if self._lost_error else None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name='biggo-lease-heartbeat',
                                            daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                # 唔可以喺 heartbeat 仍跑嘅情況下發布；當 lease 已失效。
                with self._lock:
                    if self._lost_error is None:
                        self._lost_error = coord_mod.LeaseLostError(
                            'heartbeat stop timeout（當 lease 已失效，fail-closed）')
        return self


def _emit(print_fn, status, **detail):
    print_fn(f'BIGGO_STAGE_STATUS: {status}')
    for key in sorted(detail):
        print_fn(f'  - {key}: {detail[key]}')


def _stat(value):
    data = value if isinstance(value, dict) else {}
    return {'token': int(data.get('token', 0) or 0),
            'search': int(data.get('search', 0) or 0)}


def _delta(after, before):
    return {'token': max(0, after.get('token', 0) - before.get('token', 0)),
            'search': max(0, after.get('search', 0) - before.get('search', 0))}


def _default_snapshot_reader(fetch_mod):
    def read():
        with open(fetch_mod.OUT_PATH, encoding='utf-8') as f:
            return json.load(f)
    return read


def _default_snapshot_writer(fetch_mod):
    def write(snapshot):
        tmp = fetch_mod.OUT_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(snapshot, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, fetch_mod.OUT_PATH)
    return write


def _safe_evidence(fetch_mod):
    evidence = getattr(fetch_mod, 'LAST_RATE_LIMIT_EVIDENCE', None) or {}
    status = evidence.get('status')
    if isinstance(status, int):
        return {'status': status, 'retryAfter': evidence.get('retryAfter')}
    return None


def _force_requested(env, force):
    if force is not None:
        return bool(force)
    value = str(env.get('AIRCON_BIGGO_FORCE_STAGE', '')).strip().lower()
    return value in ('1', 'true', 'yes', 'on')


def _force_cycle_id(now_fn):
    stamp = datetime.datetime.fromtimestamp(int(now_fn()), datetime.timezone.utc)
    return 'force-' + stamp.strftime('%Y-%m-%d')


def _record_evidence(client, fetch_mod, delta):
    client.record_attempt('token', delta['token'])
    client.record_attempt('search', delta['search'])
    evidence = _safe_evidence(fetch_mod)
    if evidence:
        client.record_response(evidence['status'])
        client.apply_cooldown(evidence['status'], {'Retry-After': evidence.get('retryAfter')})


def _needs_review(client, print_fn, cycle_id, *, stage, reason, lease_lost=False,
                  calls='made', extra=None):
    """標記 needs_review；lease 已轉手時唔可以寫 state，只如實報告。"""
    detail = {'cycle': cycle_id, 'stage': stage, 'rerun': 'prohibited', 'calls': calls}
    if extra:
        detail.update(extra)
    if lease_lost:
        detail['lease'] = 'lost'
    try:
        client.commit(status='needs_review', last_review_reason=reason)
        _emit(print_fn, 'needs-review', **detail)
        return {'status': 'needs-review', 'leaseLost': bool(lease_lost)}
    except coord_mod.LeaseLostError:
        detail['lease'] = 'lost'
        detail['commit'] = 'blocked-lease-lost'
        _emit(print_fn, 'needs-review', **detail)
        return {'status': 'needs-review', 'leaseLost': True}


def run_stage(*, env=None, batch_mod=None, fetch_mod=None, client_factory=None,
              now=None, print_fn=print, snapshot_reader=None, snapshot_writer=None,
              force=None, heartbeat_interval=DEFAULT_HEARTBEAT_INTERVAL):
    """回傳 exit code：0 = 安全 skip／成功；1 = 未預期失敗；2 = 契約／配置錯誤阻斷。"""
    env = env if env is not None else os.environ
    batch_mod = batch_mod or _batch_utils
    fetch_mod = fetch_mod or _fetch_biggo
    now_fn = now or time.time
    snapshot_reader = snapshot_reader or _default_snapshot_reader(fetch_mod)
    snapshot_writer = snapshot_writer or _default_snapshot_writer(fetch_mod)
    force_mode = _force_requested(env, force)

    # ---- 1. 本地階段先決；inactive（且非 force）唔准碰 coordinator／BigGo ----
    try:
        meta = batch_mod.load_meta()
    except batch_mod.MetaError as e:
        _emit(print_fn, 'blocked-meta-invalid', error=str(e)[:200])
        return 2

    if force_mode:
        # Force intent 只改變要處理嘅型號集合；一樣要 coordinator lease／budget。
        cycle_id = _force_cycle_id(now_fn)
        stage_no = 1
        queued = len(fetch_mod.load_models())
    else:
        idx = meta.get('price_batch_idx', 0)
        if not meta.get('price_batch_start') or idx >= PRICE_BATCH_DAYS:
            _emit(print_fn, 'skip-not-active')
            return 0
        models = fetch_mod.load_models()
        batch = batch_mod.get_batch_todo(models, meta)
        if not batch:
            _emit(print_fn, 'skip-not-active')
            return 0
        todo, stage_idx, _total = batch
        cycle_id = f"{meta['price_batch_start']}:{stage_idx + 1}/{PRICE_BATCH_DAYS}"
        stage_no = stage_idx + 1
        queued = len(todo)

    # ---- 2. Coordinator 配置缺失：保留快照，唔呼叫 BigGo ----
    cfg_status = coord_mod.config_status(env)
    if not cfg_status['configured']:
        _emit(print_fn, 'skip-coordinator-not-configured',
              reason=cfg_status['reason'], cycle=cycle_id, queuedModels=queued,
              snapshot='preserved')
        # 配置缺失＝安全跳過（保留快照），唔會阻斷 EMSD daily；但唔會呼叫 BigGo。
        return 0
    try:
        config = coord_mod.CoordinatorConfig.from_env(env)
    except coord_mod.CoordinatorConfigError as e:
        _emit(print_fn, 'skip-coordinator-config-invalid', error=type(e).__name__,
              snapshot='preserved')
        return 2

    owner = (env.get('AIRCON_BIGGO_LEASE_OWNER')
             or f"{env.get('GITHUB_RUN_ID', 'local')}-{os.getpid()}")
    if client_factory is None:
        contents = coord_mod.GitHubContentsClient(config)
        client = coord_mod.CoordinatorClient(
            contents, owner=owner, now=now_fn,
            provider_quota_limit=config.provider_quota_limit,
            provider_window_end=config.provider_window_end)
    else:
        client = client_factory(config, owner, now_fn)

    # ---- 3. CAS lease ----
    try:
        acquired = client.acquire(cycle_id, stage_no)
    except coord_mod.CoordinatorError as e:
        _emit(print_fn, 'blocked-coordinator-error', error=type(e).__name__)
        return 2
    result = acquired['result']
    if result == 'completed-idempotent':
        _emit(print_fn, 'completed-idempotent', cycle=cycle_id, force='yes' if force_mode else 'no')
        return 0
    if result == 'needs-review':
        _emit(print_fn, 'needs-review', cycle=cycle_id, rerun='prohibited',
              reason=(acquired['state'].get('lastReviewReason') or 'prior-review'))
        return 0
    if result == 'lost':
        try:
            imported = client.import_snapshot(cycle_id, stage_no)
            snapshot_writer(imported['snapshot'])
            _emit(print_fn, 'skip-lost-imported', cycle=cycle_id,
                  snapshotHash=imported['snapshotHash'])
        except coord_mod.SnapshotImportError as e:
            _emit(print_fn, 'skip-lost-no-snapshot', cycle=cycle_id,
                  reason=type(e).__name__)
        return 0

    # ---- 4. winner：cooldown／budget ----
    state = client.state
    now_int = int(now_fn())
    if state.get('cooldownUntil', 0) > now_int:
        client.abort_without_calls()
        _emit(print_fn, 'skip-cooldown', cycle=cycle_id,
              cooldownUntil=state['cooldownUntil'])
        return 0
    budget = client.budget(queued)
    if not budget['allowed']:
        client.abort_without_calls()
        _emit(print_fn, 'skip-budget', cycle=cycle_id, searchUsed=budget['searchUsed'],
              effectiveSearchCap=budget['effectiveSearchCap'])
        return 0

    # ---- 5. winner：lease heartbeat + 全程網絡工作（finally 保證停止） ----
    heartbeat = LeaseHeartbeat(client, interval=heartbeat_interval, now_fn=now_fn)
    heartbeat.start()
    try:
        smoke_delta = {'token': 0, 'search': 0}
        batch_delta = {'token': 0, 'search': 0}

        # 5a. bounded smoke（最多 1 個 product-search request；8 秒 timeout）
        before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
        smoke_ok = False
        try:
            smoke_ok = bool(fetch_mod.run_smoke())
        except Exception as e:  # noqa: BLE001 - smoke 例外一律視為失敗
            print_fn(f'  ⚠️ smoke 例外：{type(e).__name__}')
        after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
        smoke_delta = _delta(after, before)
        _record_evidence(client, fetch_mod, smoke_delta)

        if heartbeat.lost:
            calls = 'made' if smoke_delta['token'] + smoke_delta['search'] else 'none'
            if calls == 'made':
                return 0 if _needs_review(
                    client, print_fn, cycle_id, stage='smoke-heartbeat',
                    reason='lease-lost-during-smoke', lease_lost=True) else 0
            _emit(print_fn, 'skip-smoke-no-calls', cycle=cycle_id, lease='lost')
            return 0

        if not smoke_ok:
            calls_made = smoke_delta['token'] + smoke_delta['search'] > 0
            if calls_made:
                _needs_review(client, print_fn, cycle_id, stage='smoke',
                              reason='smoke-failed')
            else:
                client.abort_without_calls()
                _emit(print_fn, 'skip-smoke-no-calls', cycle=cycle_id)
            return 0

        # 5b. 本地價格批次（coordinated force 只換型號集合，唔會繞過 lease）
        before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
        try:
            if force_mode:
                batch_result = fetch_mod.run_force_batch(
                    None, smoke=False, should_abort=lambda: heartbeat.lost,
                    exit_on_fail=False)
                if batch_result is True:
                    batch_status = 'completed'
                elif isinstance(batch_result, dict):
                    batch_status = batch_result.get('status', 'unknown')
                else:
                    batch_status = 'unknown'
            else:
                batch_result = fetch_mod.run_price_batch(
                    should_abort=lambda: heartbeat.lost)
                batch_status = (batch_result.get('status')
                                if isinstance(batch_result, dict) else 'unknown')
        except Exception as e:  # noqa: BLE001
            after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
            batch_delta = _delta(after, before)
            _record_evidence(client, fetch_mod, batch_delta)
            if batch_delta['token'] + batch_delta['search'] > 0:
                _needs_review(client, print_fn, cycle_id, stage='batch-exception',
                              reason='batch-exception',
                              lease_lost=heartbeat.lost)
            else:
                client.abort_without_calls()
                _emit(print_fn, 'skip-batch-no-calls', cycle=cycle_id,
                      error=type(e).__name__)
            return 0
        after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
        batch_delta = _delta(after, before)
        _record_evidence(client, fetch_mod, batch_delta)

        if heartbeat.lost or batch_status == 'aborted':
            return 0 if _needs_review(
                client, print_fn, cycle_id, stage='batch-heartbeat',
                reason='lease-lost-during-batch' if heartbeat.lost else 'batch-aborted',
                lease_lost=heartbeat.lost, extra={'batchStatus': batch_status}) else 0

        # 5c. 只喺成功、lease 仍然有效、且 heartbeat 未失效時發布
        if batch_status == 'completed':
            if heartbeat.lost:
                return 0 if _needs_review(
                    client, print_fn, cycle_id, stage='pre-publish',
                    reason='lease-lost-before-publish', lease_lost=True) else 0
            try:
                snapshot = snapshot_reader()
                coord_mod.validate_price_snapshot(snapshot)
                manifest = {
                    'schemaVersion': coord_mod.MANIFEST_SCHEMA_VERSION,
                    'cycleId': cycle_id,
                    'stage': stage_no,
                    'mode': 'force' if force_mode else 'price-batch',
                    'writer': owner,
                    'writerKind': 'github-actions' if env.get('GITHUB_ACTIONS') == 'true'
                                  else 'self-host',
                    'commit': env.get('GITHUB_SHA') or env.get('AIRCON_COMMIT') or 'unknown',
                    'publishedAt': coord_mod.utc_stamp(now_fn()),
                    'recordCount': len(snapshot),
                }
                if heartbeat.lost:
                    raise coord_mod.LeaseLostError('lease lost before publish write')
                published = client.publish_snapshot(snapshot, manifest)
                if heartbeat.lost:
                    raise coord_mod.LeaseLostError('lease lost during publish')
                client.commit(status='completed', snapshot_hash=published['snapshotHash'],
                              snapshot_path=published['snapshotPath'])
                _emit(print_fn, 'completed', cycle=cycle_id,
                      snapshotHash=published['snapshotHash'], records=len(snapshot))
                return 0
            except coord_mod.LeaseLostError:
                return 0 if _needs_review(
                    client, print_fn, cycle_id, stage='publish',
                    reason='lease-lost-during-publish', lease_lost=True) else 0
            except Exception as e:  # noqa: BLE001 - 發佈失敗 = publication uncertain
                _needs_review(client, print_fn, cycle_id, stage='publish',
                              reason='publish-uncertain', lease_lost=heartbeat.lost,
                              extra={'error': type(e).__name__})
                return 0

        # 5d. 非 completed：有呼叫 → needs_review；零呼叫 → 安全收手
        calls_made = (smoke_delta['token'] + smoke_delta['search']
                      + batch_delta['token'] + batch_delta['search']) > 0
        if calls_made:
            _needs_review(client, print_fn, cycle_id, stage='batch',
                          reason=f'batch-{batch_status}',
                          lease_lost=heartbeat.lost,
                          extra={'batchStatus': batch_status})
        else:
            client.abort_without_calls()
            _emit(print_fn, 'skip-batch-no-calls', cycle=cycle_id, batchStatus=batch_status)
        return 0
    finally:
        # 保證冇 orphan heartbeat：設定 stop event + join（timeout 後當 lease lost）。
        heartbeat.stop()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    argv = list(sys.argv[1:] if argv is None else argv)
    force = '--force' in argv
    unknown = [a for a in argv if a not in ('--force',)]
    if unknown:
        print(f'❌ 唔支援嘅參數：{unknown}（只支援 --force；force intent 亦可以經 '
              f'AIRCON_BIGGO_FORCE_STAGE=1）', file=sys.stderr)
        return 2
    return run_stage(force=True if force else None)


if __name__ == '__main__':
    sys.exit(main())
