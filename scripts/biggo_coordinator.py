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


def utc_stamp(now=None):
    ts = now if now is not None else time.time()
    if isinstance(ts, datetime):
        ts = ts.timestamp()
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def canonical_json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sha256_id(data):
    import hashlib
    return 'sha256:' + hashlib.sha256(data).hexdigest()


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
    """Factual provider quota 嘅 80% 自動上限（quota 未確認 → None）。"""
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


def validate_price_snapshot(snapshot):
    """Price snapshot schema：{model: {price, merchants, url, updated}}；唔合格 raise。"""
    if not isinstance(snapshot, dict) or not snapshot:
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

    def acquire(self, cycle_id, stage):
        """CAS 搶／續／接收 lease；回傳 {'result', 'state'}。"""
        for _attempt in range(2):
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
            if state['status'] == 'completed' and same_cycle:
                self.state, self._sha = state, sha
                return {'result': 'completed-idempotent', 'state': state}
            if state['status'] == 'needs_review' and same_cycle:
                self.state, self._sha = state, sha
                return {'result': 'needs-review', 'state': state}
            lease_active = (state['status'] == 'active'
                            and state['leaseOwner'] is not None
                            and state['leaseExpiresAt'] > self._now_int())
            if lease_active and state['leaseOwner'] != self.owner:
                self.state, self._sha = state, sha
                return {'result': 'lost', 'state': state}
            if same_cycle and state['status'] == 'active' and state['leaseOwner'] == self.owner:
                renewed = dict(state)
                renewed['leaseRenewedAt'] = self._now_int()
                renewed['leaseExpiresAt'] = self._now_int() + LEASE_SECONDS
                try:
                    self._sha = self._contents.write_state(renewed, sha)
                    self.state = renewed
                    return {'result': 'renewed', 'state': renewed}
                except CASConflict:
                    continue
            # 新 cycle 或 expired takeover：重設進度，保留 provider quota 事實
            taken = initial_state(cycle_id, stage, self.owner, self._now_int(),
                                  state.get('providerQuotaLimit', self._provider_quota_limit),
                                  state.get('providerWindowEnd', self._provider_window_end))
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
        if last_review_reason is not None:
            self.state['lastReviewReason'] = str(last_review_reason)[:120]
        if status in ('completed', 'needs_review'):
            # 完成／待審核即釋放 lease，唔會阻擋下一個 cycle／stage。
            self.state['leaseOwner'] = None
            self.state['leaseExpiresAt'] = 0
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

    def abort_without_calls(self):
        """零呼叫之下安全收手：清 lease、status 回 idle（唔算 needs_review）。"""
        self.state['status'] = 'idle'
        self.state['leaseOwner'] = None
        self.state['leaseExpiresAt'] = 0
        return self.commit()

    # ---- budget ----

    def budget(self, queued_models):
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
