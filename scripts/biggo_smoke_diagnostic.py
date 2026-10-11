#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded manual BigGo diagnostic（預設純本地 preview；只有 `--execute` 先會連網）。

設計重點
--------
- **預設 preview**：零 coordinator、零 provider。只由當前本地 dataset 揀一個
  candidate（`biggo_candidate.select_candidate`），報告 planned 上限
  （<=1 token＋<=1 search＋<=19 coordinator HTTP ops）同本地 stage 狀態。
- **`--execute`** 先做完整 preflight：run identity、provider 憑證、本地 active
  stage/workload、coordinator 配置、remote state cycle/stage 相容；缺 remote state
  一律 blocked，唔會 bootstrap 或者自創 cycle。
- **共享 lease／budget**：用同一個 coordinator state file 同 lease；以 deep copy
  現有 remote state 嘅 CAS 一次寫入（narrowly scoped reservation）同時：
  acquire lease、record diagnostic marker、同將 reserved upper bound（1 token＋
  1 search）加入 `requestAttempts`。business 欄位（cycle/stage、snapshot/bundle
  refs、intent、status/progress）一律保留；**唔會** set business
  `callsMayHaveStarted`、唔會 reset 任何 counter、唔會 advance cycle/stage。
- **Reserved vs actual**：`requestAttempts` 記錄嘅係 durable reserved upper bound
  （請求前已加，唔會事後再加／減）；實際觀察到嘅 token/search delta 只記入
  diagnostic marker (`actual`) 同報告，兩者明確分開。auth 失敗可能用咗 token
  但未發 search，reserved search 仍然會計入共享上限（保守）。
- **Provider 階段**：`RequestLimiter(token_cap=1, search_cap=1)`、單次 bounded
  `run_smoke`（8 秒 timeout、無 retry）、每個 request phase 前經 abort hook 核實
  lease ownership；redirect 已停用；完成後還原 module-global limiter/abort/opener。
- **結尾**：只 release 今次 diagnostic 嘅 lease（保留 business status 同所有
  無關欄位），persist sanitized outcome／actual facts、honour 403／429 factual
  cooldown。finalize 失敗／歧義 → explicit uncertain 結果（非零），唔會清走
  其他 owner 嘅 lease，亦唔會靜靜當成功。
- **無本地副作用**：唔寫 price snapshot、queue、prices_meta、history、blacklist
  或任何 publication；報告只含 allowlisted facts，唔會輸出憑證、raw response
  body、private coordinator 識別或者 exception message。
- **Read-only coordinator check（`--check-coordinator`）**：同 `--execute` 互斥。完成
  有效 coordinator 配置後只做**一次** bounded GitHub GET（`read_state`）；零
  PUT／lease／CAS／bootstrap／snapshot／reservation／provider 呼叫，亦唔需要 run
  identity、本地 active stage 或 BigGo 憑證。報告只有 sanitized 讀取事實同
  coordinator identity digest（唔含 raw repo／path／token）；成功唔代表 write
  權限、token 相等、BigGo 連線或 daily stage 相容。

Exit code：0 = preview／local-skip／healthy／coordinator-read-ok；1 = probe failure／uncertain；
2 = preflight／contract／config／read blocked。
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (BASE, SCRIPT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import batch_utils as _batch_utils  # noqa: E402
import biggo_candidate as candidate_mod  # noqa: E402
import biggo_coordinator as coord_mod  # noqa: E402
import fetch_biggo as fetch_mod  # noqa: E402
from biggo_limiter import RequestLimiter  # noqa: E402
from biggo_smoke_result import validate_smoke_result  # noqa: E402

# Finite coordinator HTTP op cap（讀＋寫＋cleanup 全部計）。實測成功路徑最多約
# 7 ops；連 CAS 重試最多約 15，19 係保守上限；到 cap 即 fail closed。
MAX_COORDINATOR_REQUESTS = 19
MAX_COORDINATOR_CHECK_REQUESTS = 1
COORDINATOR_CHECK_MODE = 'coordinator-check'
COORDINATOR_CHECK_OK = 'coordinator-read-ok'
COORDINATOR_CHECK_PLANNED = {'tokenLimit': 0, 'searchLimit': 0,
                             'coordinatorOpsCap': MAX_COORDINATOR_CHECK_REQUESTS,
                             'smokeTimeoutSeconds': 0}
COORDINATOR_CHECK_NOT_PROVEN = ('write-permission', 'token-equality',
                                'provider-connectivity', 'daily-stage-compatibility')
RESERVE_CAS_ATTEMPTS = 3
FINALIZE_CAS_ATTEMPTS = 2
DIAGNOSTIC_FIELD = 'diagnosticReservation'
DIAGNOSTIC_SCHEMA_VERSION = 1
RESERVED = {'token': 1, 'search': 1}

RUN_ID_RE = re.compile(r'^[0-9A-Za-z._-]{1,64}$')
LABEL_RE = re.compile(r'^[A-Za-z0-9 ._+/()\-|:]{1,80}$')
AUTH_FACTS = frozenset({'anonymous', 'verified', 'failed', 'unknown'})
HEALTHY_STATUSES = ('healthy-with-price', 'healthy-without-price')
PROBE_FAILURE_STATUSES = ('probe-auth-failed', 'probe-rate-limited',
                          'probe-transport', 'probe-invalid-response',
                          'probe-aborted', 'probe-exception')
UNCERTAIN_STATUSES = ('uncertain-reservation', 'uncertain-lease-lost',
                      'uncertain-cleanup', 'uncertain-finalize',
                      'uncertain-lease-not-confirmed')
PREVIEW_STATUSES = ('preview', 'preview-local-skipped', 'local-skipped')


class DiagnosticBlocked(Exception):
    """Preflight／contract blocked：未有 provider 呼叫，fail closed。"""

    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        super().__init__(status)


class _CappedApi:
    """Coordinator HTTP ops 硬上限；到 cap 即 raise（cleanup 都計在內）。"""

    def __init__(self, api, cap=MAX_COORDINATOR_REQUESTS):
        self._api = api
        self._cap = int(cap)
        self.calls = 0

    def __call__(self, method, url, body=None, headers=None):
        if self.calls >= self._cap:
            raise coord_mod.CoordinatorApiError('diagnostic-coordinator-cap')
        self.calls += 1
        return self._api(method, url, body, headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """停用 redirect replay：302／303／307 直接當 HTTPError，唔會跟去新目標。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _harden_transport(disable_proxy=True):
    """停用 redirect replay；provider 階段亦停 proxy fallback，回傳舊 opener 供還原。

    `disable_proxy=False` 保留環境／已配置 proxy 行為（read-only coordinator check 用）。
    """
    previous = getattr(urllib.request, '_opener', None)
    handlers = [_NoRedirect()]
    if disable_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(urllib.request.build_opener(*handlers))
    return previous


def _restore_transport(previous):
    urllib.request._opener = previous


def _stat(value):
    data = value if isinstance(value, dict) else {}
    return {'token': int(data.get('token', 0) or 0),
            'search': int(data.get('search', 0) or 0)}


def _delta(after, before):
    return {'token': max(0, after.get('token', 0) - before.get('token', 0)),
            'search': max(0, after.get('search', 0) - before.get('search', 0))}


def _safe_label(value):
    text = str(value if value is not None else '')
    return text if LABEL_RE.match(text) else '[filtered]'


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _preexisting_limiter(fetch):
    """讀取診斷前已存在嘅 request limiter（getter 優先，fake 用屬性 fallback）。"""
    getter = getattr(fetch, 'get_request_limiter', None)
    if callable(getter):
        return getter()
    return getattr(fetch, 'request_limiter', None)


def _preexisting_abort_check(fetch):
    """讀取診斷前已存在嘅 abort hook（getter 優先，fake 用屬性 fallback）。"""
    getter = getattr(fetch, 'get_abort_check', None)
    if callable(getter):
        return getter()
    return getattr(fetch, 'abort_check', None)


def _base_report(mode):
    return {
        'schemaVersion': 1,
        'mode': mode,
        'status': None,
        'reason': None,
        'providersCalled': False,
        'providerPhaseEntered': False,
        'candidate': None,
        'selection': None,
        'localStage': None,
        'planned': None,
        'reservedUpperBound': None,
        'actual': {'token': 0, 'search': 0},
        'coordinatorOps': 0,
        'reservation': None,
        'remoteStage': None,
        'cycleStageMatches': None,
        'connectivity': None,
        'response': None,
        'price': None,
        'authFact': None,
        'replayGuard': 'coordinator-state-marker',
        'uncertainty': None,
        'exitCode': 0,
    }


def _planned():
    return {'tokenLimit': 1, 'searchLimit': 1,
            'coordinatorOpsCap': MAX_COORDINATOR_REQUESTS,
            'smokeTimeoutSeconds': int(getattr(fetch_mod, 'SMOKE_TIMEOUT', 8))}


def _candidate_view(selection):
    if not isinstance(selection, dict):
        return None
    return {'model': _safe_label(selection.get('model')),
            'brand': _safe_label(selection.get('brand')),
            'canonicalKey': _safe_label(selection.get('canonicalKey'))}


def _selection_view(info):
    if not isinstance(info, dict):
        return None
    return {'active': _safe_int(info.get('active')),
            'blacklisted': _safe_int(info.get('blacklisted')),
            'eligible': _safe_int(info.get('eligible'))}


def _local_stage_info(batch_mod, fetch):
    """Best-effort 本地 stage 狀態；preview／execute 共用（唔連網、唔寫檔）。"""
    try:
        meta = batch_mod.load_meta()
    except Exception:  # noqa: BLE001 - meta 損毀只反映本地狀態
        return {'metaValid': False, 'active': False, 'stage': None, 'queuedModels': 0}
    if not isinstance(meta, dict):
        return {'metaValid': False, 'active': False, 'stage': None, 'queuedModels': 0}
    start = meta.get('price_batch_start')
    idx = meta.get('price_batch_idx', 0)
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = 0
    days = int(getattr(batch_mod, 'PRICE_BATCH_DAYS', 7))
    if not start or idx >= days:
        return {'metaValid': True, 'active': False, 'stage': None, 'queuedModels': 0}
    try:
        workload = fetch.stage_workload(meta)
    except Exception:  # noqa: BLE001
        return {'metaValid': True, 'active': False, 'stage': None, 'queuedModels': 0}
    if not isinstance(workload, dict) or 'idx' not in workload:
        return {'metaValid': True, 'active': False, 'stage': None, 'queuedModels': 0}
    return {'metaValid': True, 'active': True,
            'stage': _safe_int(workload.get('idx')) + 1,
            'queuedModels': _safe_int(workload.get('queuedModels'))}


def _resolve_local_stage(batch_mod, fetch):
    """Execute preflight：要求既有 local active price-batch stage；回
    ``(cycle_id, stage_no, queued)`` 或 raise DiagnosticBlocked。"""
    try:
        meta = batch_mod.load_meta()
    except Exception:  # noqa: BLE001
        raise DiagnosticBlocked('blocked-meta-invalid', 'local-meta-invalid') from None
    if not isinstance(meta, dict) or not meta.get('price_batch_start'):
        raise DiagnosticBlocked('blocked-no-active-local-stage',
                                'local-stage-inactive')
    idx = meta.get('price_batch_idx', 0)
    days = int(getattr(batch_mod, 'PRICE_BATCH_DAYS', 7))
    if not isinstance(idx, int) or isinstance(idx, bool) or idx >= days:
        raise DiagnosticBlocked('blocked-no-active-local-stage', 'local-stage-out-of-range')
    try:
        workload = fetch.stage_workload(meta)
    except Exception:  # noqa: BLE001
        raise DiagnosticBlocked('blocked-no-active-local-stage',
                                'local-workload-error') from None
    if not isinstance(workload, dict) or 'idx' not in workload:
        raise DiagnosticBlocked('blocked-no-active-local-stage', 'local-workload-missing')
    stage_idx = workload['idx']
    if not isinstance(stage_idx, int) or isinstance(stage_idx, bool):
        raise DiagnosticBlocked('blocked-no-active-local-stage', 'local-stage-invalid')
    return (f"{meta['price_batch_start']}:{stage_idx + 1}/{days}", stage_idx + 1,
            _safe_int(workload.get('queuedModels')))


def _credential_state(fetch):
    helper = getattr(fetch, '_credentials_state', None)
    if callable(helper):
        try:
            return helper()
        except Exception:  # noqa: BLE001
            return 'unknown'
    return 'unknown'


def _reservation_marker(owner, attempt_id, cycle_id, stage_no, now):
    return {
        'schemaVersion': DIAGNOSTIC_SCHEMA_VERSION,
        'state': 'reserved',
        'owner': owner,
        'attemptId': attempt_id,
        'cycleId': cycle_id,
        'stage': stage_no,
        'reserved': dict(RESERVED),
        'actual': {'token': 0, 'search': 0},
        'reservedAt': int(now),
        'completedAt': None,
        'reason': None,
        'authFact': None,
        'reachable': None,
        'responseValid': None,
        'priceAvailable': None,
    }


def _reservation_view(state):
    marker = state.get(DIAGNOSTIC_FIELD) if isinstance(state, dict) else None
    if not isinstance(marker, dict):
        return None
    attempt_id = marker.get('attemptId')
    if not isinstance(attempt_id, str) or not RUN_ID_RE.match(attempt_id):
        attempt_id = None
    return {'state': str(marker.get('state'))[:32],
            'attemptId': attempt_id,
            'actual': _stat(marker.get('actual'))}


def _evaluate_remote(state, client, cycle_id, stage_no, owner, now, queued):
    """檢查 remote state 是否容許 diagnostic reservation；回 status 或 None。

    以 deepest business fields 為本：拒絕 completed／needs_review、非 active、
    business intent、未解決 diagnostic marker、legacy provider quota/window、
    cooldown、foreign valid lease、cycle/stage 唔相容、token/search allowance 不足。
    """
    if state is None:
        return 'blocked-no-remote-state'
    if state.get('cycleId') != cycle_id or state.get('stage') != stage_no:
        return 'blocked-incompatible-cycle-stage'
    status = state.get('status')
    if status in ('completed', 'needs_review'):
        return 'blocked-remote-' + status
    if status != 'active':
        # idle stage 可以被普通 runner 無 lease 檢查地接管；diagnostic 只喺
        # remote active（無 valid foreign lease）先預留，避免同 takeover 競賽。
        return 'blocked-remote-inactive'
    if state.get('callsMayHaveStarted'):
        return 'blocked-business-intent'
    if state.get('providerQuotaLimit') is not None \
            or state.get('providerWindowEnd') is not None:
        return 'blocked-quota-window-unsupported'
    if _safe_int(state.get('cooldownUntil')) > now:
        return 'blocked-cooldown'
    lease_owner = state.get('leaseOwner')
    if lease_owner is not None and lease_owner != owner \
            and _safe_int(state.get('leaseExpiresAt')) > now:
        return 'blocked-foreign-lease'
    marker = state.get(DIAGNOSTIC_FIELD)
    if marker is not None:
        if not isinstance(marker, dict) or marker.get('state') != 'completed':
            return 'blocked-diagnostic-pending'
    client.state = state
    budget = client.budget(queued)
    if budget['remainingToken'] < 1 or budget['remainingSearch'] < 1:
        return 'blocked-budget'
    return None


def _apply_reservation(state, owner, cycle_id, stage_no, marker, now):
    """Deep copy 上做 narrowly scoped diagnostic reservation（business 欄位保留）。"""
    state['leaseOwner'] = owner
    state['leaseAcquiredAt'] = int(now)
    state['leaseRenewedAt'] = int(now)
    state['leaseExpiresAt'] = int(now) + coord_mod.LEASE_SECONDS
    state['updatedAt'] = int(now)
    # Durable reserved upper bound：請求前先加；之後唔再加／唔會減。
    attempts = state.setdefault('requestAttempts', {'token': 0, 'search': 0})
    for kind in ('token', 'search'):
        attempts[kind] = _safe_int(attempts.get(kind)) + 1
    state[DIAGNOSTIC_FIELD] = copy.deepcopy(marker)
    return state


def _reserve(contents, client, cycle_id, stage_no, owner, queued, marker,
             now_fn):
    """一次 CAS 寫入：acquire lease ＋ durable reservation ＋ diagnostic marker。

    回 ``(state, error_status)``；失敗唔會有 provider 呼叫。CAS 衝突會重讀同重新
    檢查所有 guard（bounded，唔會無限 retry）；寫入結果歧義即 uncertain。
    """
    for _attempt in range(RESERVE_CAS_ATTEMPTS):
        try:
            state, sha = contents.read_state()
        except Exception:  # noqa: BLE001 - 任何讀取失敗都 blocked，唔外洩 message
            return None, 'blocked-coordinator-read'
        verdict = _evaluate_remote(state, client, cycle_id, stage_no, owner,
                                   now_fn(), queued)
        if verdict:
            return None, verdict
        candidate_state = _apply_reservation(copy.deepcopy(state), owner, cycle_id,
                                             stage_no, marker, now_fn())
        try:
            contents.write_state(candidate_state, sha)
            return candidate_state, None
        except coord_mod.CASConflict:
            continue
        except coord_mod.CoordinatorStateError:
            return None, 'blocked-reservation-contract'
        except Exception:  # noqa: BLE001 - PUT 可能已 commit 但回應遺失：唔可以當冇發生
            return None, 'uncertain-reservation'
    return None, 'blocked-reserve-conflict'


class _LeaseAbort:
    """每個 provider request phase 之前核實 lease ownership；唔確定即 abort。"""

    def __init__(self, contents, owner, cycle_id, stage_no, now_fn):
        self._contents = contents
        self._owner = owner
        self._cycle = cycle_id
        self._stage = stage_no
        self._now_fn = now_fn
        self.lease_lost = False

    def check(self):
        try:
            state, _sha = self._contents.read_state()
        except Exception:  # noqa: BLE001 - 唔確定即當 lease 唔可確認
            self.lease_lost = True
            return True
        if state is None or state.get('leaseOwner') != self._owner \
                or state.get('cycleId') != self._cycle \
                or state.get('stage') != self._stage \
                or state.get('status') != 'active' \
                or _safe_int(state.get('leaseExpiresAt')) <= int(self._now_fn()):
            self.lease_lost = True
            return True
        return False


def _smoke_status(reason):
    mapping = {'auth': 'probe-auth-failed', 'rate-limit': 'probe-rate-limited',
               'transport': 'probe-transport', 'invalid-response': 'probe-invalid-response',
               'aborted': 'uncertain-lease-not-confirmed'}
    return mapping.get(reason, 'probe-invalid-response')


def _safe_evidence(fetch):
    evidence = getattr(fetch, 'LAST_RATE_LIMIT_EVIDENCE', None) or {}
    status = evidence.get('status')
    if isinstance(status, int):
        return {'status': status, 'retryAfter': evidence.get('retryAfter')}
    return None


def _finalize(contents, owner, cycle_id, stage_no, in_memory, now_fn):
    """只 release 今次 lease；merge marker／cooldown／response counts，保留其他欄位。

    回 error status 或 None（成功）。
    """
    for _attempt in range(FINALIZE_CAS_ATTEMPTS):
        try:
            state, sha = contents.read_state()
        except Exception:  # noqa: BLE001 - 唔知 lease 現況即 uncertain，唔可以當成功
            return 'uncertain-cleanup'
        if state is None:
            return 'uncertain-lease-lost'
        if state.get('leaseOwner') != owner or state.get('cycleId') != cycle_id \
                or state.get('stage') != stage_no:
            # 另一個 owner 已接管：絕對唔可以清走人哋嘅 lease。
            return 'uncertain-lease-lost'
        final = copy.deepcopy(state)
        marker = in_memory.get(DIAGNOSTIC_FIELD) if isinstance(in_memory, dict) else None
        if isinstance(marker, dict):
            final[DIAGNOSTIC_FIELD] = copy.deepcopy(marker)
        final['cooldownUntil'] = max(_safe_int(state.get('cooldownUntil')),
                                     _safe_int(in_memory.get('cooldownUntil')))
        counts = dict(state.get('responseStatusCounts') or {})
        for key, value in (in_memory.get('responseStatusCounts') or {}).items():
            counts[key] = max(_safe_int(counts.get(key)), _safe_int(value))
        final['responseStatusCounts'] = counts
        final['leaseOwner'] = None
        final['leaseExpiresAt'] = 0
        final['updatedAt'] = int(now_fn())
        try:
            contents.write_state(final, sha)
            return None
        except coord_mod.CASConflict:
            continue
        except coord_mod.CoordinatorStateError:
            return 'uncertain-finalize'
        except Exception:  # noqa: BLE001 - 寫入歧義：explicit uncertain，唔會清他人 lease
            return 'uncertain-cleanup'
    return 'uncertain-cleanup'


def _finish(report, status, *, reason=None, exit_code=None, uncertainty=None):
    report['status'] = status
    if reason is not None:
        report['reason'] = reason[:120]
    if uncertainty is not None:
        report['uncertainty'] = uncertainty[:120]
    if exit_code is None:
        if status in PREVIEW_STATUSES or status in HEALTHY_STATUSES \
                or status == COORDINATOR_CHECK_OK:
            exit_code = 0
        elif status in PROBE_FAILURE_STATUSES or status in UNCERTAIN_STATUSES:
            exit_code = 1
        else:
            exit_code = 2
    report['exitCode'] = exit_code
    return report


def _run_preview(report, fetch, batch_mod, selector):
    report['planned'] = _planned()
    report['localStage'] = _local_stage_info(batch_mod, fetch)
    try:
        selection, info = selector()
    except Exception:  # noqa: BLE001 - malformed dataset 係本地阻斷，唔可以當成功
        return _finish(report, 'blocked-candidate-selection',
                       reason='candidate-selection-error')
    if selection is None:
        info = info if isinstance(info, dict) else {}
        report['selection'] = _selection_view(info)
        return _finish(report, 'preview-local-skipped',
                       reason=str(info.get('reason') or 'no-active-eligible-model'))
    report['candidate'] = _candidate_view(selection)
    return _finish(report, 'preview', reason='local-preview-no-provider-call')


def _production_check_contents(config, now_fn):
    """Check 模式 client：一個 bounded GET cap；唔會 retry／redirect replay。"""
    base = coord_mod.GitHubContentsClient(config, now=now_fn)
    capped = _CappedApi(base._default_api, MAX_COORDINATOR_CHECK_REQUESTS)
    return coord_mod.GitHubContentsClient(config, now=now_fn, api=capped)


def _run_coordinator_check(report, env, now_fn, contents_factory=None):
    """Read-only coordinator readability check。

    完整有效配置後恰好一次 `read_state` GET；唔碰本地 stage、run identity、provider
    憑證或任何寫入。失敗全部 fail closed（非零），且只輸出固定 sanitized reason。
    """
    report['readOnly'] = True
    report['planned'] = dict(COORDINATOR_CHECK_PLANNED)
    cfg_status = coord_mod.config_status(env)
    if not cfg_status.get('configured'):
        return _finish(report, 'blocked-coordinator-config',
                       reason=str(cfg_status.get('reason') or 'coordinator-not-configured'))
    try:
        config = coord_mod.CoordinatorConfig.from_env(env)
    except (coord_mod.CoordinatorConfigError, TypeError, ValueError):
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-config-invalid')
    except Exception:  # noqa: BLE001 - 配置解析任何失敗都 fail closed，零 HTTP
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-config-error')
    if config is None:
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-not-configured')
    report['identity'] = {
        'version': coord_mod.IDENTITY_DIGEST_VERSION,
        'representation': coord_mod.IDENTITY_DIGEST_REPRESENTATION,
        'digest': coord_mod.coordinator_identity_digest(config),
    }
    try:
        if contents_factory is None:
            contents = _production_check_contents(config, now_fn)
        else:
            contents = contents_factory(config, now_fn)
    except Exception:  # noqa: BLE001 - client 建立失敗即 blocked，冇 HTTP
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-client-error')
    previous_opener = _harden_transport(disable_proxy=False)
    failure = None
    state = None
    try:
        state, _sha = contents.read_state()
    except coord_mod.CoordinatorStateError:
        failure = ('blocked-coordinator-invalid-response', 'coordinator-state-invalid')
    except coord_mod.CoordinatorApiError as exc:
        http_status = getattr(exc, 'status', None)
        if http_status == 403:
            failure = ('blocked-coordinator-forbidden', 'coordinator-http-403')
        elif http_status == 429:
            failure = ('blocked-coordinator-rate-limited', 'coordinator-http-429')
        elif http_status == 404:
            failure = ('blocked-coordinator-state-missing', 'coordinator-http-404')
        elif isinstance(http_status, int) and http_status != 200:
            failure = ('blocked-coordinator-http', f'coordinator-http-{http_status}')
        elif http_status == 200:
            failure = ('blocked-coordinator-invalid-response',
                       'coordinator-contents-invalid')
        else:
            failure = ('blocked-coordinator-read', 'coordinator-read-error')
    except Exception:  # noqa: BLE001 - transport／未預期例外 fail closed，唔外洩 message
        failure = ('blocked-coordinator-transport', 'coordinator-transport-error')
    finally:
        _restore_transport(previous_opener)
        report['coordinatorOps'] = _op_count(contents)
    if failure is not None:
        return _finish(report, failure[0], reason=failure[1])
    if state is None:
        return _finish(report, 'blocked-coordinator-state-missing',
                       reason='coordinator-state-404')
    report['readSuccess'] = {
        'stateRead': True,
        'statePresent': True,
        'contentsValid': True,
        'jsonValid': True,
        'schemaValid': True,
        'schemaVersion': _safe_int(state.get('schemaVersion')),
    }
    report['notProven'] = list(COORDINATOR_CHECK_NOT_PROVEN)
    return _finish(report, COORDINATOR_CHECK_OK, reason='coordinator-state-readable')


def run_diagnostic(*, execute=False, check_coordinator=False, env=None, fetch=None,
                   batch_mod=None, now=None, contents_factory=None, selector=None):
    """執行一次 diagnostic；回傳 sanitized report dict（含 `exitCode`）。"""
    env = os.environ if env is None else env
    fetch = fetch_mod if fetch is None else fetch
    batch_mod = batch_mod or _batch_utils
    now_fn = now or time.time
    selector = selector or candidate_mod.select_candidate

    if check_coordinator and execute:
        report = _base_report('conflicting-modes')
        return _finish(report, 'blocked-conflicting-modes',
                       reason='check-coordinator-and-execute-conflict')
    if check_coordinator:
        report = _base_report(COORDINATOR_CHECK_MODE)
        return _run_coordinator_check(report, env, now_fn, contents_factory)

    report = _base_report('execute' if execute else 'preview')

    if not execute:
        return _run_preview(report, fetch, batch_mod, selector)

    report['planned'] = _planned()
    report['localStage'] = _local_stage_info(batch_mod, fetch)

    # ---- preflight（全部喺任何 provider／coordinator mutation 之前）----
    run_id = str(env.get('GITHUB_RUN_ID') or '')
    run_attempt = str(env.get('GITHUB_RUN_ATTEMPT') or '')
    if not RUN_ID_RE.match(run_id) or not RUN_ID_RE.match(run_attempt):
        return _finish(report, 'blocked-run-identity', reason='missing-run-identity')

    cred_state = _credential_state(fetch)
    if cred_state != 'full':
        return _finish(report, 'blocked-credentials',
                       reason=f'provider-credentials-{cred_state[:16]}')

    if str(env.get('AIRCON_BIGGO_TEST_MODE') or '') == '1':
        return _finish(report, 'blocked-test-mode', reason='test-mode-forbids-network')

    try:
        selection, info = selector()
    except Exception:  # noqa: BLE001
        return _finish(report, 'blocked-candidate-selection',
                       reason='candidate-selection-error')
    if selection is None:
        info = info if isinstance(info, dict) else {}
        report['selection'] = _selection_view(info)
        return _finish(report, 'local-skipped',
                       reason=str(info.get('reason') or 'no-active-eligible-model'))
    report['candidate'] = _candidate_view(selection)

    try:
        cycle_id, stage_no, queued = _resolve_local_stage(batch_mod, fetch)
    except DiagnosticBlocked as blocked:
        return _finish(report, blocked.status, reason=blocked.reason)

    cfg_status = coord_mod.config_status(env)
    if not cfg_status['configured']:
        return _finish(report, 'blocked-coordinator-config',
                       reason=str(cfg_status.get('reason') or 'coordinator-not-configured'))
    try:
        config = coord_mod.CoordinatorConfig.from_env(env)
    except coord_mod.CoordinatorConfigError:
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-config-invalid')
    if config.provider_quota_limit is not None or config.provider_window_end is not None:
        return _finish(report, 'blocked-quota-window-unsupported',
                       reason='quota-window-accounting-unsupported')

    owner = f'aircon-diag-{run_id}-{run_attempt}'
    attempt_id = f'{run_id}-{run_attempt}'
    if contents_factory is None:
        def _production_contents(cfg, now_fn_):
            base_contents = coord_mod.GitHubContentsClient(cfg, now=now_fn_)
            capped = _CappedApi(base_contents._default_api, MAX_COORDINATOR_REQUESTS)
            return coord_mod.GitHubContentsClient(cfg, now=now_fn_, api=capped)

        contents_factory = _production_contents
    try:
        contents = contents_factory(config, now_fn)
    except Exception:  # noqa: BLE001
        return _finish(report, 'blocked-coordinator-config',
                       reason='coordinator-client-error')
    client = coord_mod.CoordinatorClient(contents, owner, now=now_fn)
    report['coordinatorOps'] = _op_count(contents)

    # ---- remote state 檢驗（read-only；唔一致即 blocked，冇 write）----
    try:
        state, _sha = contents.read_state()
    except Exception:  # noqa: BLE001 - read 失敗即 blocked，唔外洩 message
        return _finish(report, 'blocked-coordinator-read', reason='coordinator-read-error')
    report['coordinatorOps'] = _op_count(contents)
    if state is not None:
        report['remoteStage'] = _safe_int(state.get('stage'))
        report['cycleStageMatches'] = (state.get('cycleId') == cycle_id
                                       and state.get('stage') == stage_no)
        report['reservation'] = _reservation_view(state)
    verdict = _evaluate_remote(state, client, cycle_id, stage_no, owner,
                               int(now_fn()), queued)
    if verdict:
        return _finish(report, verdict, reason=verdict[len('blocked-'):])

    # ---- CAS reservation：lease ＋ reserved upper bound ＋ marker 同一寫入 ----
    marker = _reservation_marker(owner, attempt_id, cycle_id, stage_no, now_fn())
    reserved_state, error = _reserve(contents, client, cycle_id, stage_no, owner,
                                     queued, marker, now_fn)
    report['coordinatorOps'] = _op_count(contents)
    if error:
        if error == 'uncertain-reservation':
            return _finish(report, 'uncertain-reservation',
                           reason='reservation-write-ambiguous',
                           uncertainty='reservation-may-or-may-not-be-persisted')
        return _finish(report, error, reason=error[len('blocked-'):])
    client.state = reserved_state
    report['reservedUpperBound'] = dict(RESERVED)
    report['reservation'] = _reservation_view(reserved_state)
    report['providerPhaseEntered'] = True

    # ---- provider 階段：有界、單次 attempt、每 phase 前核實 lease ----
    # 保留診斷前已存在嘅 limiter／abort／opener，完成、provider 例外或
    # SystemExit／termination 都由 finally 原樣還原（唔可以清掉呼叫方 hooks）。
    limiter = RequestLimiter(search_cap=1, token_cap=1)
    abort = _LeaseAbort(contents, owner, cycle_id, stage_no, now_fn)
    previous_limiter = _preexisting_limiter(fetch)
    previous_abort = _preexisting_abort_check(fetch)
    previous_opener = _harden_transport()
    before = _stat(getattr(fetch, 'REQUEST_STATS', {}))
    try:
        fetch.set_request_limiter(limiter)
        fetch.set_abort_check(abort.check)
        with contextlib.redirect_stdout(io.StringIO()):
            raw_smoke = fetch.run_smoke([selection])
    except Exception:  # noqa: BLE001 - 例外只當 provider uncertainty，無 message 外洩
        raw_smoke = {'reason': 'transport', 'ok': False, 'reachable': False,
                     'responseValid': False, 'priceAvailable': False,
                     'token': 0, 'search': 0}
    finally:
        _restore_transport(previous_opener)
        fetch.set_request_limiter(previous_limiter)
        fetch.set_abort_check(previous_abort)
    after = _stat(getattr(fetch, 'REQUEST_STATS', {}))
    delta = _delta(after, before)
    # Diagnostic 邊界唔接受 legacy bool：任何非 dict 結果一律 invalid（非零）。
    ok, smoke_reason, smoke_info = validate_smoke_result(raw_smoke,
                                                        allow_legacy_bool=False)
    smoke_info = smoke_info if isinstance(smoke_info, dict) else {}

    auth_fact = getattr(fetch, 'LAST_AUTH_FACT', None)
    auth_fact = auth_fact if auth_fact in AUTH_FACTS else 'unknown'
    report['actual'] = dict(delta)
    report['providersCalled'] = bool(delta['token'] + delta['search'])
    report['authFact'] = auth_fact
    report['connectivity'] = {'reachable': bool(smoke_info.get('reachable'))}
    report['response'] = {'valid': bool(smoke_info.get('responseValid'))}
    report['price'] = {'available': bool(smoke_info.get('priceAvailable'))}

    # 實際 response／cooldown 只經既有 helper；`requestAttempts` 唔再加。
    evidence = _safe_evidence(fetch)
    if evidence:
        client.record_response(evidence['status'])
        client.apply_cooldown(evidence['status'],
                              {'Retry-After': evidence.get('retryAfter')})

    # ---- persist sanitized outcome（marker 內 actual facts）----
    final_reason = smoke_reason if ok or smoke_reason else 'smoke-invalid-result'
    marker = client.state.get(DIAGNOSTIC_FIELD)
    if isinstance(marker, dict):
        marker.update({'state': 'completed', 'completedAt': int(now_fn()),
                       'actual': dict(delta),
                       'reason': str(final_reason)[:64],
                       'authFact': auth_fact,
                       'reachable': bool(smoke_info.get('reachable')),
                       'responseValid': bool(smoke_info.get('responseValid')),
                       'priceAvailable': bool(smoke_info.get('priceAvailable'))})

    # ---- release 只今次 lease（preserve business status／unrelated fields）----
    error = _finalize(contents, owner, cycle_id, stage_no, client.state, now_fn)
    report['coordinatorOps'] = _op_count(contents)
    if error:
        return _finish(report, error, reason=error[len('uncertain-'):],
                       uncertainty='lease-release-or-outcome-persist-uncertain')

    report['reservation'] = _reservation_view(client.state)
    if ok and smoke_reason == 'priced':
        return _finish(report, 'healthy-with-price', reason='priced')
    if ok and smoke_reason == 'no-price':
        return _finish(report, 'healthy-without-price', reason='no-price')
    if smoke_reason == 'aborted':
        return _finish(report, 'uncertain-lease-not-confirmed',
                       reason='aborted-before-next-phase')
    return _finish(report, _smoke_status(smoke_reason), reason=str(smoke_reason)[:64])


def _op_count(contents):
    api = getattr(contents, '_api', None)
    calls = getattr(api, 'calls', None)
    if isinstance(calls, bool):
        return 0
    if isinstance(calls, int):
        return calls
    if isinstance(calls, list):
        return len(calls)
    return 0


def render_summary(report):
    """Readable、sanitized summary（只讀 report 內 allowlisted 欄位）。"""
    lines = [f"### BigGo manual diagnostic — {_safe_label(report.get('mode'))}",
             f"- status: `{_safe_label(report.get('status'))}`"]
    if report.get('reason'):
        lines.append(f"- reason: `{_safe_label(report.get('reason'))}`")
    lines.append(f"- providersCalled: {str(bool(report.get('providersCalled'))).lower()}")
    if report.get('readOnly'):
        lines.append('- readOnly: true')
    candidate = report.get('candidate') or {}
    if candidate.get('model'):
        lines.append(f"- candidate: `{candidate['model']}` / "
                     f"`{candidate.get('brand') or 'UNKNOWN'}`")
    planned = report.get('planned') or {}
    if planned:
        lines.append('- planned: token<={}, search<={}, coordinatorOps<={}, '
                     'smokeTimeout={}s'.format(
                         _safe_int(planned.get('tokenLimit')),
                         _safe_int(planned.get('searchLimit')),
                         _safe_int(planned.get('coordinatorOpsCap')),
                         _safe_int(planned.get('smokeTimeoutSeconds'))))
    if report.get('reservedUpperBound'):
        bound = report['reservedUpperBound']
        lines.append(f"- reservedUpperBound: token={_safe_int(bound.get('token'))}, "
                     f"search={_safe_int(bound.get('search'))}")
    actual = report.get('actual') or {}
    lines.append(f"- actual: token={_safe_int(actual.get('token'))}, "
                 f"search={_safe_int(actual.get('search'))}")
    if report.get('connectivity') is not None:
        lines.append(f"- reachable: {str(bool(report['connectivity'].get('reachable'))).lower()}")
    if report.get('response') is not None:
        lines.append(f"- responseValid: "
                     f"{str(bool(report['response'].get('valid'))).lower()}")
    if report.get('price') is not None:
        lines.append(f"- priceAvailable: "
                     f"{str(bool(report['price'].get('available'))).lower()}")
    lines.append(f"- coordinatorOps: {_safe_int(report.get('coordinatorOps'))}")
    identity = report.get('identity')
    if isinstance(identity, dict) and identity.get('digest'):
        lines.append("- coordinatorIdentity: `{}` v{} `{}`".format(
            _safe_label(identity.get('representation')),
            _safe_int(identity.get('version')),
            _safe_label(identity.get('digest'))))
    read_success = report.get('readSuccess')
    if isinstance(read_success, dict):
        lines.append('- readSuccess: stateRead={}, statePresent={}, contentsValid={}, '
                     'jsonValid={}, schemaValid={}, schemaVersion={}'.format(
                         str(bool(read_success.get('stateRead'))).lower(),
                         str(bool(read_success.get('statePresent'))).lower(),
                         str(bool(read_success.get('contentsValid'))).lower(),
                         str(bool(read_success.get('jsonValid'))).lower(),
                         str(bool(read_success.get('schemaValid'))).lower(),
                         _safe_int(read_success.get('schemaVersion'))))
    if report.get('mode') == COORDINATOR_CHECK_MODE:
        lines.append('- note: read-only reachability only; does NOT prove write '
                     'permission, token equality, BigGo connectivity or daily '
                     'stage compatibility')
    if report.get('notProven'):
        lines.append('- notProven: ' + ', '.join(
            _safe_label(item) for item in report.get('notProven')
            if isinstance(item, str)))
    if report.get('uncertainty'):
        lines.append(f"- uncertainty: `{_safe_label(report.get('uncertainty'))}`")
    return '\n'.join(lines) + '\n'


def _write_text(path, text):
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(directory, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(
        description='Bounded BigGo diagnostic；預設 preview（零 coordinator／零 provider）')
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--execute', action='store_true',
                            help='執行 bounded live probe（1 token + 1 search）；'
                                 '需要完整憑證／coordinator／run identity')
    mode_group.add_argument('--check-coordinator', dest='check_coordinator',
                            action='store_true',
                            help='只讀 1 次共享 coordinator state GET；零 token／search／'
                                 'PUT，唔需要 BigGo 憑證或本地 active stage')
    parser.add_argument('--json-out', default=None, help='寫出 sanitized JSON report')
    parser.add_argument('--summary-out', default=None, help='寫出 readable summary')
    args = parser.parse_args(argv)
    mode = (COORDINATOR_CHECK_MODE if args.check_coordinator
            else 'execute' if args.execute else 'preview')
    try:
        report = run_diagnostic(execute=args.execute,
                                check_coordinator=args.check_coordinator)
    except Exception:  # noqa: BLE001 - 未預期例外一律 fail closed，唔輸出 exception message
        report = _base_report(mode)
        report['status'] = 'uncertain-exception'
        report['reason'] = 'unexpected-internal-error'
        report['uncertainty'] = 'exception-left-explicit-uncertain-state'
        report['exitCode'] = 1
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        _write_text(args.json_out, payload + '\n')
    if args.summary_out:
        _write_text(args.summary_out, render_summary(report))
    print(payload)
    return _safe_int(report.get('exitCode'), 2)


if __name__ == '__main__':
    sys.exit(main())
