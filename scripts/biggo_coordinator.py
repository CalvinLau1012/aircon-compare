#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo 共享憑證協調器（approved design C）。

兩個部署 writer（GitHub Actions、私人自架）可能同時有 BigGo 憑證。呢個模組提供
一個 generic、只用環境／Secrets 配置嘅 coordinator client：

  - 狀態放喺 coordinator repo 嘅 JSON 檔（路徑由環境提供；**唔可以硬編碼私人 repo 識別**）；
  - 用 GitHub Contents API 既有 blob SHA 做 compare-and-swap（CAS）；同一 cycle+stage
    只有一個 winner；
  - lease 45 分鐘、每 5 分鐘 renew；expired 可以 takeover；
  - completed cycle+stage 係 idempotent；任何已發出呼叫但 snapshot 發布唔確定 →
    needs_review，之後禁止自動重跑；
  - 本地 hard cap = 1 smoke + 2 × queued models；token 同 search 分開計；
  - provider quota／window 未有事實證據前一律 None；若配置 factual quota，自動上限 = 80%；
  - 只捕捉安全 rate-limit 證據（status counts、Retry-After、allowlist header），
    唔會記錄 auth／response body。

本模組唔會自己發 BigGo API 呼叫；所有 HTTP 依賴可注入（fake 可完全離線測試）。
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from biggo_canonical import canonical_json_bytes, sha256_id, sha256_json  # noqa: E402

STATE_PATH_DEFAULT = 'coordinator/biggo-state.json'
SNAPSHOT_DIR_DEFAULT = 'coordinator/snapshots'
BRANCH_DEFAULT = 'main'

LEASE_SECONDS = 45 * 60
RENEW_INTERVAL_SECONDS = 5 * 60
# 項目自身 fallback 冷卻（48 小時）。呢個係 aircon-compare 嘅保守政策，
# **唔係** provider 公布嘅 quota window；provider quota／window 仍屬 UNKNOWN。
PROJECT_COOLDOWN_SECONDS = 48 * 3600

STATE_SCHEMA_VERSION = 1
SNAPSHOT_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
# P0 stage bundle（transaction）schema：新 runner 只用 v2；舊 v1 只可被拒絕自動 apply。
BUNDLE_MANIFEST_SCHEMA_VERSION = 2
STAGE_RESULT_SCHEMA_VERSION = 1

STATUSES = ('idle', 'active', 'completed', 'needs_review')

# 只准捕捉呢啲 rate-limit header 名（其餘一律丟棄，避免 auth／cookie 落入證據）
RATE_LIMIT_HEADER_ALLOWLIST = frozenset({
    'retry-after',
    'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset',
    'ratelimit-limit', 'ratelimit-remaining', 'ratelimit-reset',
    'x-rate-limit-limit', 'x-rate-limit-remaining', 'x-rate-limit-reset',
})

SNAPSHOT_HASH_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
CYCLE_RE = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}:[0-9]+/[0-9]+$')


class CoordinatorError(RuntimeError):
    pass


class CoordinatorConfigError(CoordinatorError):
    pass


class CoordinatorStateError(CoordinatorError):
    pass


class CoordinatorApiError(CoordinatorError):
    pass


class CASConflict(CoordinatorError):
    """GitHub Contents API 409：另一個 writer 先一步更新（CAS 輸）。"""


class LeaseLostError(CoordinatorError):
    pass


class SnapshotImportError(CoordinatorError):
    pass


class LegacyBundleError(SnapshotImportError):
    """manifest schemaVersion 1（snapshot-only）唔可以自動 apply。"""


class BundleValidationError(CoordinatorError):
    """stage-result / bundle binding 唔合格。"""


NORMAL_CYCLE_RE = re.compile(
    r'^(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2}):(?P<stage>[0-9]+)/(?P<total>[0-9]+)$')
FORCE_CYCLE_RE = re.compile(r'^force-(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})$')


def parse_cycle(cycle_id):
    """解析 cycleId（normal／force）；唔合格式回 None。"""
    if not isinstance(cycle_id, str):
        return None
    m = NORMAL_CYCLE_RE.match(cycle_id)
    if m:
        return {'kind': 'normal', 'date': m.group('date'),
                'stage': int(m.group('stage')), 'total': int(m.group('total'))}
    m = FORCE_CYCLE_RE.match(cycle_id)
    if m:
        return {'kind': 'force', 'date': m.group('date'), 'stage': 1, 'total': 1}
    return None


def utc_stamp(now=None):
    ts = now if now is not None else time.time()
    if isinstance(ts, datetime):
        ts = ts.timestamp()
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


# ---------------------------------------------------------------- safe evidence


def safe_rate_limit_evidence(headers):
    """只保留 allowlist 內嘅 rate-limit header（名細寫、值截短）；其餘全部丟棄。"""
    if not headers:
        return {}
    out = {}
    for name, value in headers.items():
        key = str(name).strip().lower()
        if key not in RATE_LIMIT_HEADER_ALLOWLIST:
            continue
        out[key] = str(value)[:128]
    return out


def parse_retry_after(value, now=None):
    """Retry-After → 秒數 int 或 None。

    支持 RFC 9110 兩種形式：delta-seconds（純數字）同 HTTP-date。
    HTTP-date 會用 `now`（Unix 秒）計剩餘秒數；唔會推斷或聲稱 provider 嘅
    quota reset window——只 honour 來源實際提供嘅 reset 資訊。
    無法解析一律回 None（交由項目 fallback 冷卻，唔猜）。
    """
    if value is None:
        return None
    text = str(value).strip()
    if re.match(r'^[0-9]+$', text):
        return int(text)
    from email.utils import parsedate_to_datetime
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    base = now if now is not None else time.time()
    try:
        return max(0, int(target.timestamp() - float(base)))
    except (OverflowError, OSError, ValueError):
        return None


def cooldown_until_for(status, headers, now, base_project_cooldown=PROJECT_COOLDOWN_SECONDS):
    """403／429 → (cooldownUntil, evidence, basis)。

    有可信 Retry-After（delta-seconds 或 HTTP-date）就 honour；冇就 fallback 項目
    自身 48 小時冷卻。basis 只會係 'retry-after' 或 'project-48h'；48h 係 aircon-compare
    嘅保守政策，唔係 provider 公布嘅 quota window（provider quota／window 仍 UNKNOWN）。
    """
    if status not in (403, 429):
        return 0, {}, None
    evidence = safe_rate_limit_evidence(headers)
    retry_after = parse_retry_after(evidence.get('retry-after'), now=now)
    if retry_after is not None:
        return int(now) + retry_after, evidence, 'retry-after'
    return int(now) + base_project_cooldown, evidence, 'project-48h'


def compute_local_cap(queued_models):
    """Per-stage local hard cap：1 smoke + 2 × queued models（search requests）。"""
    if isinstance(queued_models, int):
        count = queued_models
    else:
        count = len(list(queued_models))
    return 1 + 2 * max(0, count)


def compute_quota_cap(provider_quota_limit):
    """Factual provider quota 嘅 80% 數字（quota 未確認 → None）。

    注意：呢個只係 stage-local 數字；因為冇共享、持久、可核對的 provider window
    usage ledger，本模組唔可以聲稱已對 provider window enforce。Runner 對有
    providerQuotaLimit／providerWindowEnd 配置的路徑必須在呼叫 budget 前 fail-closed
    （status: blocked-quota-window-unsupported），不得只靠呢個 stage-local 80%。
    """
    if provider_quota_limit is None:
        return None
    if not isinstance(provider_quota_limit, int) or isinstance(provider_quota_limit, bool) \
            or provider_quota_limit <= 0:
        raise CoordinatorConfigError('providerQuotaLimit 必須係正整數')
    return int(provider_quota_limit * 80 / 100)


# ---------------------------------------------------------------- state schema


def initial_state(cycle_id, stage, owner, now, provider_quota_limit=None,
                  provider_window_end=None):
    return {
        'schemaVersion': STATE_SCHEMA_VERSION,
        'cycleId': cycle_id,
        'stage': int(stage),
        'status': 'active',
        'leaseOwner': owner,
        'leaseAcquiredAt': int(now),
        'leaseRenewedAt': int(now),
        'leaseExpiresAt': int(now) + LEASE_SECONDS,
        'requestAttempts': {'token': 0, 'search': 0},
        'responseStatusCounts': {},
        'cooldownUntil': 0,
        'snapshotHash': None,
        'snapshotPath': None,
        'stageResultHash': None,
        'stageResultPath': None,
        'bundleHash': None,
        'callsMayHaveStarted': False,
        'intentAt': None,
        'providerQuotaLimit': provider_quota_limit,
        'providerWindowEnd': provider_window_end,
        'lastReviewReason': None,
        'updatedAt': int(now),
    }


def validate_state(state):
    """嚴格驗證 coordinator state；任何偏差 raise CoordinatorStateError。"""
    if not isinstance(state, dict):
        raise CoordinatorStateError(f'state 必須係 object（got {type(state).__name__}）')
    required = ('schemaVersion', 'cycleId', 'stage', 'status', 'leaseOwner',
                'requestAttempts', 'responseStatusCounts', 'cooldownUntil',
                'snapshotHash', 'providerQuotaLimit', 'providerWindowEnd')
    missing = [f for f in required if f not in state]
    if missing:
        raise CoordinatorStateError(f'state 缺少欄位：{missing}')
    if state['schemaVersion'] != STATE_SCHEMA_VERSION:
        raise CoordinatorStateError('state schemaVersion 唔支援')
    if not isinstance(state['cycleId'], str) or not state['cycleId']:
        raise CoordinatorStateError('cycleId 必須係非空字串')
    if isinstance(state['stage'], bool) or not isinstance(state['stage'], int) or state['stage'] < 1:
        raise CoordinatorStateError('stage 必須係 >=1 整數')
    if state['status'] not in STATUSES:
        raise CoordinatorStateError(f'status 唔合法：{state["status"]!r}')
    if state['leaseOwner'] is not None and not isinstance(state['leaseOwner'], str):
        raise CoordinatorStateError('leaseOwner 必須係字串或 null')
    attempts = state['requestAttempts']
    if not isinstance(attempts, dict) or set(attempts) != {'token', 'search'}:
        raise CoordinatorStateError('requestAttempts 必須係 {token, search}')
    for key in ('token', 'search'):
        value = attempts[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError(f'requestAttempts.{key} 必須係非負整數')
    counts = state['responseStatusCounts']
    if not isinstance(counts, dict):
        raise CoordinatorStateError('responseStatusCounts 必須係 object')
    for key, value in counts.items():
        if not re.match(r'^[0-9]{3}$', str(key)):
            raise CoordinatorStateError(f'responseStatusCounts key 必須係 HTTP status：{key!r}')
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError(f'responseStatusCounts[{key}] 必須係非負整數')
    for key in ('leaseAcquiredAt', 'leaseRenewedAt', 'leaseExpiresAt',
                'cooldownUntil', 'updatedAt'):
        value = state.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError(f'{key} 必須係非負整數')
    if state['snapshotHash'] is not None and \
            not (isinstance(state['snapshotHash'], str)
                 and SNAPSHOT_HASH_RE.match(state['snapshotHash'])):
        raise CoordinatorStateError('snapshotHash 必須係 sha256 或 null')
    for field in ('stageResultHash', 'bundleHash'):
        value = state.get(field)
        if value is not None and not (isinstance(value, str)
                                      and SNAPSHOT_HASH_RE.match(value)):
            raise CoordinatorStateError(f'{field} 必須係 sha256 或 null')
    if not isinstance(state.get('callsMayHaveStarted', False), bool):
        raise CoordinatorStateError('callsMayHaveStarted 必須係 bool')
    intent_at = state.get('intentAt')
    if intent_at is not None and (isinstance(intent_at, bool)
                                  or not isinstance(intent_at, int) or intent_at < 0):
        raise CoordinatorStateError('intentAt 必須係非負整數或 null')
    if state['providerQuotaLimit'] is not None and \
            (isinstance(state['providerQuotaLimit'], bool)
             or not isinstance(state['providerQuotaLimit'], int)
             or state['providerQuotaLimit'] <= 0):
        raise CoordinatorStateError('providerQuotaLimit 必須係正整數或 null')
    if state['providerWindowEnd'] is not None and \
            (isinstance(state['providerWindowEnd'], bool)
             or not isinstance(state['providerWindowEnd'], int)
             or state['providerWindowEnd'] < 0):
        raise CoordinatorStateError('providerWindowEnd 必須係非負整數或 null')
    return state


# ---------------------------------------------------------------- snapshots


def validate_price_snapshot(snapshot, *, allow_empty=False):
    """Price snapshot schema：{model: {price, merchants, url, updated}}；唔合格 raise。

    `allow_empty=True` 只用於 base snapshot（可能未有舊價）。
    """
    if not isinstance(snapshot, dict) or (not snapshot and not allow_empty):
        raise CoordinatorStateError('price snapshot 必須係非空 object')
    date_re = re.compile(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
    for model, entry in snapshot.items():
        if not isinstance(model, str) or not model.strip():
            raise CoordinatorStateError('price snapshot model key 必須係非空字串')
        if not isinstance(entry, dict):
            raise CoordinatorStateError(f'{model} entry 必須係 object')
        price = entry.get('price')
        if not isinstance(price, str) or not price.startswith('$'):
            raise CoordinatorStateError(f'{model} price 必須係 $ 開頭')
        merchants = entry.get('merchants')
        if isinstance(merchants, bool) or not isinstance(merchants, int) or merchants < 1:
            raise CoordinatorStateError(f'{model} merchants 必須係 >=1 整數')
        url = entry.get('url')
        if not isinstance(url, str) or not url.startswith('https://'):
            raise CoordinatorStateError(f'{model} url 必須係 https')
        if not date_re.match(str(entry.get('updated', ''))):
            raise CoordinatorStateError(f'{model} updated 必須係 YYYY-MM-DD')
    return snapshot


def _is_sha(value):
    return isinstance(value, str) and bool(SNAPSHOT_HASH_RE.match(value))


def validate_stage_result(stage_result, base_snapshot, new_snapshot, *, cycle_id=None,
                          stage=None, mode=None):
    """嚴格驗證 stage-result bundle 同 base/new snapshot 的 binding。

    驗證重點：
      - schema/version/cycle/stage/mode；
      - base + 本 stage priced outcomes == new（保留舊 stage keys）；
      - clean_miss/net_error 的 entry 同 base 相同（不得擅自改價）；
      - counters、model identity、records；
      - effects 結構、pre/post state hashes、effectsHash。
    """
    if not isinstance(stage_result, dict):
        raise CoordinatorStateError('stage-result 必須係 object')
    if stage_result.get('schemaVersion') != STAGE_RESULT_SCHEMA_VERSION:
        raise CoordinatorStateError('stage-result schemaVersion 唔支援')
    required = ('cycleId', 'stage', 'mode', 'generatedAt', 'counters',
                'requestAttempts', 'responseStatusCounts', 'outcomes',
                'blacklistReview', 'effects', 'preState', 'postState', 'effectsHash')
    missing = [f for f in required if f not in stage_result]
    if missing:
        raise CoordinatorStateError(f'stage-result 缺少欄位：{missing}')
    if cycle_id is not None and stage_result['cycleId'] != cycle_id:
        raise CoordinatorStateError('stage-result cycleId 唔 match')
    if stage is not None and stage_result['stage'] != stage:
        raise CoordinatorStateError('stage-result stage 唔 match')
    if mode is not None and stage_result['mode'] != mode:
        raise CoordinatorStateError('stage-result mode 唔 match')
    if not isinstance(stage_result['generatedAt'], str) or not stage_result['generatedAt']:
        raise CoordinatorStateError('generatedAt 必須係非空字串')

    counters = stage_result['counters']
    if not isinstance(counters, dict) or set(counters) != {'got', 'cleanMiss', 'netErrors'}:
        raise CoordinatorStateError('counters 必須係 {got, cleanMiss, netErrors}')
    for key, value in counters.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError(f'counters.{key} 必須係非負整數')
    attempts = stage_result['requestAttempts']
    if not isinstance(attempts, dict) or set(attempts) != {'token', 'search'}:
        raise CoordinatorStateError('requestAttempts 必須係 {token, search}')
    for key, value in attempts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError(f'requestAttempts.{key} 必須係非負整數')
    counts = stage_result['responseStatusCounts']
    if not isinstance(counts, dict):
        raise CoordinatorStateError('responseStatusCounts 必須係 object')
    for key, value in counts.items():
        if not re.match(r'^[0-9]{3}$', str(key)) or isinstance(value, bool) \
                or not isinstance(value, int) or value < 0:
            raise CoordinatorStateError('responseStatusCounts 唔合法')

    outcomes = stage_result['outcomes']
    if not isinstance(outcomes, list):
        raise CoordinatorStateError('outcomes 必須係 array')
    priced, clean_miss, net_err = {}, set(), set()
    for item in outcomes:
        if not isinstance(item, dict):
            raise CoordinatorStateError('outcome 必須係 object')
        model = item.get('model')
        outcome = item.get('outcome')
        if not isinstance(model, str) or not model.strip():
            raise CoordinatorStateError('outcome model 必須係非空字串')
        if outcome not in ('priced', 'clean_miss', 'net_error'):
            raise CoordinatorStateError(f'outcome 唔合法：{outcome!r}')
        if model in priced or model in clean_miss or model in net_err:
            raise CoordinatorStateError(f'outcome model 重複：{model}')
        if outcome == 'priced':
            price = item.get('price')
            if not isinstance(price, dict):
                raise CoordinatorStateError(f'{model} priced 必須有 price object')
            priced[model] = price
        elif outcome == 'clean_miss':
            clean_miss.add(model)
        else:
            net_err.add(model)
    if len(priced) != counters['got']:
        raise CoordinatorStateError('counters.got 同 priced outcomes 唔一致')
    if len(clean_miss) != counters['cleanMiss']:
        raise CoordinatorStateError('counters.cleanMiss 同 clean_miss outcomes 唔一致')
    if len(net_err) != counters['netErrors']:
        raise CoordinatorStateError('counters.netErrors 同 net_error outcomes 唔一致')

    if not isinstance(base_snapshot, dict) or not isinstance(new_snapshot, dict):
        raise CoordinatorStateError('base/new snapshot 必須係 object')
    validate_price_snapshot(base_snapshot, allow_empty=True)
    validate_price_snapshot(new_snapshot)
    expected = dict(base_snapshot)
    for model, price in priced.items():
        expected[model] = price
    if canonical_json_bytes(expected) != canonical_json_bytes(new_snapshot):
        raise CoordinatorStateError('base snapshot + stage outcomes 唔等於 new snapshot')
    for model in list(clean_miss) + list(net_err):
        if base_snapshot.get(model) != new_snapshot.get(model):
            raise CoordinatorStateError(f'{model} clean_miss/net_error 唔可以改 snapshot entry')
    if canonical_json_bytes(base_snapshot) == canonical_json_bytes(new_snapshot) \
            and counters['got'] == 0 and counters['cleanMiss'] == 0 and counters['netErrors'] == 0:
        raise CoordinatorStateError('stage-result 冇任何 outcome')

    review = stage_result['blacklistReview']
    if not isinstance(review, dict):
        raise CoordinatorStateError('blacklistReview 必須係 object')
    reviewed = review.get('reviewed', [])
    if not isinstance(reviewed, list):
        raise CoordinatorStateError('blacklistReview.reviewed 必須係 array')
    for item in reviewed:
        if not isinstance(item, dict) or not isinstance(item.get('key'), str) \
                or item.get('result') not in ('revived', 'confirmed', 'net_error'):
            raise CoordinatorStateError('blacklistReview.reviewed entry 唔合法')

    effects = stage_result['effects']
    if not isinstance(effects, dict) or set(effects) != {
            'trackingUpserts', 'trackingRemovals',
            'blacklistUpserts', 'blacklistRemovals'}:
        raise CoordinatorStateError('effects 結構唔合法')
    for key in ('trackingUpserts', 'blacklistUpserts'):
        value = effects[key]
        if not isinstance(value, dict) or any('|' not in k for k in value):
            raise CoordinatorStateError(f'effects.{key} 必須係 canonical key object')
    for key in ('trackingRemovals', 'blacklistRemovals'):
        value = effects[key]
        if not isinstance(value, list) or any(('|' not in str(k)) for k in value):
            raise CoordinatorStateError(f'effects.{key} 必須係 canonical key array')
    pre_state = stage_result['preState']
    post_state = stage_result['postState']
    if not isinstance(pre_state, dict) or not isinstance(post_state, dict) \
            or not _is_sha(pre_state.get('trackingHash')) \
            or not _is_sha(pre_state.get('blacklistHash')) \
            or not _is_sha(post_state.get('trackingHash')) \
            or not _is_sha(post_state.get('blacklistHash')):
        raise CoordinatorStateError('preState/postState hash 唔合法')
    if stage_result['effectsHash'] != sha256_id(canonical_json_bytes(effects)):
        raise CoordinatorStateError('effectsHash 同 effects 唔一致')
    return stage_result


def cycle_slug(cycle_id):
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', str(cycle_id)).strip('-')
    if not slug:
        raise CoordinatorStateError('cycleId 唔可以正規化成空 slug')
    return slug


# ---------------------------------------------------------------- env config


class CoordinatorConfig:
    """只由環境／Secrets 提供；**冇任何私人 repo 識別預設值**。"""

    def __init__(self, repo, token, *, branch=BRANCH_DEFAULT, state_path=STATE_PATH_DEFAULT,
                 snapshot_dir=SNAPSHOT_DIR_DEFAULT, provider_quota_limit=None,
                 provider_window_end=None):
        self.repo = repo
        self.token = token
        self.branch = branch
        self.state_path = state_path
        self.snapshot_dir = snapshot_dir
        self.provider_quota_limit = provider_quota_limit
        self.provider_window_end = provider_window_end

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        repo = (env.get('AIRCON_BIGGO_COORDINATOR_REPO') or '').strip()
        token = env.get('AIRCON_BIGGO_COORDINATOR_TOKEN') or ''
        if not repo and not token:
            return None
        if not repo or not token:
            raise CoordinatorConfigError(
                'coordinator 配置不完整（REPO／TOKEN 必須同時設定），拒絕降級')
        if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo):
            raise CoordinatorConfigError('coordinator repo 格式錯誤')
        quota = (env.get('AIRCON_BIGGO_PROVIDER_QUOTA_LIMIT') or '').strip()
        window_end = (env.get('AIRCON_BIGGO_PROVIDER_WINDOW_END') or '').strip()
        return cls(
            repo, token,
            branch=(env.get('AIRCON_BIGGO_COORDINATOR_BRANCH') or BRANCH_DEFAULT).strip(),
            state_path=(env.get('AIRCON_BIGGO_COORDINATOR_STATE_PATH')
                        or STATE_PATH_DEFAULT).strip(),
            snapshot_dir=(env.get('AIRCON_BIGGO_COORDINATOR_SNAPSHOT_DIR')
                          or SNAPSHOT_DIR_DEFAULT).strip(),
            provider_quota_limit=int(quota) if quota else None,
            provider_window_end=int(window_end) if window_end else None,
        )


def config_status(env=None):
    """回傳唔含秘密嘅配置狀態（俾 workflow 輸出清晰 status，唔會呼叫任何 API）。"""
    env = env if env is not None else os.environ
    repo = (env.get('AIRCON_BIGGO_COORDINATOR_REPO') or '').strip()
    token = env.get('AIRCON_BIGGO_COORDINATOR_TOKEN') or ''
    if not repo and not token:
        return {'configured': False, 'reason': 'coordinator-not-configured'}
    if not repo or not token:
        return {'configured': False, 'reason': 'coordinator-config-incomplete'}
    return {'configured': True, 'reason': None}


# ---------------------------------------------------------------- GitHub Contents API


class GitHubContentsClient:
    """GitHub Contents API 最小 client（blob SHA CAS）；HTTP 可注入 fake。"""

    def __init__(self, config, *, api=None, now=None):
        self.config = config
        self._api = api or self._default_api
        self._now = now or time.time

    def _default_api(self, method, url, body=None, headers=None):
        hdrs = {
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'aircon-compare-biggo-coordinator',
        }
        if self.config.token:
            hdrs['Authorization'] = f'Bearer {self.config.token}'
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _contents_url(self, path):
        quoted = urllib.parse.quote(path, safe='/')
        return f'https://api.github.com/repos/{self.config.repo}/contents/{quoted}'

    def get_file(self, path):
        url = self._contents_url(path) + '?ref=' + urllib.parse.quote(self.config.branch)
        status, raw = self._api('GET', url)
        if status == 404:
            return None, None
        if status != 200:
            raise CoordinatorApiError(f'coordinator GET 失敗（HTTP {status}）')
        try:
            doc = json.loads(raw.decode('utf-8'))
            content = base64.b64decode(doc['content'])
        except (ValueError, KeyError, TypeError) as e:
            raise CoordinatorApiError(f'coordinator GET 回應唔合法：{type(e).__name__}')
        return content, doc.get('sha')

    def put_file(self, path, data, message, sha=None):
        payload = {'message': message, 'content': base64.b64encode(data).decode('ascii'),
                   'branch': self.config.branch}
        if sha:
            payload['sha'] = sha
        status, raw = self._api('PUT', self._contents_url(path),
                                json.dumps(payload).encode('utf-8'),
                                {'Content-Type': 'application/json'})
        if status == 409:
            raise CASConflict('coordinator CAS 409：另一個 writer 先贏')
        if status not in (200, 201):
            raise CoordinatorApiError(f'coordinator PUT 失敗（HTTP {status}）')
        try:
            return json.loads(raw.decode('utf-8'))['content']['sha']
        except (ValueError, KeyError, TypeError) as e:
            raise CoordinatorApiError(f'coordinator PUT 回應唔合法：{type(e).__name__}')

    # ---- state helpers ----
    def read_state(self):
        data, sha = self.get_file(self.config.state_path)
        if data is None:
            return None, None
        try:
            state = json.loads(data.decode('utf-8'))
        except ValueError as e:
            raise CoordinatorStateError(f'coordinator state JSON 損毀：{e}')
        return validate_state(state), sha

    def write_state(self, state, sha):
        validate_state(state)
        return self.put_file(self.config.state_path,
                             canonical_json_bytes(state), 'coordinator: state update', sha=sha)

    # ---- snapshot helpers ----
    def read_snapshot(self, path):
        data, sha = self.get_file(path)
        return data, sha

    def write_snapshot(self, path, data, message):
        return self.put_file(path, data, message)


# ---------------------------------------------------------------- client


class CoordinatorClient:
    """CAS lease／進度客戶端；所有時間比較用 Unix 秒。"""

    def __init__(self, contents, owner, *, now=None,
                 provider_quota_limit=None, provider_window_end=None):
        self._contents = contents
        self.owner = owner
        self._now = now or time.time
        self._provider_quota_limit = provider_quota_limit
        self._provider_window_end = provider_window_end
        self.state = None
        self._sha = None

    def _now_int(self):
        return int(self._now())

    def _takeover_state(self, cycle_id, stage, previous, *, preserve_intent=False,
                        preserve_attempts=False):
        """接管 state的建設。

        - `preserve_intent=True`：只用於同一 cycle/stage 的 expired active takeover（可能
          已發過請求，必須 fail-closed）；會保留 intent 同 per-stage attempts；
        - `preserve_attempts=True`：同一 stage idle 安全重入時保留 attempts 審計（防 budget
          低估）；
        - 完成前一 stage 或切新 cycle 的合法前進：fresh intent=false、attempts=0（避免
          per-stage attempts 被當 provider window 用量）；
        - `cooldownUntil`（時間事實）一律保留；provider quota/window 由 initial_state 帶入。
        """
        taken = initial_state(cycle_id, stage, self.owner, self._now_int(),
                              previous.get('providerQuotaLimit', self._provider_quota_limit),
                              previous.get('providerWindowEnd', self._provider_window_end))
        taken['cooldownUntil'] = max(0, int(previous.get('cooldownUntil', 0) or 0))
        if preserve_intent or preserve_attempts:
            for kind in ('token', 'search'):
                taken['requestAttempts'][kind] = max(
                    0, int(previous.get('requestAttempts', {}).get(kind, 0) or 0))
            counts = previous.get('responseStatusCounts') or {}
            taken['responseStatusCounts'] = {k: int(v) for k, v in counts.items()
                                             if isinstance(v, int)}
        if preserve_intent:
            taken['callsMayHaveStarted'] = bool(previous.get('callsMayHaveStarted'))
            taken['intentAt'] = previous.get('intentAt')
        return taken

    def _guard_regression(self, requested, remote):
        """P0 單調守門：回 True = 拒絕接管（0 PUT）。"""
        req = parse_cycle(requested)
        rem = parse_cycle(remote.get('cycleId'))
        if not req or not rem:
            return False
        if req['kind'] == 'normal' and rem['kind'] == 'normal':
            if rem['date'] > req['date']:
                return True
            if rem['date'] == req['date'] and rem['stage'] > req['stage']:
                return True
        return False

    def _stage_gap_reason(self, requested, remote_cycle):
        """7-stage 週期的 gap 守門；回 reason 字串 = 拒絕，None = 允許。

        只在 remote completed 分支使用（active／idle 已經 blocked-incomplete）：
        - 同日期 normal：下一個合法 stage 必須精確 = remote.stage + 1；
        - 較新日期 normal：remote 上一個 cycle 必須 7/7，且新 cycle 必須由 stage 1 開始；
        - force 係明確人手 intent（completed 後可另行處理），不套 stage gap；
        - 較舊日期交由 `_guard_regression` 回 completed-ahead。
        """
        req = parse_cycle(requested)
        rem = parse_cycle(remote_cycle)
        if not rem:
            return 'unknown-remote-cycle'
        if not req:
            return 'unknown-requested-cycle'
        if rem['kind'] == 'force' or req['kind'] == 'force':
            return None
        if req['date'] == rem['date']:
            if req['stage'] > rem['stage'] + 1:
                return 'stage-skip'
            return None
        if req['date'] > rem['date']:
            if rem['stage'] < rem['total']:
                return 'previous-cycle-incomplete'
            if req['stage'] > 1:
                return 'new-cycle-must-start-at-1'
            return None
        # req.date < rem.date → 舊請求，交由 _guard_regression 回 completed-ahead
        return None

    def acquire(self, cycle_id, stage):
        """CAS 搶／續／接收 lease；回傳 {'result', 'state', 'reason'(?)}。

        結果：winner／winner-intent／renewed／completed-idempotent／needs-review／
        blocked-needs-review／lost／completed-ahead／blocked-incomplete。

        P0 第二輪不變式：
        - 未完成（active／idle）或 needs_review 的 remote stage 唔可以跨越：正常同 force
          一樣；即使 expired、同一 owner，都只可同一 stage 安全重入或 adopt／needs-review；
        - 只有 remote completed（方向單調）或同一 stage idle 無 intent 才可前進；
        - blocked-incomplete／completed-ahead／blocked-needs-review／lost 一律 0 PUT、
          唔改 state。
        """
        for _attempt in range(3):
            state, sha = self._contents.read_state()
            if state is None:
                new = initial_state(cycle_id, stage, self.owner, self._now_int(),
                                    self._provider_quota_limit, self._provider_window_end)
                try:
                    self._sha = self._contents.write_state(new, sha=None)
                    self.state = new
                    return {'result': 'winner', 'state': new}
                except CASConflict:
                    continue
            same_cycle = state['cycleId'] == cycle_id and state['stage'] == stage
            status = state['status']
            if status == 'completed' and same_cycle:
                self.state, self._sha = state, sha
                return {'result': 'completed-idempotent', 'state': state}
            if status == 'needs_review':
                self.state, self._sha = state, sha
                return {'result': 'needs-review' if same_cycle else 'blocked-needs-review',
                        'state': state}
            # 任何 active stage 被其他 owner 有效 lease 持有 → lost（0 PUT；唔算 alert）。
            lease_active = (status == 'active' and state['leaseOwner'] is not None
                            and state['leaseExpiresAt'] > self._now_int())
            if lease_active and state['leaseOwner'] != self.owner:
                self.state, self._sha = state, sha
                return {'result': 'lost', 'state': state}
            if same_cycle and status == 'active':
                if state['leaseOwner'] == self.owner:
                    renewed = dict(state)
                    renewed['leaseRenewedAt'] = self._now_int()
                    renewed['leaseExpiresAt'] = self._now_int() + LEASE_SECONDS
                    try:
                        self._sha = self._contents.write_state(renewed, sha)
                        self.state = renewed
                        # 已有 durable intent 的續接：唔可以當普通 winner 重跑。
                        if renewed.get('callsMayHaveStarted'):
                            return {'result': 'winner-intent', 'state': renewed}
                        return {'result': 'renewed', 'state': renewed}
                    except CASConflict:
                        continue
                # expired other owner：同一 stage 安全重入（冇 intent）或 adopt／needs-review
                taken = self._takeover_state(cycle_id, stage, state, preserve_intent=True)
                try:
                    self._sha = self._contents.write_state(taken, sha)
                    self.state = taken
                    return {'result': 'winner-intent' if taken['callsMayHaveStarted']
                            else 'winner', 'state': taken}
                except CASConflict:
                    continue
            if same_cycle and status == 'idle':
                if state.get('callsMayHaveStarted'):
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-incomplete', 'state': state,
                            'reason': 'same-stage-idle-with-intent'}
                taken = self._takeover_state(cycle_id, stage, state,
                                             preserve_intent=False, preserve_attempts=True)
                try:
                    self._sha = self._contents.write_state(taken, sha)
                    self.state = taken
                    return {'result': 'winner', 'state': taken}
                except CASConflict:
                    continue
            # 未完成（active／idle）stage 唔可以跨越：正常／force 一視同仁，
            # 即使 expired、同 owner、冇 intent 都唔可以開下一個 stage。
            if status in ('active', 'idle'):
                self.state, self._sha = state, sha
                return {'result': 'blocked-incomplete', 'state': state,
                        'reason': f'unfinished-{status}-stage'}
            # status == completed、唔同 cycle：先處理 force↔normal 邊界，再查 7-stage gap。
            req = parse_cycle(cycle_id)
            rem = parse_cycle(state['cycleId'])
            if rem and rem['kind'] == 'normal' and req and req['kind'] == 'force':
                # 未完成 monthly cycle 唔可以被 force 覆寫（force 只授權一次查價，
                # 不代表授權放棄未完成月度進度）。
                if rem['stage'] < rem['total']:
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-force-cycle-incomplete', 'state': state,
                            'reason': 'normal-cycle-incomplete'}
                if req['date'] < rem['date']:
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-stale', 'state': state,
                            'reason': 'force-older-than-normal-cycle'}
            if rem and rem['kind'] == 'force' and req:
                if req['date'] < rem['date']:
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-stale', 'state': state,
                            'reason': 'request-older-than-force-cycle'}
                if req['date'] == rem['date'] and req['kind'] == 'normal':
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-stale', 'state': state,
                            'reason': 'same-day-normal-after-force'}
                if req['kind'] == 'normal' and req['date'] > rem['date'] \
                        and req['stage'] > 1:
                    self.state, self._sha = state, sha
                    return {'result': 'blocked-stage-gap', 'state': state,
                            'reason': 'new-cycle-must-start-at-1'}
            gap = self._stage_gap_reason(cycle_id, state['cycleId'])
            if gap:
                self.state, self._sha = state, sha
                return {'result': 'blocked-stage-gap', 'state': state, 'reason': gap}
            if self._guard_regression(cycle_id, state):
                self.state, self._sha = state, sha
                return {'result': 'completed-ahead', 'state': state}
            taken = self._takeover_state(cycle_id, stage, state, preserve_intent=False)
            try:
                self._sha = self._contents.write_state(taken, sha)
                self.state = taken
                return {'result': 'winner', 'state': taken}
            except CASConflict:
                continue
        raise CASConflict('coordinator acquire 連續 CAS 失敗')

    def renew(self):
        if self.state is None or self._sha is None:
            raise LeaseLostError('未持有 coordinator state')
        state, sha = self._contents.read_state()
        if state is None or state['leaseOwner'] != self.owner \
                or state['cycleId'] != self.state['cycleId']:
            raise LeaseLostError('lease 已轉手或消失')
        renewed = dict(state)
        renewed['leaseRenewedAt'] = self._now_int()
        renewed['leaseExpiresAt'] = self._now_int() + LEASE_SECONDS
        self._sha = self._contents.write_state(renewed, sha)
        self.state = renewed
        return renewed

    def maybe_renew(self):
        """每 5 分鐘先 renew（避免每次模型都寫 API）。"""
        if self.state and self._now_int() - self.state.get('leaseRenewedAt', 0) >= RENEW_INTERVAL_SECONDS:
            return self.renew()
        return None

    def mark_calls_may_have_started(self):
        """Write-ahead intent：任何真實 BigGo 請求之前 CAS 持久化 callsMayHaveStarted。

        只寫一次（state 已 True 即 no-op）；CAS 失敗／lease 轉手即 raise，caller 必須
        禁止發請求。唔會每個 request 寫 coordinator。
        """
        if self.state is None:
            raise LeaseLostError('未持有 coordinator state')
        if self.state.get('callsMayHaveStarted'):
            return True
        self.state['callsMayHaveStarted'] = True
        self.state['intentAt'] = self._now_int()
        self.state['updatedAt'] = self._now_int()
        for _attempt in range(2):
            remote, sha = self._contents.read_state()
            if remote is not None:
                if remote.get('leaseOwner') != self.owner \
                        or remote.get('cycleId') != self.state['cycleId']:
                    raise LeaseLostError('intent 寫入前 lease 已轉手')
                if remote.get('callsMayHaveStarted'):
                    self.state['callsMayHaveStarted'] = True
                    self.state['intentAt'] = remote.get('intentAt')
                    self.state['updatedAt'] = remote.get('updatedAt', self.state['updatedAt'])
                    return True
            try:
                self._sha = self._contents.write_state(self.state, sha)
                return True
            except CASConflict:
                continue
        raise CASConflict('intent 寫入連續 CAS 失敗')

    def record_attempt(self, kind, n=1):
        if self.state is None:
            raise LeaseLostError('未持有 coordinator state')
        if kind not in ('token', 'search'):
            raise CoordinatorStateError('attempt kind 只准 token／search')
        self.state['requestAttempts'][kind] = self.state['requestAttempts'].get(kind, 0) + n

    def record_response(self, status):
        if self.state is None:
            raise LeaseLostError('未持有 coordinator state')
        key = str(int(status))
        self.state['responseStatusCounts'][key] = \
            self.state['responseStatusCounts'].get(key, 0) + 1

    def apply_cooldown(self, status, headers=None):
        until, evidence, basis = cooldown_until_for(status, headers, self._now_int())
        if until:
            self.state['cooldownUntil'] = max(self.state.get('cooldownUntil', 0), until)
        return {'cooldownUntil': self.state.get('cooldownUntil', 0),
                'evidence': evidence, 'basis': basis}

    def commit(self, *, status=None, snapshot_hash=None, snapshot_path=None,
               stage_result_hash=None, stage_result_path=None, bundle_hash=None,
               last_review_reason=None):
        if self.state is None:
            raise LeaseLostError('未持有 coordinator state')
        if status is not None:
            if status not in STATUSES:
                raise CoordinatorStateError(f'status 唔合法：{status!r}')
            self.state['status'] = status
        if snapshot_hash is not None:
            if not SNAPSHOT_HASH_RE.match(snapshot_hash):
                raise CoordinatorStateError('snapshotHash 格式錯誤')
            self.state['snapshotHash'] = snapshot_hash
        if snapshot_path is not None:
            self.state['snapshotPath'] = snapshot_path
        if stage_result_hash is not None:
            if not SNAPSHOT_HASH_RE.match(stage_result_hash):
                raise CoordinatorStateError('stageResultHash 格式錯誤')
            self.state['stageResultHash'] = stage_result_hash
        if stage_result_path is not None:
            self.state['stageResultPath'] = stage_result_path
        if bundle_hash is not None:
            if not SNAPSHOT_HASH_RE.match(bundle_hash):
                raise CoordinatorStateError('bundleHash 格式錯誤')
            self.state['bundleHash'] = bundle_hash
        if last_review_reason is not None:
            self.state['lastReviewReason'] = str(last_review_reason)[:120]
        if status in ('completed', 'needs_review'):
            # 完成／待審核即釋放 lease，唔會阻擋下一個 cycle／stage。
            self.state['leaseOwner'] = None
            self.state['leaseExpiresAt'] = 0
        if self.state.get('status') == 'completed':
            # per-stage intent 唔可以帶去下一個 stage。
            self.state['callsMayHaveStarted'] = False
            self.state['intentAt'] = None
        self.state['updatedAt'] = self._now_int()
        for _attempt in range(2):
            remote, sha = self._contents.read_state()
            if remote is not None:
                if remote.get('leaseOwner') != self.owner \
                        or remote.get('cycleId') != self.state['cycleId']:
                    raise LeaseLostError('commit 前 lease 已轉手')
                # 單調合併：同一 owner 嘅並行寫入只可以抬高計數，唔可以降低。
                for kind in ('token', 'search'):
                    self.state['requestAttempts'][kind] = max(
                        self.state['requestAttempts'].get(kind, 0),
                        remote.get('requestAttempts', {}).get(kind, 0))
                counts = dict(remote.get('responseStatusCounts', {}))
                for key, value in self.state.get('responseStatusCounts', {}).items():
                    counts[key] = max(counts.get(key, 0), value)
                self.state['responseStatusCounts'] = counts
            try:
                self._sha = self._contents.write_state(self.state, sha)
                return self._sha
            except CASConflict:
                continue
        raise CASConflict('coordinator commit 連續 CAS 失敗')

    def abort_without_calls(self, *, clear_intent=False):
        """零呼叫之下安全收手：清 lease、status 回 idle（唔算 needs_review）。

        `clear_intent=True` 只用於確認零 HTTP request 的情況下，避免下次 takeover
        因 false-positive intent 而卡 needs_review。
        """
        self.state['status'] = 'idle'
        self.state['leaseOwner'] = None
        self.state['leaseExpiresAt'] = 0
        if clear_intent:
            self.state['callsMayHaveStarted'] = False
            self.state['intentAt'] = None
        return self.commit()

    # ---- budget ----

    def budget(self, queued_models):
        """Stage-local budget（唔係 provider window enforcement）。

        `quota80Cap` 只係由 stage 的 providerQuotaLimit 算出的一個數字；因冇跨 stage／
        window 的持久 usage ledger，`providerWindowAccounting` 一律為 'unsupported'。
        Runner 對有 provider 配置的路徑必須先 fail-closed，唔可以靠呢個數字聲稱已對
        provider window enforce。
        """
        attempts = self.state['requestAttempts']
        local_cap = compute_local_cap(queued_models)
        quota_cap = compute_quota_cap(self.state.get('providerQuotaLimit'))
        caps = {'local': local_cap}
        if quota_cap is not None:
            caps['quota80'] = quota_cap
        effective = min(caps.values())
        remaining_search = effective - attempts.get('search', 0)
        remaining_token = 1 - attempts.get('token', 0)  # 每個 stage 最多一個 token 階段
        return {
            'searchUsed': attempts.get('search', 0),
            'tokenUsed': attempts.get('token', 0),
            'localCap': local_cap,
            'quota80Cap': quota_cap,
            'providerWindowAccounting': 'unsupported',
            'effectiveSearchCap': effective,
            'remainingSearch': max(0, remaining_search),
            'remainingToken': max(0, remaining_token),
            'allowed': remaining_search > 0,
        }

    # ---- snapshot publish / import ----

    def publish_snapshot(self, snapshot, manifest):
        validate_price_snapshot(snapshot)
        snapshot_bytes = canonical_json_bytes(snapshot)
        snap_hash = sha256_id(snapshot_bytes)
        snap_path = f'{self._contents.config.snapshot_dir}/{cycle_slug(self.state["cycleId"])}/biggo_prices.json'
        man_path = f'{self._contents.config.snapshot_dir}/{cycle_slug(self.state["cycleId"])}/manifest.json'
        manifest = dict(manifest)
        manifest.update({
            'schemaVersion': MANIFEST_SCHEMA_VERSION,
            'snapshotSchemaVersion': SNAPSHOT_SCHEMA_VERSION,
            'snapshotHash': snap_hash,
        })
        existing, _sha = self._contents.read_snapshot(snap_path)
        if existing is not None:
            if sha256_id(existing) != snap_hash:
                raise CoordinatorError('已有同 cycle snapshot 但 hash 唔同，拒絕覆蓋')
        else:
            self._contents.write_snapshot(snap_path, snapshot_bytes,
                                          'coordinator: publish price snapshot')
        # snapshot 已成功，manifest 係第二個 CAS 寫入。若呢步失敗，publication 屬
        # 「唔確定」：caller 必須 needs_review，唔可以自動重跑。
        self._contents.write_snapshot(man_path, canonical_json_bytes(manifest),
                                      'coordinator: publish snapshot manifest')
        return {'snapshotHash': snap_hash, 'snapshotPath': snap_path,
                'manifestPath': man_path}

    def import_snapshot(self, expected_cycle, expected_stage):
        """Loser 匯入：cycle、stage、schema、hash 全部要 match，否則 raise。"""
        base = f'{self._contents.config.snapshot_dir}/{cycle_slug(expected_cycle)}'
        man_bytes, _ = self._contents.read_snapshot(f'{base}/manifest.json')
        if man_bytes is None:
            raise SnapshotImportError('coordinator 冇 manifest 可以匯入')
        try:
            manifest = json.loads(man_bytes.decode('utf-8'))
        except ValueError as e:
            raise SnapshotImportError(f'manifest JSON 損毀：{e}')
        if manifest.get('schemaVersion') != MANIFEST_SCHEMA_VERSION:
            raise SnapshotImportError('manifest schemaVersion 唔 match')
        if manifest.get('cycleId') != expected_cycle or manifest.get('stage') != expected_stage:
            raise SnapshotImportError('manifest cycle／stage 唔 match')
        snap_bytes, _ = self._contents.read_snapshot(f'{base}/biggo_prices.json')
        if snap_bytes is None:
            raise SnapshotImportError('coordinator 冇 snapshot 可以匯入')
        actual = sha256_id(snap_bytes)
        if manifest.get('snapshotHash') != actual:
            raise SnapshotImportError('snapshot sha256 同 manifest 唔 match')
        try:
            snapshot = json.loads(snap_bytes.decode('utf-8'))
        except ValueError as e:
            raise SnapshotImportError(f'snapshot JSON 損毀：{e}')
        validate_price_snapshot(snapshot)
        return {'snapshot': snapshot, 'snapshotHash': actual, 'manifest': manifest}

    # ---- P0 stage bundle（v2） ----

    def _bundle_paths(self, cycle_id):
        base = f'{self._contents.config.snapshot_dir}/{cycle_slug(cycle_id)}'
        return {
            'base': f'{base}/base-biggo_prices.json',
            'snapshot': f'{base}/biggo_prices.json',
            'stageResult': f'{base}/stage-result.json',
            'manifest': f'{base}/manifest.json',
        }

    def _write_immutable(self, path, data, message):
        existing, _sha = self._contents.read_snapshot(path)
        if existing is not None:
            if existing != data:
                raise CoordinatorError('immutable stage artifact conflict（同 cycle 唔可覆寫）')
            return
        self._contents.write_snapshot(path, data, message)

    def bundle_hash(self, manifest):
        """bundle 整體 id：manifest canonical bytes 的 sha256。"""
        return sha256_id(canonical_json_bytes(manifest))

    def publish_bundle(self, base_snapshot, new_snapshot, stage_result, manifest_fields):
        """P0 發佈：寫 base → new → stage-result → manifest（manifest 最後 = 凍結點）。

        所有檔案 immutable：同 cycle 不同 hash 禁止覆寫；相同 bytes 係 no-op。
        """
        validate_price_snapshot(base_snapshot, allow_empty=True)
        validate_price_snapshot(new_snapshot)
        validate_stage_result(stage_result, base_snapshot, new_snapshot,
                              cycle_id=manifest_fields.get('cycleId'),
                              stage=manifest_fields.get('stage'),
                              mode=manifest_fields.get('mode'))
        if not isinstance(manifest_fields, dict):
            raise CoordinatorStateError('manifest fields 必須係 object')
        paths = self._bundle_paths(manifest_fields['cycleId'])
        base_bytes = canonical_json_bytes(base_snapshot)
        new_bytes = canonical_json_bytes(new_snapshot)
        sr_bytes = canonical_json_bytes(stage_result)
        base_hash = sha256_id(base_bytes)
        new_hash = sha256_id(new_bytes)
        sr_hash = sha256_id(sr_bytes)
        manifest = dict(manifest_fields)
        manifest.update({
            'schemaVersion': BUNDLE_MANIFEST_SCHEMA_VERSION,
            'snapshotSchemaVersion': SNAPSHOT_SCHEMA_VERSION,
            'stageResultSchemaVersion': STAGE_RESULT_SCHEMA_VERSION,
            'recordCount': len(new_snapshot),
            'baseSnapshotHash': base_hash,
            'newSnapshotHash': new_hash,
            'snapshotHash': new_hash,
            'stageResultHash': sr_hash,
            'files': {'base': paths['base'], 'snapshot': paths['snapshot'],
                      'stageResult': paths['stageResult']},
            'authority': 'coordinator-stage-result-v1',
        })
        man_bytes = canonical_json_bytes(manifest)
        self._write_immutable(paths['base'], base_bytes, 'coordinator: stage base snapshot')
        self._write_immutable(paths['snapshot'], new_bytes, 'coordinator: stage snapshot')
        self._write_immutable(paths['stageResult'], sr_bytes, 'coordinator: stage result')
        self._write_immutable(paths['manifest'], man_bytes, 'coordinator: stage manifest')
        return {
            'manifest': manifest,
            'baseSnapshotHash': base_hash,
            'newSnapshotHash': new_hash,
            'snapshotHash': new_hash,
            'stageResultHash': sr_hash,
            'bundleHash': self.bundle_hash(manifest),
            'paths': paths,
            'snapshotPath': paths['snapshot'],
            'stageResultPath': paths['stageResult'],
            'manifestPath': paths['manifest'],
        }

    def import_bundle(self, expected_cycle, expected_stage):
        """讀取並嚴格驗證 v2 bundle；任何缺檔／hash／binding 唔符即 raise。"""
        paths = self._bundle_paths(expected_cycle)
        man_bytes, _sha = self._contents.read_snapshot(paths['manifest'])
        if man_bytes is None:
            raise SnapshotImportError('coordinator 冇 manifest 可以匯入')
        try:
            manifest = json.loads(man_bytes.decode('utf-8'))
        except ValueError as e:
            raise SnapshotImportError(f'manifest JSON 損毀：{e}')
        if manifest.get('schemaVersion') == MANIFEST_SCHEMA_VERSION:
            raise LegacyBundleError('legacy snapshot-only manifest 唔支援自動 apply')
        if manifest.get('schemaVersion') != BUNDLE_MANIFEST_SCHEMA_VERSION:
            raise SnapshotImportError('manifest schemaVersion 唔支援')
        if manifest.get('cycleId') != expected_cycle or manifest.get('stage') != expected_stage:
            raise SnapshotImportError('manifest cycle／stage 唔 match')
        if sha256_id(man_bytes) != sha256_id(canonical_json_bytes(manifest)):
            raise SnapshotImportError('manifest bytes 唔係 canonical JSON')
        for key in ('baseSnapshotHash', 'newSnapshotHash', 'stageResultHash'):
            if not _is_sha(manifest.get(key)):
                raise SnapshotImportError(f'manifest.{key} 唔合法')

        def _read(path, label):
            data, _ = self._contents.read_snapshot(path)
            if data is None:
                raise SnapshotImportError(f'coordinator 缺少 {label}')
            return data

        base_bytes = _read(paths['base'], 'base snapshot')
        new_bytes = _read(paths['snapshot'], 'snapshot')
        sr_bytes = _read(paths['stageResult'], 'stage-result')
        if sha256_id(base_bytes) != manifest['baseSnapshotHash']:
            raise SnapshotImportError('base snapshot hash 唔 match')
        if sha256_id(new_bytes) != manifest['newSnapshotHash']:
            raise SnapshotImportError('snapshot hash 唔 match')
        if sha256_id(sr_bytes) != manifest['stageResultHash']:
            raise SnapshotImportError('stage-result hash 唔 match')
        try:
            base_snapshot = json.loads(base_bytes.decode('utf-8'))
            new_snapshot = json.loads(new_bytes.decode('utf-8'))
            stage_result = json.loads(sr_bytes.decode('utf-8'))
        except (ValueError, UnicodeDecodeError) as e:
            raise SnapshotImportError(f'bundle JSON 損毀：{e}')
        validate_stage_result(stage_result, base_snapshot, new_snapshot,
                              cycle_id=expected_cycle, stage=expected_stage,
                              mode=manifest.get('mode'))
        if manifest.get('recordCount') != len(new_snapshot):
            raise SnapshotImportError('manifest recordCount 唔 match')
        # state 綁定（completed 時必須一致）
        if self.state is not None and self.state.get('status') == 'completed':
            if self.state.get('snapshotHash') != manifest['newSnapshotHash']:
                raise SnapshotImportError('state.snapshotHash 同 manifest 唔 match')
            if self.state.get('snapshotPath') != paths['snapshot']:
                raise SnapshotImportError('state.snapshotPath 同 manifest 唔 match')
            if self.state.get('stageResultHash') not in (None, manifest['stageResultHash']):
                raise SnapshotImportError('state.stageResultHash 同 manifest 唔 match')
        return {
            'manifest': manifest,
            'stageResult': stage_result,
            'baseSnapshot': base_snapshot,
            'newSnapshot': new_snapshot,
            'baseSnapshotHash': manifest['baseSnapshotHash'],
            'newSnapshotHash': manifest['newSnapshotHash'],
            'stageResultHash': manifest['stageResultHash'],
            'bundleHash': self.bundle_hash(manifest),
            'snapshotPath': paths['snapshot'],
        }
