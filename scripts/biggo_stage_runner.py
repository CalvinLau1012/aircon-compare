#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo 價錢批次階段閘門（approved design C 嘅 orchestration 層）。

順序（任何情況都唔可以調亂）：
  1. 先讀本地 `prices_meta.json` 批次階段；inactive／已完成 → 即回，零 BigGo
     token／search，亦零 coordinator 讀寫；
  2. active 但冇 coordinator 配置 → 保留現有價錢快照，回清晰非秘密 status，
     **唔會**呼叫 BigGo（避免兩個 writer 重複呼叫）；
  3. active + 配置 → CAS acquire lease：
       - completed cycle+stage → idempotent skip；
       - needs_review → 禁止自動重跑；
       - lost → 只可以按 cycle／stage／schema／hash 匯入 winner snapshot；
       - winner → 檢查 cooldown／budget（per-stage cap = 1 smoke + 2×queued models），
         先行一次 bounded smoke，再跑本地價格批次；
  4. winner 全部成功 → schema-validated snapshot + manifest + SHA-256 發佈到
     coordinator repo，狀態 completed；
  5. 任何已發出呼叫但 snapshot 發布唔確定 → needs_review，唔會自動重跑。

依賴（HTTP／fetch／時鐘）全部可注入，測試完全離線。
"""
from __future__ import annotations

import json
import os
import sys
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


def run_stage(*, env=None, batch_mod=None, fetch_mod=None, client_factory=None,
              now=None, print_fn=print, snapshot_reader=None, snapshot_writer=None):
    """回傳 exit code：0 = 安全 skip／成功；1 = 未預期失敗；2 = 契約／配置錯誤阻斷。"""
    env = env if env is not None else os.environ
    batch_mod = batch_mod or _batch_utils
    fetch_mod = fetch_mod or _fetch_biggo
    now_fn = now or time.time
    snapshot_reader = snapshot_reader or _default_snapshot_reader(fetch_mod)
    snapshot_writer = snapshot_writer or _default_snapshot_writer(fetch_mod)

    # ---- 1. 本地階段先決；inactive 唔准碰 coordinator／BigGo ----
    try:
        meta = batch_mod.load_meta()
    except batch_mod.MetaError as e:
        _emit(print_fn, 'blocked-meta-invalid', error=str(e)[:200])
        return 2
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
        acquired = client.acquire(cycle_id, stage_idx + 1)
    except coord_mod.CoordinatorError as e:
        _emit(print_fn, 'blocked-coordinator-error', error=type(e).__name__)
        return 2
    result = acquired['result']
    if result == 'completed-idempotent':
        _emit(print_fn, 'completed-idempotent', cycle=cycle_id)
        return 0
    if result == 'needs-review':
        _emit(print_fn, 'needs-review', cycle=cycle_id, rerun='prohibited',
              reason=(acquired['state'].get('lastReviewReason') or 'prior-review'))
        return 0
    if result == 'lost':
        try:
            imported = client.import_snapshot(cycle_id, stage_idx + 1)
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

    # ---- 5. bounded smoke（最多 1 個 product-search request；8 秒 timeout） ----
    before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
    smoke_ok = False
    try:
        smoke_ok = bool(fetch_mod.run_smoke())
    except Exception as e:  # noqa: BLE001 - smoke 例外一律視為失敗
        print_fn(f'  ⚠️ smoke 例外：{type(e).__name__}')
    after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
    smoke_delta = _delta(after, before)
    client.record_attempt('token', smoke_delta['token'])
    client.record_attempt('search', smoke_delta['search'])
    evidence = _safe_evidence(fetch_mod)
    if evidence:
        client.record_response(evidence['status'])
        client.apply_cooldown(evidence['status'], {'Retry-After': evidence.get('retryAfter')})

    if not smoke_ok:
        calls_made = smoke_delta['token'] + smoke_delta['search'] > 0
        if calls_made:
            client.commit(status='needs_review', last_review_reason='smoke-failed')
            _emit(print_fn, 'needs-review', cycle=cycle_id, stage='smoke',
                  rerun='prohibited', calls='made')
        else:
            client.abort_without_calls()
            _emit(print_fn, 'skip-smoke-no-calls', cycle=cycle_id)
        return 0

    # ---- 6. 本地價格批次 ----
    before = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
    try:
        batch_result = fetch_mod.run_price_batch()
    except Exception as e:  # noqa: BLE001
        after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
        delta = _delta(after, before)
        client.record_attempt('token', delta['token'])
        client.record_attempt('search', delta['search'])
        if delta['token'] + delta['search'] > 0:
            client.commit(status='needs_review', last_review_reason='batch-exception')
        else:
            client.abort_without_calls()
        _emit(print_fn, 'needs-review' if delta['search'] + delta['token'] else 'skip-batch-no-calls',
              cycle=cycle_id, error=type(e).__name__)
        return 0
    after = _stat(getattr(fetch_mod, 'REQUEST_STATS', {}))
    batch_delta = _delta(after, before)
    client.record_attempt('token', batch_delta['token'])
    client.record_attempt('search', batch_delta['search'])
    evidence = _safe_evidence(fetch_mod)
    if evidence:
        client.record_response(evidence['status'])
        client.apply_cooldown(evidence['status'], {'Retry-After': evidence.get('retryAfter')})

    batch_status = batch_result.get('status') if isinstance(batch_result, dict) else 'unknown'

    # ---- 7. 成功才發佈；其他一律 needs_review（有呼叫）或安全收手（零呼叫） ----
    if batch_status == 'completed':
        try:
            snapshot = snapshot_reader()
            coord_mod.validate_price_snapshot(snapshot)
            manifest = {
                'schemaVersion': coord_mod.MANIFEST_SCHEMA_VERSION,
                'cycleId': cycle_id,
                'stage': stage_idx + 1,
                'writer': owner,
                'writerKind': 'github-actions' if env.get('GITHUB_ACTIONS') == 'true'
                              else 'self-host',
                'commit': env.get('GITHUB_SHA') or env.get('AIRCON_COMMIT') or 'unknown',
                'publishedAt': coord_mod.utc_stamp(now_fn()),
                'recordCount': len(snapshot),
            }
            published = client.publish_snapshot(snapshot, manifest)
            client.commit(status='completed', snapshot_hash=published['snapshotHash'],
                          snapshot_path=published['snapshotPath'])
            _emit(print_fn, 'completed', cycle=cycle_id, snapshotHash=published['snapshotHash'],
                  records=len(snapshot))
            return 0
        except Exception as e:  # noqa: BLE001 - 發佈／commit 失敗 = publication uncertain
            try:
                client.commit(status='needs_review', last_review_reason='publish-uncertain')
            except Exception:  # noqa: BLE001
                pass
            _emit(print_fn, 'needs-review', cycle=cycle_id, stage='publish',
                  rerun='prohibited', error=type(e).__name__)
            return 0

    calls_made = (smoke_delta['token'] + smoke_delta['search']
                  + batch_delta['token'] + batch_delta['search']) > 0
    if calls_made:
        client.commit(status='needs_review', last_review_reason=f'batch-{batch_status}')
        _emit(print_fn, 'needs-review', cycle=cycle_id, stage='batch',
              batchStatus=batch_status, rerun='prohibited')
    else:
        client.abort_without_calls()
        _emit(print_fn, 'skip-batch-no-calls', cycle=cycle_id, batchStatus=batch_status)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    return run_stage()


if __name__ == '__main__':
    sys.exit(main())
