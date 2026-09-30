#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BigGo 香港格價價錢快照抓取（官方 JSON API）

- 用 BigGo 官方 API：https://api.biggo.com/api/v1/spa/search/{query}/product
  （同網頁版 biggo.hk 係唔同 host；GitHub Actions IP 對 api.biggo.com 友好，
  2026-08-26 實測 HTTP 200）
- 每月最多一次、分 7 日分批（批次進度共用 batch_utils）
輸出：biggo_prices.json {型號: {price: "$X,XXX-YY,YYY", merchants: N, updated: 日期}}

共享工具（同 fetch_pricesapi 一套規則，唔會走樣）：
- crawl_utils：norm_model / load_models
- price_utils：num_price / is_ac_title（冷氣關鍵字 + 配件排除同一套）
- batch_utils：prices_meta 讀取、批次切片 get_batch_todo（網絡階段零寫入）
"""
import base64
import json
import os
import random
import sys
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from crawl_utils import norm_model, load_models, canonical_model_key, load_brand_lookup
from price_utils import num_price as _num_price, is_ac_title, model_excerpt
from batch_utils import (PRICE_BATCH_DAYS, load_meta, get_batch_todo)
from model_lifecycle import (load_blacklist, filter_active, plan_record_results,
                             plan_revive, compute_effects)
from biggo_canonical import canonical_json_bytes, sha256_json
from biggo_limiter import BigGoLimitExceeded, RequestLimiter

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'biggo_prices.json')

# 黑名單復核 quota：每個批次日最多復核 40 個黑名單型號（獨立小額，唔會永遠冇得復活）
BLACKLIST_REVIEW_QUOTA = 40

# 官方 JSON API（product search 需登入認證；2026-08-26 起免登入通道已關閉，見 docs/DECISIONS.md D10）
API_URL = 'https://api.biggo.com/api/v1/spa/search/{q}/product'
AUTH_URL = 'https://api.biggo.com/auth/v1/token'
API_HEADERS = {'Content-Type': 'application/json', 'site': 'biggo.hk', 'region': 'hk'}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')

# access_token 快取（client credentials；55 分鐘 TTL，token 一般 60 分鐘有效）
_TOKEN = {'value': None, 'expires': 0.0}
# token 取得序列化：兩個 worker 同時 cache miss 亦只可以發一次 auth request。
_TOKEN_LOCK = threading.Lock()
# REQUEST_STATS 由多個 worker 更新；加鎖避免 lost update（bundle 計數要準確）。
_STATS_LOCK = threading.Lock()

# ===== approved design C：兩次 attempt、403／429 即停、離線 guard、可審計計數 =====
# 每個 model 最多 2 次 attempt；403／429 唔會即刻 retry（honour Retry-After，否則項目
# 自身 48 小時冷卻；48h 唔係 provider 嘅 quota window）。
DEFAULT_MAX_ATTEMPTS = 2
# 項目自身 fallback 冷卻（秒）；呢個係 aircon-compare 政策，唔代表 provider quota。
PROJECT_COOLDOWN_SECONDS = 48 * 3600
# 只計實際發出嘅 HTTP request，token 同 search 分開。
REQUEST_STATS = {'token': 0, 'search': 0}
# 最近一次 403／429 嘅安全 rate-limit 證據（只 status／Retry-After，無 auth／body）。
LAST_RATE_LIMIT_EVIDENCE = {}
BIGGO_TEST_MODE_ENV = 'AIRCON_BIGGO_TEST_MODE'


class BigGoTestModeError(RuntimeError):
    """測試模式禁止真實 BigGo 網絡呼叫（E2 實作階段硬保險）。"""


class BigGoAbortedError(RuntimeError):
    """lease lost／should_abort：唔可以再發新請求。"""


# 每個 stage 由 runner 注入；同一時間只有一個 limiter（請求前 thread-safe reserve）。
_REQUEST_LIMITER = None
_ABORT_CHECK = None


def set_request_limiter(limiter):
    global _REQUEST_LIMITER
    _REQUEST_LIMITER = limiter


def set_abort_check(check):
    global _ABORT_CHECK
    _ABORT_CHECK = check


def get_request_limiter():
    return _REQUEST_LIMITER


def _reserve(kind):
    """每個真正 outbound 請求前：先查 abort，再向 limiter reserve。"""
    if _ABORT_CHECK is not None and _ABORT_CHECK():
        raise BigGoAbortedError('lease lost／abort：停止新 BigGo 請求')
    if _REQUEST_LIMITER is not None:
        _REQUEST_LIMITER.reserve(kind)


def reset_request_stats():
    with _STATS_LOCK:
        REQUEST_STATS['token'] = 0
        REQUEST_STATS['search'] = 0
    LAST_RATE_LIMIT_EVIDENCE.clear()


def _bump_stats(kind):
    with _STATS_LOCK:
        REQUEST_STATS[kind] += 1


def _abort_requested():
    return _ABORT_CHECK is not None and _ABORT_CHECK()


def _sleep_abortable(seconds, *, chunk=0.2):
    """可被 abort 截斷的 sleep；abort 即 raise BigGoAbortedError。

    `_ABORT_CHECK is None`（例如 standalone fetch 測試／非 runner 呼叫）時維持原本
    單次 sleep 契約；runner 全程一定設置 abort check，所以生產等待都係可截斷分段。
    """
    seconds = max(0.0, float(seconds))
    if seconds <= 0:
        return
    if _ABORT_CHECK is None:
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while True:
        if _abort_requested():
            raise BigGoAbortedError('abort during wait：停止新 BigGo 請求')
        remain = end - time.monotonic()
        if remain <= 0:
            return
        time.sleep(min(remain, chunk))


def snapshot_request_stats():
    return dict(REQUEST_STATS)


def _network_guard():
    """測試模式：任何真實網絡入口即 raise（唔會靜默繼續）。"""
    if os.environ.get(BIGGO_TEST_MODE_ENV) == '1':
        raise BigGoTestModeError('BigGo 測試模式：禁止真實網絡呼叫')


def _record_response_evidence(status, retry_after=None):
    LAST_RATE_LIMIT_EVIDENCE.clear()
    LAST_RATE_LIMIT_EVIDENCE.update({'status': int(status)})
    if retry_after is not None:
        LAST_RATE_LIMIT_EVIDENCE['retryAfter'] = str(retry_after)[:128]


def _retry_after_seconds(value, now=None):
    """Retry-After → 秒數 int／None。支持 delta-seconds 同 HTTP-date 兩種標準寫法。

    唔會推斷 provider 嘅 quota reset；只係 honour 來源實際提供嘅值。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    try:
        from email.utils import parsedate_to_datetime
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        import datetime as _dt
        target = target.replace(tzinfo=_dt.timezone.utc)
    base = now if now is not None else time.time()
    try:
        return max(0, int(target.timestamp() - float(base)))
    except (OverflowError, OSError, ValueError):
        return None


def _get_access_token(timeout=20):
    """用 BIGGO_CLIENT_ID/SECRET 攞 access_token（免費官方認證；冇配置就 None = 免登入 fallback）

    `timeout` 預設 20 秒，同原本批次行為一致；smoke 會傳較短 timeout 令連線測試有界。
    測試模式（AIRCON_BIGGO_TEST_MODE=1）下任何真實呼叫即 BigGoTestModeError。
    token 取得全程 token lock 序列化：cache miss 時只會發一次 auth request。
    """
    cid = os.environ.get('BIGGO_CLIENT_ID', '').strip()
    csec = os.environ.get('BIGGO_CLIENT_SECRET', '').strip()
    if not cid or not csec:
        return None
    _network_guard()
    now = time.time()
    if _TOKEN['value'] and now < _TOKEN['expires']:
        return _TOKEN['value']
    with _TOKEN_LOCK:
        now = time.time()
        if _TOKEN['value'] and now < _TOKEN['expires']:
            return _TOKEN['value']
        _reserve('token')
        cred = base64.b64encode(f'{cid}:{csec}'.encode()).decode()
        data = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
        req = urllib.request.Request(AUTH_URL, data=data, headers={
            'Authorization': f'Basic {cred}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': UA,
        })
        _bump_stats('token')
        try:
            tok = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8')).get('access_token')
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                _record_response_evidence(e.code, (e.headers or {}).get('Retry-After')
                                          if hasattr(e.headers, 'get') else None)
            raise
        if tok:
            _TOKEN['value'] = tok
            _TOKEN['expires'] = now + 55 * 60
        return tok

# 全局冷卻狀態：遇 429 就冷卻 60-120s，期間所有新請求停喺度等（防止批次被限流打死）
_COOLDOWN_UNTIL = 0.0
_NEXT_SLOT = 0.0
_LOCK = __import__('threading').Lock()

# 主動限速：批次內全局最小請求間隔（2 個 worker 共用；每秒最多約 0.4 個請求）
# 默認 2.5s 保守值（GitHub IP）；本地驗證可設 BIGGO_MIN_PACE=0.4 加速（本地 IP 友好）
MIN_PACE = float(os.environ.get('BIGGO_MIN_PACE', '2.5'))


def _global_cooldown(seconds):
    """全局冷卻：批次內任何 worker 觸發 429 後，所有請求一齊等"""
    global _COOLDOWN_UNTIL
    with _LOCK:
        _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.time() + seconds)


def _wait_cooldown():
    """喺發請求前等待全局冷卻結束；等待期間可被 abort 截斷。"""
    while True:
        with _LOCK:
            remain = _COOLDOWN_UNTIL - time.time()
        if remain <= 0:
            return
        _sleep_abortable(min(remain, 0.5))


def _wait_pace():
    """全局最小請求間隔（主動限速）；等待期間可被 abort 截斷。"""
    global _NEXT_SLOT
    with _LOCK:
        wait = _NEXT_SLOT - time.time()
        _NEXT_SLOT = max(time.time(), _NEXT_SLOT) + MIN_PACE
    if wait > 0:
        _sleep_abortable(wait)


def _api_search(model, jitter=(0.2, 0.6), *, max_attempts=DEFAULT_MAX_ATTEMPTS, timeout=20,
                use_cooldown=True, use_pace=True, sleep_on_error=True):
    """官方 API 搜尋：回 (data, reachable)
    reachable=True  → API 有正常回覆（data 可能係空結果 = 乾淨無匹配）
    reachable=False → 網絡／限流錯誤（唔計入淘汰統計）

    Approved design C 語義：
    - 每個 model 最多 `max_attempts` 次（預設 2）；
    - 403／429 **唔會即刻 retry**：記錄安全證據（status／Retry-After）後直接回
      reachable=False；Retry-After 有就交 coordinator honour，冇就由項目自身 48 小時
      冷卻接手（唔係 provider quota window）；
    - 其他錯誤才按 attempt backoff retry；
    - smoke 會傳 max_attempts=1、timeout=8、use_cooldown=False、use_pace=False、
      sleep_on_error=False，維持有界。
    """
    LAST_RATE_LIMIT_EVIDENCE.clear()
    if os.environ.get(BIGGO_TEST_MODE_ENV) == '1':
        LAST_RATE_LIMIT_EVIDENCE.update({'testMode': True})
        return None, False
    for attempt in range(max(1, int(max_attempts))):
        retry_wait = None
        try:
            if use_cooldown:
                _wait_cooldown()
            if use_pace:
                _wait_pace()
            headers = {'User-Agent': UA, **API_HEADERS, 'Accept': 'application/json'}
            token = _get_access_token(timeout=timeout)
            if token:
                headers['Authorization'] = f'Bearer {token}'
            _reserve('search')
            req = urllib.request.Request(API_URL.format(q=urllib.parse.quote(model, safe='')), headers=headers)
            _bump_stats('search')
            data = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore'))
            return data, True
        except urllib.error.HTTPError as e:
            retry_after = None
            try:
                retry_after = e.headers.get('Retry-After') if e.headers else None
            except AttributeError:
                retry_after = None
            if e.code in (403, 429):
                _record_response_evidence(e.code, retry_after)
                if use_cooldown and retry_after:
                    wait = _retry_after_seconds(retry_after, now=time.time()) or 0
                    if wait > 0:
                        print(f'  ⏳ {e.code} 限流：honour Retry-After {wait}s（{model}）；'
                              f'本 model 唔會即刻 retry', flush=True)
                        _global_cooldown(wait)
                print(f'  ⏳ {e.code}：唔即刻 retry（{model}）；安全證據已記錄，'
                      f'冷卻交由 coordinator／項目 48h fallback 處理', flush=True)
                return None, False
            retry_wait = 5 * (attempt + 1)
        except BigGoTestModeError:
            raise
        except BigGoAbortedError:
            print(f'  ⏹️ abort：停止新請求（{model}）；當網絡錯誤，唔會再排隊', flush=True)
            return None, False
        except BigGoLimitExceeded as e:
            print(f'  ⛔ {e}（{model}）；當網絡錯誤，唔會當 clean miss', flush=True)
            return None, False
        except Exception:
            retry_wait = 3 * (attempt + 1)
        if retry_wait is not None and sleep_on_error:
            try:
                _sleep_abortable(retry_wait)
            except BigGoAbortedError:
                print(f'  ⏹️ abort：停止新請求（{model}）；當網絡錯誤，唔會再排隊', flush=True)
                return None, False
    return None, False


def _extract_price(data, model):
    """從 API data 抽最平價（共用過濾規則：型號 boundary-aware 匹配 + 冷氣關鍵字 + 排除配件 + 香港商戶）；冇匹配回 None

    新抓取會一併保留最平項目的 `matchedTitle` 同 `nindex` 作身份證據（公開資料、
    非秘密；只限 upstream response 真的有嘅值，唔會 retroactively 補舊快照）。
    `matchedTitle` 係包含匹配型號嘅 bounded NFKC 正規化 excerpt；型號長過 limit → 唔
    加該欄，唔會聲稱有 identity 證據。
    """
    nm = norm_model(model)
    matched = []
    for it in (data or {}).get('list', []):
        title = (it.get('title') or '').strip()
        # 共用過濾規則（同 PricesAPI 一套）：型號精確匹配 + 冷氣關鍵字 + 排除配件
        if len(nm) < 4 or not is_ac_title(title, nm):
            continue
        # 只收香港商戶（排除 us_bid_aliexpress 等外國平台撞名產品）
        nindex = it.get('nindex') or ''
        if not nindex.startswith('hk_'):
            continue
        p = _num_price(it.get('price'))
        if p:
            matched.append({'price': p, 'excerpt': model_excerpt(title, nm, limit=200),
                            'nindex': nindex[:64]})
    if not matched:
        return None
    prices = [m['price'] for m in matched]
    lo, hi = min(prices), max(prices)
    cheapest = min(matched, key=lambda m: m['price'])
    price = f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起'
    entry = {
        'price': price,
        'merchants': len(prices),
        'url': 'https://biggo.hk/s/?q=' + urllib.parse.quote(model),
        'updated': time.strftime('%Y-%m-%d'),
    }
    excerpt = cheapest['excerpt']
    if excerpt:
        entry['matchedTitle'] = excerpt
    if cheapest['nindex']:
        entry['nindex'] = cheapest['nindex']
    return entry


def fetch_biggo_price(model):
    """官方 API 搜一個型號，回傳 {price, merchants, url} 或 None"""
    data, _reachable = _api_search(model)
    return _extract_price(data, model)


def protected_models():
    """受保護型號（canonical key）：核心 29 + 有官方網店價型號（唔會自動淘汰，治理要求）"""
    protected = set()
    try:
        from models_data import MODELS
        for m in MODELS:
            key = canonical_model_key(m.get('brand'), m.get('model'))
            if key:
                protected.add(key)
    except Exception:
        pass
    brand_lookup = load_brand_lookup()
    for fname in ('official_specs.json', 'rasonic_official.json', 'pana_official.json',
                  'midea_official.json', 'shew_official.json', 'general_official.json',
                  'carrier_official.json'):
        p = os.path.join(BASE, fname)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
            for k, v in data.items():
                if isinstance(v, dict) and str(v.get('price', '')).startswith('HK$'):
                    brand = brand_lookup.get(norm_model(k))
                    if brand:
                        protected.add(canonical_model_key(brand, k))
        except Exception:
            pass
    return protected


def _search_tri_state(model):
    """三態搜尋（force batch 同 price batch 共用）：
    回 (model, result_or_None, ok)
      ok=True   → API 正常回覆（result 可能有價，可能係乾淨無匹配）
      ok=False  → 網絡/限流/認證錯誤（唔計入淘汰統計）
    """
    data, reachable = _api_search(model)
    if not reachable:
        return model, None, False
    return model, _extract_price(data, model), True


def _brand_of(brand_lookup):
    """型號 → 品牌原文（解唔到就 UNKNOWN，令 key 保持 canonical 一致）"""
    return lambda m: brand_lookup.get(norm_model(m)) or 'UNKNOWN'


# ===== P0 staged batch（網絡階段零本地寫入；效果交 coordinator bundle）=====


def _load_snapshot_readonly():
    """讀本地 biggo_prices.json（唯讀）；缺失／壞檔回 {}。"""
    if not os.path.exists(OUT_PATH):
        return {}
    try:
        with open(OUT_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _read_tracking():
    """讀本地 model_status.json（唯讀）。"""
    from crawl_utils import load_json
    from model_lifecycle import TRACKING_PATH
    data = load_json(TRACKING_PATH, {})
    return data if isinstance(data, dict) else {}


def _state_hashes(tracking, blacklist):
    return {'trackingHash': sha256_json(tracking),
            'blacklistHash': sha256_json(blacklist)}


def _key_of(model, brand_lookup):
    return canonical_model_key(brand_lookup.get(norm_model(model)) or 'UNKNOWN', model)


def _merge_outcomes(got, clean_miss, net_err, revived, results, brand_lookup):
    """組合 outcome ledger（priced／clean_miss／net_error）；每個 model 最多一次。"""
    out = []
    seen = set()
    for m in list(got) + [m for m in revived if m in results]:
        if m in seen:
            continue
        seen.add(m)
        entry = results.get(m)
        if entry is not None:
            out.append({'model': m, 'canonicalKey': _key_of(m, brand_lookup),
                        'outcome': 'priced', 'price': entry})
    for m in clean_miss:
        if m in seen:
            continue
        seen.add(m)
        out.append({'model': m, 'canonicalKey': _key_of(m, brand_lookup),
                    'outcome': 'clean_miss'})
    for m in net_err:
        if m in seen:
            continue
        seen.add(m)
        out.append({'model': m, 'canonicalKey': _key_of(m, brand_lookup),
                    'outcome': 'net_error'})
    return out


def _partial_payload(status, base, results, got, clean_miss, net_err, brand_lookup, **extra):
    """未完成 stage：保留 staged 證據，但唔會有 effects／發佈。"""
    payload = {
        'status': status,
        'baseSnapshot': base,
        'snapshot': results,
        'outcomes': _merge_outcomes(got, clean_miss, net_err, [], results, brand_lookup),
        'counters': {'got': len(got), 'cleanMiss': len(clean_miss),
                     'netErrors': len(net_err)},
    }
    payload.update(extra)
    return payload


def _completed_payload(*, idx, base, results, got, clean_miss, net_err, revived,
                       review_evidence, pre_tracking, pre_black, post_tracking,
                       post_black, brand_lookup):
    effects = compute_effects(pre_tracking, pre_black, post_tracking, post_black)
    return {
        'status': 'completed',
        'idx': idx,
        'baseSnapshot': base,
        'snapshot': results,
        'outcomes': _merge_outcomes(got, clean_miss, net_err, revived, results, brand_lookup),
        'counters': {'got': len(got) + len(revived),
                     'cleanMiss': len(clean_miss), 'netErrors': len(net_err)},
        'blacklistReview': {'quotaIndex': idx, 'reviewed': review_evidence},
        'preState': _state_hashes(pre_tracking, pre_black),
        'postState': _state_hashes(post_tracking, post_black),
        'effects': effects,
    }


def blacklist_review_slice(idx, blacklist=None):
    """黑名單復核 quota 切片（同 workload／cap 計數共用同一定義）。"""
    black = sorted(blacklist if blacklist is not None else load_blacklist())
    if not black:
        return []
    chunks = max(1, (len(black) + BLACKLIST_REVIEW_QUOTA - 1) // BLACKLIST_REVIEW_QUOTA)
    start = (idx % chunks) * BLACKLIST_REVIEW_QUOTA
    return black[start:start + BLACKLIST_REVIEW_QUOTA]


def stage_workload(meta=None):
    """本 stage 實際會處理嘅型號（batch + review）；cap = 1 + 2×queuedModels。

    batch 切片先 `filter_active` 再 `get_batch_todo`，同 run_price_batch 完全一致；
    review 切片用同一 quota 定義，避免 runner 同 fetch 兩套計數。
    """
    meta = load_meta() if meta is None else meta
    brand_lookup = load_brand_lookup()
    todo_src, _ = filter_active(load_models(), key_of=lambda m: canonical_model_key(
        brand_lookup.get(norm_model(m)) or 'UNKNOWN', m))
    batch = get_batch_todo(todo_src, meta)
    if not batch:
        return None
    todo, idx, total = batch
    review = blacklist_review_slice(idx)
    return {'batch': todo, 'review': review, 'idx': idx, 'total': total,
            'queuedModels': len(todo) + len(review)}


def force_workload(limit=None):
    """Force 實際會處理嘅型號（batch + review）；限制同 run_force_batch 一致。"""
    brand_lookup = load_brand_lookup()
    todo, _ = filter_active(load_models(), key_of=lambda m: canonical_model_key(
        brand_lookup.get(norm_model(m)) or 'UNKNOWN', m))
    if limit:
        todo = todo[:limit]
    black = sorted(load_blacklist())
    if limit:
        black = black[:limit]
    review = black[:BLACKLIST_REVIEW_QUOTA]
    return {'batch': todo, 'review': review, 'idx': 0, 'total': len(todo),
            'queuedModels': len(todo) + len(review)}


def _run_blacklist_review(idx, results, *, tracking, blacklist, brand_lookup,
                          review_todo=None, should_abort=None):
    """黑名單復核（staged、零寫檔）：查到有價 → plan 復活並加入 results。

    - `review_todo` 由 workload 預先固定，確保 request cap 同實際查詢一致；
    - incremental submit＋`should_abort`：lease lost 後停止新排隊、cancel 未開始 futures；
    - `limitReached` 反映 limiter sticky 狀態，caller 必須 fail-closed；
    - 回傳 {'results','tracking','blacklist','reviewed','revived','confirmed',
      'netErrors','aborted','limitReached'}；唔會呼叫 plan_revive 以外的任何寫入。
    """
    evidence = []
    revived, confirmed, blk_err = [], [], []
    aborted = False
    if review_todo is None:
        review_todo = blacklist_review_slice(idx, blacklist)
    todo = [k for k in review_todo if k in blacklist]
    if not todo:
        return {'results': results, 'tracking': tracking, 'blacklist': blacklist,
                'reviewed': evidence, 'revived': revived, 'confirmed': confirmed,
                'netErrors': blk_err, 'aborted': False, 'limitReached': False}
    print(f'\n🔎 黑名單復核（批次 {idx + 1}）：{len(todo)} 個型號確認「不再賣」狀態...')
    consec_fail = 0
    ex = ThreadPoolExecutor(max_workers=2)
    pending = {}
    todo_iter = iter(todo)

    def _submit_next():
        try:
            key = next(todo_iter)
        except StopIteration:
            return False
        pending[ex.submit(_search_tri_state, key.split('|', 1)[1])] = key
        return True

    try:
        for _ in range(2):
            _submit_next()
        while pending:
            if should_abort is not None and should_abort():
                aborted = True
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                key = pending.pop(fut)
                try:
                    model, result, ok = fut.result()
                except Exception:
                    model, result, ok = key.split('|', 1)[1], None, False
                if result:
                    results[model] = result
                    tracking, blacklist, was = plan_revive(
                        key, tracking=tracking, blacklist=blacklist)
                    if was:
                        revived.append(key)
                    evidence.append({'key': key, 'model': model, 'result': 'revived',
                                     'price': result})
                    consec_fail = 0
                elif ok:
                    confirmed.append(key)
                    evidence.append({'key': key, 'model': model, 'result': 'confirmed'})
                    consec_fail = 0
                else:
                    blk_err.append(key)
                    evidence.append({'key': key, 'model': model, 'result': 'net_error'})
                    consec_fail += 1
                if consec_fail >= 12:
                    print(f'  ⏳ 復核連續 {consec_fail} 個失敗：全局冷卻 90s 再繼續', flush=True)
                    _global_cooldown(90)
                    consec_fail = 0
            if aborted:
                break
            while len(pending) < 2:
                if should_abort is not None and should_abort():
                    aborted = True
                    break
                if not _submit_next():
                    break
            if aborted:
                break
    finally:
        # wait=True：確保所有已啟動 worker 完成／abort 後才返回，runner 才可以安全清除
        # limiter／abort context；cancel_futures 取消未開始 futures（worker 生命週期契約）。
        ex.shutdown(wait=True, cancel_futures=True)
    limit_reached = bool(_REQUEST_LIMITER is not None and _REQUEST_LIMITER.reached)
    print(f'🔎 復核完成：♻️ 復活 {len(revived)} ｜ ✅ 確認不再賣 {len(confirmed)} ｜ ⚠️ 網絡錯誤 {len(blk_err)}'
          f'{" ｜ ⏹️ aborted" if aborted else ""}'
          f'{" ｜ ⛔ limit reached" if limit_reached else ""}')
    if revived:
        print(f'  ♻️ 復活清單（前 30）：{revived[:30]}')
    return {'results': results, 'tracking': tracking, 'blacklist': blacklist,
            'reviewed': evidence, 'revived': revived, 'confirmed': confirmed,
            'netErrors': blk_err, 'aborted': aborted, 'limitReached': limit_reached}


def review_blacklist_batch(idx):
    """黑名單復核（staged 唯讀入口；唔寫任何本地檔）。"""
    return _run_blacklist_review(
        idx, _load_snapshot_readonly(), tracking=_read_tracking(),
        blacklist=dict(load_blacklist()), brand_lookup=load_brand_lookup())


def run_force_batch(limit=None, *, smoke=True, should_abort=None, exit_on_fail=True):
    """一次性強行全量批次（受 coordinator 約束嘅 force intent）：唔分 7 日。

    P0 交易語義：網絡階段零本地寫入（唔再寫 biggo_prices.json／prices_meta.json／
    model_blacklist.json／model_status.json）。效果全部經 `plan_*` 純函數回傳，
    由 runner 在 coordinator CAS completed 之後才 apply。
    """
    if smoke and not run_smoke():
        print('❌ 強行批次中止：smoke 唔過（BigGo API 對當前 IP 唔友好）')
        if exit_on_fail:
            sys.exit(1)
        return {'status': 'smoke-failed'}

    workload = force_workload(limit)
    brand_lookup = load_brand_lookup()
    todo = workload['batch']
    print(f'🚀 強行全量批次開始：{len(todo)} 個型號'
          f'（review {len(workload["review"])} 個）')

    base = _load_snapshot_readonly()
    results = dict(base)
    pre_tracking = _read_tracking()
    pre_black = dict(load_blacklist())
    protected = protected_models()
    got, clean_miss, net_err = [], [], []
    consec_fail = 0
    t0 = time.time()

    aborted = False
    ex = ThreadPoolExecutor(max_workers=2)
    pending = {}
    todo_iter = iter(todo)

    def _submit_next():
        try:
            m = next(todo_iter)
        except StopIteration:
            return False
        pending[ex.submit(_search_tri_state, m)] = m
        return True

    try:
        for _ in range(2):
            _submit_next()
        done_count = 0
        while pending:
            if should_abort is not None and should_abort():
                print('⚠️ coordinator lease 已失效：中止 force batch（唔會聲稱完成）', flush=True)
                aborted = True
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                model = pending.pop(fut)
                try:
                    _model, result, ok = fut.result()
                except Exception:
                    result, ok = None, False
                if result:
                    results[model] = result
                    got.append(model)
                    consec_fail = 0
                elif ok:
                    clean_miss.append(model)
                    consec_fail = 0
                else:
                    net_err.append(model)
                    consec_fail += 1
                done_count += 1
                if done_count % 25 == 0:
                    el = time.time() - t0
                    print(f'  進度 {done_count}/{len(todo)}（得價 {len(got)} · 無報價 {len(clean_miss)} · 錯誤 {len(net_err)}）· {el:.0f}s', flush=True)
                if consec_fail >= 12 and done_count < len(todo):
                    print(f'  ⏳ 連續 {consec_fail} 個失敗：全局冷卻 90s 再繼續', flush=True)
                    _global_cooldown(90)
                    consec_fail = 0
                if done_count >= 40 and consec_fail >= 40:
                    print('⚠️ 連續 40 個網絡錯誤，疑似被限流，中止本批', flush=True)
                    aborted = True
                    break
            if aborted:
                break
            while len(pending) < 2:
                if should_abort is not None and should_abort():
                    aborted = True
                    break
                if not _submit_next():
                    break
            if aborted:
                break
    finally:
        # wait=True：確保所有已啟動 worker 完成／abort 後才返回，runner 才可以安全清除
        # limiter／abort context；cancel_futures 取消未開始 futures（worker 生命週期契約）。
        ex.shutdown(wait=True, cancel_futures=True)

    if aborted:
        print(f'  （中止前已得價 {len(got)} · 無報價 {len(clean_miss)} · 錯誤 {len(net_err)}）', flush=True)
        if exit_on_fail:
            sys.exit(1)
        return _partial_payload('aborted', base, results, got, clean_miss, net_err,
                                brand_lookup, projectCooldown=True)

    batch_id = 'force-' + time.strftime('%Y%m%d%H%M%S')
    rec = [(m, True) for m in got] + [(m, False) for m in clean_miss]
    post_tracking, post_black = plan_record_results(
        rec, protected=protected, batch_id=batch_id, brand_of=_brand_of(brand_lookup),
        tracking=pre_tracking, blacklist=pre_black)
    review = _run_blacklist_review(0, results, tracking=post_tracking,
                                   blacklist=post_black, brand_lookup=brand_lookup,
                                   review_todo=workload['review'],
                                   should_abort=should_abort)
    if review['aborted']:
        print('⚠️ 黑名單復核因 abort 中止：唔會當完成')
        if exit_on_fail:
            sys.exit(1)
        return _partial_payload('aborted', base, results, got, clean_miss, net_err,
                                brand_lookup, leaseLost=True, projectCooldown=True)
    if review['limitReached']:
        print('⛔ 黑名單復核到 request hard cap：唔會當完成')
        if exit_on_fail:
            sys.exit(1)
        return _partial_payload('review-limit-reached', base, results, got, clean_miss,
                                net_err, brand_lookup,
                                reviewNetErrors=len(review['netErrors']))
    payload = _completed_payload(
        idx=0, base=base, results=review['results'], got=got, clean_miss=clean_miss,
        net_err=net_err, revived=review['revived'], review_evidence=review['reviewed'],
        pre_tracking=pre_tracking, pre_black=pre_black,
        post_tracking=review['tracking'], post_black=review['blacklist'],
        brand_lookup=brand_lookup)
    payload['metaFields'] = {'last_force_batch': time.strftime('%Y-%m-%d %H:%M:%S')}
    el = time.time() - t0
    print(f'\n🏁 強行全量批次完成（{el:.0f}s）')
    print(f'  ✅ 得價：{len(got)} ｜ 📭 乾淨無報價：{len(clean_miss)} ｜ ⚠️ 網絡錯誤：{len(net_err)}')
    return payload


def run_price_batch(should_abort=None):
    """執行當日 BigGo 價錢批次（每月一次、分 7 日；切片由 batch_utils 共用）。

    P0 交易語義：網絡階段**零本地寫入**；只有 coordinator CAS completed 之後，
    runner 才由回傳的 stage payload apply 本地檔。aborted／partial 一律唔會 apply。
    """
    meta = load_meta()
    workload = stage_workload(meta)
    if not workload:
        print('💰 BigGo 批次：唔喺進行中，跳過')
        return {'status': 'not-active'}
    todo, idx, total = workload['batch'], workload['idx'], workload['total']
    brand_lookup = load_brand_lookup()
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return {'status': 'cooldown-skip'}

    print(f'💰 BigGo 批次 {idx + 1}/{PRICE_BATCH_DAYS}：{len(todo)}/{total} 個型號，開始...')
    base = _load_snapshot_readonly()
    results = dict(base)
    pre_tracking = _read_tracking()
    pre_black = dict(load_blacklist())

    got, clean_miss, net_err = [], [], []
    consec_fail = 0
    aborted = False
    lease_lost = False
    t0 = time.time()
    ex = ThreadPoolExecutor(max_workers=2)
    pending = {}
    todo_iter = iter(todo)

    def _submit_next():
        try:
            m = next(todo_iter)
        except StopIteration:
            return False
        pending[ex.submit(_search_tri_state, m)] = m
        return True

    try:
        for _ in range(2):
            _submit_next()
        done_count = 0
        while pending:
            if should_abort is not None and should_abort():
                print('⚠️ coordinator lease 已失效：中止本批（唔會推進進度）', flush=True)
                aborted = True
                lease_lost = True
                break
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                m = pending.pop(fut)
                try:
                    model, result, ok = fut.result()
                except Exception:
                    result, ok = None, False
                if result:
                    results[m] = result
                    got.append(m)
                    consec_fail = 0
                elif ok:
                    clean_miss.append(m)
                    consec_fail = 0
                else:
                    net_err.append(m)
                    consec_fail += 1
                done_count += 1
                if done_count % 50 == 0:
                    el = time.time() - t0
                    print(f'  進度 {done_count}/{len(todo)}（得價 {len(got)} · 無報價 {len(clean_miss)} · 錯誤 {len(net_err)}）· {el:.0f}s', flush=True)
                if done_count >= 40 and consec_fail >= 40:
                    print('⚠️ 連續 40 個失敗，疑似被限流，中止本批', flush=True)
                    aborted = True
                    break
            if aborted:
                break
            while len(pending) < 2:
                if should_abort is not None and should_abort():
                    aborted = True
                    lease_lost = True
                    break
                if not _submit_next():
                    break
            if aborted:
                break
    finally:
        # wait=True：確保所有已啟動 worker 完成／abort 後才返回，runner 才可以安全清除
        # limiter／abort context；cancel_futures 取消未開始 futures（worker 生命週期契約）。
        ex.shutdown(wait=True, cancel_futures=True)

    if aborted:
        print(f'❌ 本批中止（網絡／限流）：idx 未推進，聽日重試；得價 {len(got)} 只作 staged 證據')
        return _partial_payload('aborted', base, results, got, clean_miss, net_err,
                                brand_lookup, idx=idx, leaseLost=lease_lost,
                                projectCooldown=True)

    if net_err:
        print(f'⚠️ 本批有 {len(net_err)} 個網絡錯誤：批次進度唔推進，聽日重試同一 slice'
              f'（得價 {len(got)} · 乾淨無報價 {len(clean_miss)}；錯誤樣本 {net_err[:10]}）')
        return _partial_payload('partial-net-errors', base, results, got, clean_miss,
                                net_err, brand_lookup, idx=idx)

    # 淘汰確認（三態；同一 batch_id 重跑唔重複計 miss）——純函數，唔寫檔
    batch_id = f"{meta.get('price_batch_start', time.strftime('%Y-%m-%d'))}:{idx + 1}/{PRICE_BATCH_DAYS}"
    rec = [(m, True) for m in got] + [(m, False) for m in clean_miss]
    post_tracking, post_black = plan_record_results(
        rec, protected=protected_models(), batch_id=batch_id,
        brand_of=_brand_of(brand_lookup), tracking=pre_tracking, blacklist=pre_black)

    # 小額黑名單復核（只在完整成功批次做；staged、零寫入）
    review = _run_blacklist_review(idx, results, tracking=post_tracking,
                                   blacklist=post_black, brand_lookup=brand_lookup,
                                   review_todo=workload['review'],
                                   should_abort=should_abort)
    if review['aborted']:
        print('⚠️ 黑名單復核因 abort 中止：唔會當完成，唔會 publish／apply')
        return _partial_payload('aborted', base, results, got, clean_miss, net_err,
                                brand_lookup, idx=idx, leaseLost=True,
                                projectCooldown=True)
    if review['limitReached']:
        print('⛔ 黑名單復核到 request hard cap：唔會當完成，唔會 publish／apply')
        return _partial_payload('review-limit-reached', base, results, got, clean_miss,
                                net_err, brand_lookup, idx=idx,
                                reviewNetErrors=len(review['netErrors']))

    return _completed_payload(
        idx=idx, base=base, results=review['results'], got=got, clean_miss=clean_miss,
        net_err=net_err, revived=review['revived'], review_evidence=review['reviewed'],
        pre_tracking=pre_tracking, pre_black=pre_black,
        post_tracking=review['tracking'], post_black=review['blacklist'],
        brand_lookup=brand_lookup)


# ===== BigGo smoke 候選（集中管理；只喺 API 連線測試用）=====
# 選取理由（全部由本地資料核實，唔需要真實 API）：
#   - 核心 29 型號（fetch_biggo.protected_models() 受保護，唔會被自動淘汰）；
#   - biggo_prices.json 本地快照有可靠報價（2026-08-26 全量復核）；
#   - 跨 3 個品牌（HITACHI／CARRIER／RASONIC），避免單一品牌搜尋異常就判死；
#   - 順序探測，首個有價即通過（正常情況只消耗 1 次 API 請求）。
SMOKE_CANDIDATES = (
    {'model': 'RA-10RF', 'brand': 'HITACHI 日立',
     'reason': '核心 29／受保護；長期熱門窗口機；本地快照 $2,500-3,680（多商戶）'},
    {'model': 'CHK12BE', 'brand': 'Carrier 開利',
     'reason': '核心 29／受保護；跨品牌；本地快照 $1,750-3,580（多商戶區間）'},
    {'model': 'RC-XG12', 'brand': 'Rasonic 樂信',
     'reason': '核心 29／受保護；跨品牌；本地快照 $3,978 起'},
)


# smoke 有界連線測試：單次 attempt，每個網絡階段（token／search）約 8 秒 socket timeout；
# 唔會等 60／90 秒冷卻，亦唔會 retry／硬碰。目標係失敗時明顯受限，唔拖住每日 workflow。
SMOKE_TIMEOUT = 8


def _smoke_probe(model):
    """Smoke 探測：回 (status, result)
      'priced'      → API 正常且有匹配報價
      'no-price'    → API 正常回覆但呢個型號暫時無匹配報價（唔等於限流）
      'unreachable' → 網絡／限流／認證等錯誤嘅粗分類

    有界語義：單次 attempt、timeout=SMOKE_TIMEOUT（約 8 秒）、唔等全局冷卻、
    唔限速等待、唔喺錯誤後 sleep；`_get_access_token`／`_api_search` 原本批次
    預設（2 attempts、403／429 唔即刻 retry、冷卻、限速）維持 approved C 語義；
    只有 smoke 傳呢組有界參數。公眾文檔舊「5 次 retry」記述已由 2026-09-28 新條目取代。

    限制（唔虛構）：`_api_search` 目前將 429／403、網絡例外同認證失敗一律回
    reachable=False，所以呢度唔會細分原因，只如實報 'unreachable'；
    若日後需要細分，要先改 `_api_search` 嘅錯誤分類契約。
    """
    data, reachable = _api_search(
        model,
        max_attempts=1,
        timeout=SMOKE_TIMEOUT,
        use_cooldown=False,
        use_pace=False,
        sleep_on_error=False,
    )
    if not reachable:
        return 'unreachable', None
    result = _extract_price(data, model)
    return ('priced', result) if result else ('no-price', None)


def run_smoke(candidates=None):
    """連線煙霧測試：approved design C 規定**最多一個 product-search request**。

    只探測第一個候選（預設 `SMOKE_CANDIDATES[0]`），單次 attempt、約 8 秒 timeout、
    唔等冷卻／唔重試：
    - 'priced'    → True（正常有價）
    - 'no-price'  → False（API 正常但暫時無匹配報價；唔會試其餘候選，唔濫用額度）
    - 'unreachable'／例外 → False
    工作流收到 False 會安全跳過本批（保留快照）；實際呼叫計數由 `REQUEST_STATS` 記錄。
    """
    cands = SMOKE_CANDIDATES if candidates is None else candidates
    if not cands:
        return False
    cand = cands[0]
    model = cand['model'] if isinstance(cand, dict) else str(cand)
    try:
        status, result = _smoke_probe(model)
    except Exception as e:
        # 只記錄例外類型，唔輸出 exception 內容（避免任何 credential 落入 log）
        print(f'  ⚠️ smoke 候選 {model} 例外：{type(e).__name__}'
              f'（網絡／憑證等，未細分）；立即結束 smoke', flush=True)
        return False
    if status == 'priced':
        print(f'✅ BigGo smoke test 通過：{model} → {result["price"]}'
              f'（{result.get("merchants", 0)} 商戶）', flush=True)
        return True
    if status == 'no-price':
        print(f'  ⚠️ smoke 候選 {model}：API 正常但暫時無匹配報價；'
              f'single-request cap，唔會再試其餘候選', flush=True)
        return False
    print(f'  ⚠️ smoke 候選 {model}：unreachable'
          f'（網絡／限流／認證等，現行錯誤分類未細分）；立即結束 smoke、唔硬碰', flush=True)
    return False


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    # Repair #1：所有會共用 BigGo 憑證嘅 CLI 網絡入口都必須經 coordinator runner
    # （scripts/biggo_stage_runner.py）取 lease；呢度一律 fail closed，冇繞過路徑。
    if any(arg in ('--smoke', '--price-batch', '--force-batch') for arg in sys.argv[1:]):
        print('❌ BigGo 網絡動作必須經 coordinator lease：'
              'python scripts/biggo_stage_runner.py\n'
              '   force intent 用環境變數 AIRCON_BIGGO_FORCE_STAGE=1；'
              '本 CLI 唔提供繞過 lease 嘅路徑（fail closed）。', file=sys.stderr)
        sys.exit(2)
    if len(sys.argv) > 1:
        print('❌ 單型號查詢同樣共用 BigGo 憑證，必須經 coordinator runner；'
              '本 CLI 唔提供繞過 lease 嘅路徑（fail closed）。', file=sys.stderr)
        sys.exit(2)
    print('用法：python scripts/biggo_stage_runner.py（coordinator-gated）；'
          '本模組函式由 runner import 使用，唔可以直接做網絡動作。')
