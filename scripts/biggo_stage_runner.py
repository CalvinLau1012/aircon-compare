#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo 價錢批次階段閘門（approved design C ＋ P0 stage bundle 交易）。

順序（任何情況都唔可以調亂）：
  1. 先讀本地 `prices_meta.json` 批次階段；inactive／已完成 → 即回，零 BigGo
     token／search，亦零 coordinator 讀寫；
  2. active 但冇 coordinator 配置 → 保留現有價錢快照，回清晰非秘密 status，
     **唔會**呼叫 BigGo；
  3. CAS acquire：winner／winner-intent（expired 帶 intent）／completed-idempotent／
     needs-review／lost／completed-ahead／blocked-needs-review／blocked-incomplete；
  4. providerQuotaLimit／providerWindowEnd 有配置（env）或 legacy state 帶呢啲事實：
     因冇共享 window usage ledger，直接 fail-closed（`blocked-quota-window-unsupported`）
     ── 零 BigGo、零 coordinator mutation、獨立 alert workflow 紅；唔會用 stage-local 80%
     冒充 provider window；
  5. winner：cooldown／budget → **write-ahead intent**（CAS 持久化
     `callsMayHaveStarted`，失敗即禁止請求）→ bounded smoke → batch（staged，
     零本地寫入）→ publish v2 bundle（base/new snapshot＋stage-result＋manifest）→
     CAS `completed` → 最後才 local apply；任何 publish 後失敗 → needs_review／
     publish-uncertain，唔 apply；
  6. winner-intent（過期 lease 但可能有呼叫）：只可 adopt 完整可驗證 bundle
     （零 BigGo）再 CAS completed；冇完整 bundle → 只可 needs_review；
  7. completed-idempotent：import v2 bundle＋apply；legacy snapshot-only bundle
     拒絕自動 apply；
  8. lost（active 他人）：零本地寫入、零 BigGo、零 apply；
  9. blocked-incomplete（remote active／idle 未完成，或 idle 帶 intent）：正常／force
     一律唔可以跨 stage；0 PUT、0 本地寫、0 BigGo＋紅 alert；
  10. completed-ahead／needs_review：0 PUT、0 本地寫、0 BigGo。

Status artifact（`--status-out`）供 CI guard／alert 用；只含狀態、cycle/stage、hash
同錯誤類型，唔含秘密。
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
import biggo_apply as _apply_mod  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402
from biggo_limiter import RequestLimiter  # noqa: E402

PRICE_BATCH_DAYS = _batch_utils.PRICE_BATCH_DAYS
DEFAULT_HEARTBEAT_INTERVAL = 30.0  # 秒；client.maybe_renew() 內部再按 5 分鐘節流

# 需要人手覆核／紅 alert 的終態。
ALERT_STATUSES = frozenset({
    'needs-review', 'blocked-intent-persist', 'completed-apply-failed',
    'completed-legacy-manual', 'completed-bundle-unavailable',
    'blocked-incomplete', 'blocked-quota-window-unsupported', 'blocked-stage-gap',
    'blocked-force-cycle-incomplete', 'blocked-stale',
})


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


def _needs_review(client, emit, cycle_id, *, stage, reason, lease_lost=False,
                  calls='made', extra=None):
    """標記 needs_review；lease 已轉手時唔可以寫 state，只如實報告。

    `emit` 係 runner 的 emit closure（print＋status artifact record）；確保 status／
    reason／requests 唔會跌返通用 error。
    """
    detail = {'cycle': cycle_id, 'stage': stage, 'rerun': 'prohibited', 'calls': calls}
    if extra:
        detail.update(extra)
    if lease_lost:
        detail['lease'] = 'lost'
    try:
        client.commit(status='needs_review', last_review_reason=reason)
        emit('needs-review', reason=reason, **detail)
        return {'status': 'needs-review', 'leaseLost': bool(lease_lost), 'reason': reason}
    except coord_mod.LeaseLostError:
        detail['lease'] = 'lost'
        detail['commit'] = 'blocked-lease-lost'
        emit('needs-review', reason=reason, **detail)
        return {'status': 'needs-review', 'leaseLost': True, 'reason': reason}
    except coord_mod.CoordinatorError as e:
        detail['commit'] = f'failed-{type(e).__name__}'
        emit('needs-review', reason=reason, **detail)
        return {'status': 'needs-review', 'leaseLost': False, 'reason': reason,
                'commitFailed': type(e).__name__}


def _write_status(status_out, record):
    """寫 status artifact（best-effort；缺失由 CI guard／alert fail-closed）。"""
    if not status_out:
        return
    payload = dict(record)
    if not payload.get('status'):
        payload['status'] = 'error'
        payload['alert'] = True
        payload['rerun'] = 'prohibited'
    try:
        tmp = status_out + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, status_out)
    except OSError:
        pass


def _write_review_evidence(status_out, staged):
    """needs_review 時保留 staged snapshot（公開價格資料、無 secrets）。"""
    if not status_out or not isinstance(staged, dict):
        return
    try:
        base_dir = os.path.dirname(os.path.abspath(status_out))
        out_dir = os.path.join(base_dir, 'biggo-review')
        os.makedirs(out_dir, exist_ok=True)
        payload = {
            'status': staged.get('status'),
            'snapshot': staged.get('snapshot'),
            'outcomes': staged.get('outcomes'),
            'counters': staged.get('counters'),
        }
        tmp = os.path.join(out_dir, 'stage-snapshot.json.tmp')
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, os.path.join(out_dir, 'stage-snapshot.json'))
    except OSError:
        pass


def _build_stage_result(*, cycle_id, stage_no, mode, client, now_fn, staged):
    stage_result = {
        'schemaVersion': coord_mod.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': cycle_id,
        'stage': stage_no,
        'mode': mode,
        'generatedAt': coord_mod.utc_stamp(now_fn()),
        'counters': dict(staged['counters']),
        'requestAttempts': dict(client.state.get('requestAttempts', {'token': 0, 'search': 0})),
        'responseStatusCounts': dict(client.state.get('responseStatusCounts', {})),
        'outcomes': list(staged['outcomes']),
        'blacklistReview': dict(staged.get('blacklistReview') or {'quotaIndex': 0, 'reviewed': []}),
        'effects': dict(staged['effects']),
        'preState': dict(staged['preState']),
        'postState': dict(staged['postState']),
    }
    if mode == 'force':
        stage_result['metaFields'] = dict(staged.get('metaFields') or {})
    stage_result['effectsHash'] = sha256_id(canonical_json_bytes(stage_result['effects']))
    return stage_result


def run_stage(*, env=None, batch_mod=None, fetch_mod=None, client_factory=None,
              now=None, print_fn=print, force=None,
              heartbeat_interval=DEFAULT_HEARTBEAT_INTERVAL,
              apply_fn=None, repo_root=None, status_out=None):
    """回傳 exit code：0 = 安全 skip／成功；1 = 未預期失敗；2 = 契約／配置錯誤阻斷。"""
    env = env if env is not None else os.environ
    batch_mod = batch_mod or _batch_utils
    fetch_mod = fetch_mod or _fetch_biggo
    now_fn = now or time.time
    apply_fn = apply_fn or _apply_mod.apply_bundle
    repo_root = repo_root or BASE
    force_mode = _force_requested(env, force)
    record = {
        'schemaVersion': 1,
        'status': None,
        'alert': False,
        'rerun': 'allowed',
        'requests': {'token': 0, 'search': 0},
        'at': None,
    }

    def emit(status, **detail):
        record['status'] = status
        record['at'] = coord_mod.utc_stamp(now_fn())
        record['alert'] = status in ALERT_STATUSES
        if status in ALERT_STATUSES:
            record['rerun'] = 'prohibited'
        record.update(detail)
        _emit(print_fn, status, **detail)

    try:
        # ---- 1. 本地階段先決；inactive（且非 force）唔准碰 coordinator／BigGo ----
        try:
            meta = batch_mod.load_meta()
        except batch_mod.MetaError as e:
            emit('blocked-meta-invalid', error=str(e)[:200])
            return 2

        if force_mode:
            cycle_id = _force_cycle_id(now_fn)
            stage_no = 1
            mode = 'force'
            workload = fetch_mod.force_workload()
            queued = workload['queuedModels']
        else:
            idx = meta.get('price_batch_idx', 0)
            if not meta.get('price_batch_start') or idx >= PRICE_BATCH_DAYS:
                emit('skip-not-active')
                return 0
            workload = fetch_mod.stage_workload(meta)
            if not workload:
                emit('skip-not-active')
                return 0
            stage_idx = workload['idx']
            cycle_id = f"{meta['price_batch_start']}:{stage_idx + 1}/{PRICE_BATCH_DAYS}"
            stage_no = stage_idx + 1
            mode = 'price-batch'
            queued = workload['queuedModels']
        record.update({'cycle': cycle_id, 'stage': stage_no, 'mode': mode})

        # ---- 2. Coordinator 配置缺失：保留快照，唔呼叫 BigGo ----
        cfg_status = coord_mod.config_status(env)
        if not cfg_status['configured']:
            emit('skip-coordinator-not-configured', reason=cfg_status['reason'],
                 cycle=cycle_id, stage=stage_no, queuedModels=queued, snapshot='preserved')
            return 0
        try:
            config = coord_mod.CoordinatorConfig.from_env(env)
        except coord_mod.CoordinatorConfigError as e:
            emit('skip-coordinator-config-invalid', error=type(e).__name__,
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

        # ---- 2b. Provider quota／window：冇共享 window ledger，配置存在即 fail-closed ----
        # 不得用 stage-local 80% 冒充 provider window enforcement；48h 冷卻亦唔係
        # provider window。零 BigGo、零 coordinator mutation、警報可見。
        if config.provider_quota_limit is not None or config.provider_window_end is not None:
            emit('blocked-quota-window-unsupported', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited',
                 reason='quota-window-accounting-unsupported',
                 providerQuotaLimitConfigured=config.provider_quota_limit is not None,
                 providerWindowEndConfigured=config.provider_window_end is not None)
            return 0

        # ---- 3. CAS acquire ----
        try:
            acquired = client.acquire(cycle_id, stage_no)
        except coord_mod.CoordinatorError as e:
            emit('blocked-coordinator-error', error=type(e).__name__)
            return 2
        result = acquired['result']
        state = acquired['state']

        if result == 'lost':
            # active 他人：零本地寫入、零 BigGo、零 apply；下次 daily 見 completed 才 import。
            emit('skip-lost-active', cycle=cycle_id, stage=stage_no,
                 rerun='allowed-when-completed')
            return 0
        if result == 'blocked-incomplete':
            emit('blocked-incomplete', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited',
                 reason=acquired.get('reason') or 'unfinished-stage',
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'),
                 remoteStatus=state.get('status'))
            return 0
        if result == 'blocked-stage-gap':
            emit('blocked-stage-gap', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited',
                 reason=acquired.get('reason') or 'stage-gap',
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'))
            return 0
        if result == 'blocked-force-cycle-incomplete':
            emit('blocked-force-cycle-incomplete', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited',
                 reason=acquired.get('reason') or 'normal-cycle-incomplete',
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'))
            return 0
        if result == 'blocked-stale':
            emit('blocked-stale', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited',
                 reason=acquired.get('reason') or 'request-older-than-remote-cycle',
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'))
            return 0
        if result == 'stale-writer':
            emit('skip-stale-writer', cycle=cycle_id, stage=stage_no,
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'))
            return 0
        if result == 'completed-ahead':
            emit('skip-completed-ahead', cycle=cycle_id, stage=stage_no,
                 remoteCycle=state.get('cycleId'), remoteStage=state.get('stage'))
            return 0
        if result == 'needs-review' or result == 'blocked-needs-review':
            emit('needs-review', cycle=cycle_id, stage=stage_no, rerun='prohibited',
                 reason=(state.get('lastReviewReason') or result))
            return 0
        if result == 'completed-idempotent':
            return _import_and_apply(client, emit, apply_fn, repo_root, now_fn,
                                     cycle_id, stage_no, final_status='completed-idempotent-applied')
        if result == 'winner-intent':
            return _recover_intent(client, emit, apply_fn, repo_root, now_fn,
                                   cycle_id, stage_no)
        if result not in ('winner', 'renewed'):
            emit('blocked-coordinator-error', error=f'unexpected-acquire:{result}')
            return 2

        # 2c. Legacy state 帶 provider quota／window 事實：冇 window ledger，唔可以行
        # stage-local 80%；釋放 lease 後 fail-closed（零 BigGo）。
        if state.get('providerQuotaLimit') is not None \
                or state.get('providerWindowEnd') is not None:
            client.abort_without_calls()
            emit('blocked-quota-window-unsupported', cycle=cycle_id, stage=stage_no,
                 rerun='prohibited', source='state-legacy',
                 reason='quota-window-accounting-unsupported')
            return 0

        # ---- 4. winner：cooldown／budget（零寫入；intent 之前）----
        if state.get('cooldownUntil', 0) > int(now_fn()):
            client.abort_without_calls()
            emit('skip-cooldown', cycle=cycle_id, stage=stage_no,
                 cooldownUntil=state['cooldownUntil'])
            return 0
        budget = client.budget(queued)
        if not budget['allowed']:
            client.abort_without_calls()
            emit('skip-budget', cycle=cycle_id, stage=stage_no,
                 searchUsed=budget['searchUsed'], effectiveSearchCap=budget['effectiveSearchCap'])
            return 0

        # ---- 5. write-ahead intent：失敗即禁止任何 BigGo 請求 ----
        try:
            client.mark_calls_may_have_started()
        except coord_mod.LeaseLostError:
            emit('needs-review', cycle=cycle_id, stage=stage_no, rerun='prohibited',
                 reason='lease-lost-before-intent', lease='lost')
            return 0
        except coord_mod.CoordinatorError as e:
            emit('blocked-intent-persist', cycle=cycle_id, stage=stage_no,
                 error=type(e).__name__)
            return 2

        # 5b. per-request hard cap：1 smoke + 2×本 stage 處理型號數；token/search 分開。
        # provider factual 80% 唔會在這裡假裝 enforce（已在上方 fail-closed）；
        # limiter 只用本地 hard cap。
        limiter = RequestLimiter(search_cap=1 + 2 * queued, token_cap=1)

        # ---- 6. winner：heartbeat ＋ 全程網絡工作（finally 保證停止）----
        heartbeat = LeaseHeartbeat(client, interval=heartbeat_interval, now_fn=now_fn)
        heartbeat.start()
        fetch_mod.set_request_limiter(limiter)
        fetch_mod.set_abort_check(lambda: heartbeat.lost)
        try:
            smoke_delta = {'token': 0, 'search': 0}
            batch_delta = {'token': 0, 'search': 0}

            # 6a. bounded smoke（最多 1 個 product-search request）
            before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
            smoke_ok = False
            try:
                smoke_ok = bool(fetch_mod.run_smoke())
            except Exception as e:  # noqa: BLE001 - smoke 例外一律視為失敗
                print_fn(f'  ⚠️ smoke 例外：{type(e).__name__}')
            after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
            smoke_delta = _delta(after, before)
            _record_evidence(client, fetch_mod, smoke_delta)
            record['requests'] = {'token': smoke_delta['token'],
                                  'search': smoke_delta['search']}

            if heartbeat.lost:
                calls = 'made' if smoke_delta['token'] + smoke_delta['search'] else 'none'
                if calls == 'made':
                    _needs_review(client, emit, cycle_id, stage='smoke-heartbeat',
                                  reason='lease-lost-during-smoke', lease_lost=True)
                else:
                    _safe_abort(client, print_fn, emit, cycle_id, stage_no,
                                reason='skip-smoke-no-calls', lease='lost')
                return 0

            if not smoke_ok:
                if smoke_delta['token'] + smoke_delta['search'] > 0:
                    _needs_review(client, emit, cycle_id, stage='smoke',
                                  reason='smoke-failed')
                else:
                    _safe_abort(client, print_fn, emit, cycle_id, stage_no,
                                reason='skip-smoke-no-calls')
                return 0

            # 6b. 本地價格批次／force（staged；零本地寫入）
            before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
            batch_result = {}
            try:
                if force_mode:
                    payload = fetch_mod.run_force_batch(
                        None, smoke=False, should_abort=lambda: heartbeat.lost,
                        exit_on_fail=False)
                else:
                    payload = fetch_mod.run_price_batch(should_abort=lambda: heartbeat.lost)
                batch_result = payload if isinstance(payload, dict) else {'status': 'unknown'}
            except Exception as e:  # noqa: BLE001
                after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
                batch_delta = _delta(after, before)
                _record_evidence(client, fetch_mod, batch_delta)
                if batch_delta['token'] + batch_delta['search'] > 0:
                    _needs_review(client, emit, cycle_id, stage='batch-exception',
                                  reason='batch-exception', lease_lost=heartbeat.lost)
                else:
                    _safe_abort(client, print_fn, emit, cycle_id, stage_no,
                                reason='skip-batch-no-calls', error=type(e).__name__)
                return 0
            after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
            batch_delta = _delta(after, before)
            _record_evidence(client, fetch_mod, batch_delta)
            total_calls = (smoke_delta['token'] + smoke_delta['search']
                           + batch_delta['token'] + batch_delta['search'])
            record['requests'] = {
                'token': smoke_delta['token'] + batch_delta['token'],
                'search': smoke_delta['search'] + batch_delta['search'],
            }
            batch_status = batch_result.get('status', 'unknown')

            if heartbeat.lost or batch_status == 'aborted':
                # 項目自身 48h fallback（冇 provider Retry-After 證據時）
                if batch_result.get('projectCooldown') and not _safe_evidence(fetch_mod):
                    client.state['cooldownUntil'] = max(
                        client.state.get('cooldownUntil', 0),
                        int(now_fn()) + coord_mod.PROJECT_COOLDOWN_SECONDS)
                _write_review_evidence(status_out, batch_result)
                if batch_status != 'aborted' or heartbeat.lost:
                    _needs_review(client, emit, cycle_id, stage='batch-heartbeat',
                                  reason=('lease-lost-during-batch' if heartbeat.lost
                                          else 'batch-aborted'),
                                  lease_lost=heartbeat.lost,
                                  extra={'batchStatus': batch_status})
                elif total_calls > 0:
                    _needs_review(client, emit, cycle_id, stage='batch',
                                  reason='batch-aborted', extra={'batchStatus': batch_status})
                else:
                    _safe_abort(client, print_fn, emit, cycle_id, stage_no,
                                reason='skip-batch-no-calls', batchStatus=batch_status)
                return 0

            if batch_status != 'completed':
                _write_review_evidence(status_out, batch_result)
                if total_calls > 0:
                    _needs_review(client, emit, cycle_id, stage='batch',
                                  reason=f'batch-{batch_status}', lease_lost=heartbeat.lost,
                                  extra={'batchStatus': batch_status})
                else:
                    _safe_abort(client, print_fn, emit, cycle_id, stage_no,
                                reason='skip-batch-no-calls', batchStatus=batch_status)
                return 0

            # ---- 7. publish bundle → CAS completed → local apply ----
            stage_result = _build_stage_result(
                cycle_id=cycle_id, stage_no=stage_no, mode=mode, client=client,
                now_fn=now_fn, staged=batch_result)
            manifest_fields = {
                'cycleId': cycle_id,
                'stage': stage_no,
                'mode': mode,
                'writer': owner,
                'writerKind': 'github-actions' if env.get('GITHUB_ACTIONS') == 'true'
                              else 'self-host',
                'commit': env.get('GITHUB_SHA') or env.get('AIRCON_COMMIT') or 'unknown',
                'publishedAt': coord_mod.utc_stamp(now_fn()),
            }
            try:
                published = client.publish_bundle(
                    batch_result['baseSnapshot'], batch_result['snapshot'],
                    stage_result, manifest_fields)
            except coord_mod.CoordinatorError as e:
                _write_review_evidence(status_out, batch_result)
                _needs_review(client, emit, cycle_id, stage='publish',
                              reason='publish-uncertain', lease_lost=heartbeat.lost,
                              extra={'error': type(e).__name__})
                return 0
            try:
                client.commit(status='completed',
                              snapshot_hash=published['newSnapshotHash'],
                              snapshot_path=published['snapshotPath'],
                              stage_result_hash=published['stageResultHash'],
                              stage_result_path=published['stageResultPath'],
                              bundle_hash=published['bundleHash'])
            except coord_mod.CoordinatorError as e:
                _write_review_evidence(status_out, batch_result)
                _needs_review(client, emit, cycle_id, stage='commit',
                              reason='commit-uncertain', lease_lost=heartbeat.lost,
                              extra={'error': type(e).__name__})
                return 0

            bundle = {
                'manifest': published['manifest'],
                'stageResult': stage_result,
                'baseSnapshot': batch_result['baseSnapshot'],
                'newSnapshot': batch_result['snapshot'],
                'baseSnapshotHash': published['baseSnapshotHash'],
                'newSnapshotHash': published['newSnapshotHash'],
                'stageResultHash': published['stageResultHash'],
                'bundleHash': published['bundleHash'],
                'snapshotPath': published['snapshotPath'],
            }
            try:
                apply_report = apply_fn(bundle, repo_root=repo_root, now=now_fn())
            except _apply_mod.ApplyError as e:
                emit('completed-apply-failed', cycle=cycle_id, stage=stage_no,
                     error=type(e).__name__, published=True,
                     snapshotHash=published['newSnapshotHash'][:16])
                return 0
            emit('completed', cycle=cycle_id, stage=stage_no,
                 localApply='noop' if apply_report.get('noop') else 'applied',
                 published=True, snapshotHash=published['newSnapshotHash'][:16],
                 bundleHash=published['bundleHash'][:16],
                 requests={'token': record['requests']['token'],
                           'search': record['requests']['search']})
            return 0
        finally:
            heartbeat.stop()
            fetch_mod.set_request_limiter(None)
            fetch_mod.set_abort_check(None)
    finally:
        _write_status(status_out, record)


def _safe_abort(client, print_fn, emit, cycle_id, stage_no, *, reason, **extra):
    """零呼叫安全收手：清 intent（今次確認冇 HTTP request）＋回 idle。"""
    try:
        client.abort_without_calls(clear_intent=True)
    except coord_mod.LeaseLostError:
        extra = dict(extra, lease='lost')
    except coord_mod.CoordinatorError as e:
        extra = dict(extra, commit=f'failed-{type(e).__name__}')
    emit(reason, cycle=cycle_id, stage=stage_no, **extra)


def _import_and_apply(client, emit, apply_fn, repo_root, now_fn, cycle_id, stage_no,
                      *, final_status):
    """completed-idempotent：import v2 bundle → apply（零 BigGo）。legacy → 人手。"""
    try:
        bundle = client.import_bundle(cycle_id, stage_no)
    except coord_mod.LegacyBundleError:
        emit('completed-legacy-manual', cycle=cycle_id, stage=stage_no,
             rerun='prohibited')
        return 0
    except coord_mod.CoordinatorError as e:
        emit('completed-bundle-unavailable', cycle=cycle_id, stage=stage_no,
             error=type(e).__name__, rerun='prohibited')
        return 0
    try:
        report = apply_fn(bundle, repo_root=repo_root, now=now_fn())
    except _apply_mod.ApplyError as e:
        emit('completed-apply-failed', cycle=cycle_id, stage=stage_no,
             error=type(e).__name__, imported=True,
             snapshotHash=bundle['newSnapshotHash'][:16])
        return 0
    emit(final_status, cycle=cycle_id, stage=stage_no,
         localApply='noop' if report.get('noop') else 'applied', imported=True,
         snapshotHash=bundle['newSnapshotHash'][:16])
    return 0


def _recover_intent(client, emit, apply_fn, repo_root, now_fn, cycle_id, stage_no):
    """expired active 帶 intent：只可 adopt 完整 bundle；否則 needs_review。"""
    try:
        bundle = client.import_bundle(cycle_id, stage_no)
    except coord_mod.LegacyBundleError:
        commit_note = None
        try:
            client.commit(status='needs_review',
                          last_review_reason='expired-intent-legacy-manual')
        except coord_mod.CoordinatorError as e:
            commit_note = type(e).__name__
        emit('completed-legacy-manual', cycle=cycle_id, stage=stage_no,
             rerun='prohibited', commit=('failed-' + commit_note) if commit_note else 'ok')
        return 0
    except coord_mod.CoordinatorError as e:
        commit_note = None
        try:
            client.commit(status='needs_review',
                          last_review_reason='expired-with-intent-no-valid-bundle')
        except coord_mod.CoordinatorError as e2:
            commit_note = type(e2).__name__
        emit('needs-review', cycle=cycle_id, stage=stage_no, rerun='prohibited',
             reason='expired-with-intent-no-valid-bundle', error=type(e).__name__,
             commit=('failed-' + commit_note) if commit_note else 'ok')
        return 0
    # bundle 完整：零 BigGo adopt → CAS completed → apply
    try:
        client.commit(status='completed',
                      snapshot_hash=bundle['newSnapshotHash'],
                      snapshot_path=bundle['snapshotPath'],
                      stage_result_hash=bundle['stageResultHash'],
                      bundle_hash=bundle['bundleHash'])
    except coord_mod.LeaseLostError:
        emit('needs-review', cycle=cycle_id, stage=stage_no, rerun='prohibited',
             reason='lease-lost-before-recover-commit')
        return 0
    except coord_mod.CoordinatorError as e:
        emit('needs-review', cycle=cycle_id, stage=stage_no, rerun='prohibited',
             reason='recover-commit-failed', error=type(e).__name__)
        return 0
    try:
        report = apply_fn(bundle, repo_root=repo_root, now=now_fn())
    except _apply_mod.ApplyError as e:
        emit('completed-apply-failed', cycle=cycle_id, stage=stage_no,
             error=type(e).__name__, recovered=True,
             snapshotHash=bundle['newSnapshotHash'][:16])
        return 0
    emit('completed-recovered', cycle=cycle_id, stage=stage_no,
         localApply='noop' if report.get('noop') else 'applied', imported=True,
         snapshotHash=bundle['newSnapshotHash'][:16])
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    argv = list(sys.argv[1:] if argv is None else argv)
    force = '--force' in argv
    status_out = None
    if '--status-out' in argv:
        i = argv.index('--status-out')
        if i + 1 >= len(argv):
            print('❌ --status-out 缺少 path', file=sys.stderr)
            return 2
        status_out = argv[i + 1]
        del argv[i:i + 2]
    unknown = [a for a in argv if a != '--force']
    if unknown:
        print(f'❌ 唔支援嘅參數：{unknown}（只支援 --force／--status-out PATH；'
              f'force intent 亦可以經 AIRCON_BIGGO_FORCE_STAGE=1）', file=sys.stderr)
        return 2
    return run_stage(force=True if force else None, status_out=status_out)


if __name__ == '__main__':
    sys.exit(main())
