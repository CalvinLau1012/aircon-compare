#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PricesAPI 香港格價快照抓取（取代 BigGo 做主力自動價錢源）
- 端點：GET https://api.pricesapi.io/api/v1/products/search
  參數：q=型號、country=hk、limit=5、offers_limit=20
  認證：Authorization: Bearer $PRICESAPI_API_KEY
- 免費額度：1,000 calls/月、6 req/min；冷查詢需 30–90s，所以 timeout 用 100s
- 每月最多一次、分 7 日分批（批次進度共用 fetch_prices 嘅 meta）
  預設每月只查 395 個型號（核心 29 個優先），留 quota 俾 retry 同快取預熱
輸出：pricesapi_prices.json {型號: {price, merchants, url, updated, source}}
過濾規則：型號精確匹配 + 冷氣關鍵字 + 配件排除 + HKD 報價 + 商戶去重
"""
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from crawl_utils import BOT_UA as UA, norm_model, load_models

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'pricesapi_prices.json')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, ''))
    except (TypeError, ValueError):
        return default


def _env_int(name, default):
    try:
        return int(os.environ.get(name, ''))
    except (TypeError, ValueError):
        return default


API_BASE = os.environ.get('PRICESAPI_API_BASE', 'https://api.pricesapi.io/api/v1').rstrip('/')
SEARCH_PATH = '/products/search'
COUNTRY = os.environ.get('PRICESAPI_COUNTRY', 'hk')
LIMIT = 5            # /products/search 最多回 5 個 candidate
OFFERS_LIMIT = 20    # 每個 candidate 最多 20 個商戶報價（cheapest-first）
TIMEOUT = 100        # 冷查詢官方建議 read timeout >= 95s
MAX_RETRIES = 3
RATE_LIMIT_INTERVAL = _env_float('PRICESAPI_RATE_LIMIT_INTERVAL', 11)  # 免費版 6 req/min，預留緩衝

# 免費版每月 1,000 calls；查 395 個 + retry 會比較穩陣。
# 付費 plan 可設 PRICESAPI_BATCH_LIMIT=0 取消上限（或改大啲）。
DEFAULT_BATCH_LIMIT = 395
MAX_BATCH_SECONDS = _env_int('PRICESAPI_BATCH_MAX_SECONDS', 50 * 60)

# 冷氣相關關鍵字（排除 LoRa/RF 模組、相機配件等撞名產品）
AC_RE = re.compile(
    r'冷氣|空調|air\s*-?\s*con(ditioner)?|窗口機|窗口式|分體機|分體式|流動機|流動式|'
    r'淨冷|制冷|冷暖|定頻|變頻|匹',
    re.I)
# 配件/服務排除（遙控器、濾網、支架、防塵罩等）
ACC_RE = re.compile(
    r'遙控|濾網|過濾|配件|說明書|支架|擋板|防塵|罩|remote|filter|parts?|cover|bracket',
    re.I)

_request_lock = threading.Lock()
_last_request_start = 0.0


def get_api_key():
    """讀取 API key；只放環境變數，唔可以 commit 入 repo"""
    return os.environ.get('PRICESAPI_API_KEY', '').strip()


def norm_title(s):
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def _num_price(p):
    """價錢轉 int；非數值/非正數回 None"""
    try:
        v = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _currency(v):
    return re.sub(r'[^A-Z]', '', str(v or '')).upper()


def _rate_limit_wait():
    """免費版 6 req/min：保證兩次請求起碼相隔 RATE_LIMIT_INTERVAL 秒"""
    global _last_request_start
    with _request_lock:
        wait = _last_request_start + RATE_LIMIT_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_start = time.monotonic()


def _retry_after(e, fallback):
    try:
        return int(e.headers.get('Retry-After') or 0) or fallback
    except Exception:
        return fallback


def _get(query, api_key):
    """呼叫 PricesAPI；回 JSON dict，失敗/限流退避後仍失敗回 None"""
    params = urllib.parse.urlencode({
        'q': query,
        'country': COUNTRY,
        'limit': str(LIMIT),
        'offers_limit': str(OFFERS_LIMIT),
    })
    url = API_BASE + SEARCH_PATH + '?' + params
    for attempt in range(MAX_RETRIES):
        _rate_limit_wait()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Accept': 'application/json',
                'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5',
                'Authorization': 'Bearer ' + api_key,
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read().decode('utf-8', 'ignore')
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            # 401/403/400/404/410 係認證、額度或參數問題，重試冇意思
            if e.code in (400, 401, 403, 404, 410):
                try:
                    body = json.loads(e.read().decode('utf-8', 'ignore'))
                    code = (body.get('error') or {}).get('code')
                    msg = (body.get('error') or {}).get('message')
                except Exception:
                    code, msg = str(e.code), ''
                print(f'  ⚠️ PricesAPI {e.code} {code or ""} {msg or ""}'.strip())
                return None
            wait = _retry_after(e, RATE_LIMIT_INTERVAL * (attempt + 1))
            print(f'  ⚠️ PricesAPI HTTP {e.code}，{"Retry-After" if e.headers.get("Retry-After") else "退避"} {wait:.0f}s')
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                wait = random.uniform(2, 4) * (attempt + 1)
                time.sleep(wait)
    return None


def extract_prices(data, model):
    """
    由 PricesAPI response 抽出一個型號嘅有效 HKD 報價。
    有價回 dict；查得到但無匹配回 False；輸入唔啱/空 response 回 None。
    """
    if not isinstance(data, dict):
        return None
    products = (data.get('data') or {}).get('products') or []
    if not isinstance(products, list):
        return None
    nm = norm_model(model)
    if len(nm) < 4:
        return False

    prices = []
    seen = set()
    urls = []
    for cand in products:
        if not isinstance(cand, dict):
            continue
        title = (cand.get('title') or '').strip()
        # 型號精確匹配 + 冷氣關鍵字，先排除 RF 模組等撞名產品
        if nm not in norm_title(title):
            continue
        if not AC_RE.search(title):
            continue
        if ACC_RE.search(title):
            continue
        cand_currency = _currency(cand.get('currency'))
        source = (cand.get('source') or '').strip()
        offers = cand.get('offers') or []
        if not isinstance(offers, list):
            offers = []

        before = len(prices)
        # 有 offers[] 就用 offers；degraded / offers 全部唔啱時，先用 candidate headline price 頂住
        for off in offers:
            if not isinstance(off, dict):
                continue
            p = _num_price(off.get('price'))
            cur = _currency(off.get('currency')) or cand_currency
            seller = (off.get('seller') or '').strip()
            if p is None or cur != 'HKD' or not seller:
                continue
            key = (seller, p, norm_title(title))
            if key in seen:
                continue
            seen.add(key)
            prices.append(p)
            urls.append(off.get('url') or '')
        if len(prices) == before:
            p = _num_price(cand.get('price'))
            if p is not None and cand_currency == 'HKD' and source:
                key = (source, p, norm_title(title))
                if key not in seen:
                    seen.add(key)
                    prices.append(p)

    if not prices:
        return False
    lo, hi = min(prices), max(prices)
    url = next((u for u in urls if u), '')
    if not url:
        url = 'https://www.google.com/search?q=' + urllib.parse.quote(model + ' 價錢')
    return {
        'price': f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起',
        'merchants': len(prices),
        'url': url,
        'updated': time.strftime('%Y-%m-%d'),
        'source': 'PricesAPI',
    }


def fetch_pricesapi_price(model, api_key=None):
    """搜一個型號。有價回 dict；查得到但無匹配回 False；API/網絡錯誤回 None"""
    key = (api_key or get_api_key()).strip()
    if not key:
        print('❌ 未設定 PRICESAPI_API_KEY 環境變數')
        return None
    nm = norm_model(model)
    if len(nm) < 4:
        return False
    data = _get(model, key)
    if data is None:
        return None
    return extract_prices(data, model)


# 名稱同其他 fetch_*.py 保持一致，方便替換/測試
fetch_price = fetch_pricesapi_price


# ===== 每月分批更新（免費額度內：核心型號優先） =====

def get_batch_limit():
    """每月最多查幾多個型號；0/負數代表唔設上限（付費 plan 用）"""
    raw = os.environ.get('PRICESAPI_BATCH_LIMIT', str(DEFAULT_BATCH_LIMIT))
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_BATCH_LIMIT


def core_model_keys():
    """核心 29 個型號（generate_html.MODELS），每月必定優先更新"""
    try:
        import generate_html
        return {norm_model(m.get('model')) for m in generate_html.MODELS if m.get('model')}
    except Exception:
        return set()


def prioritize_models(models, core_keys=None):
    """核心型號行先；其餘跟 EMSD CSV 次序"""
    core_keys = core_model_keys() if core_keys is None else set(core_keys)
    core, rest = [], []
    for m in models:
        (core if norm_model(m) in core_keys else rest).append(m)
    return core + rest


def load_results():
    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def save_results(results):
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def run_price_batch():
    """執行當日 PricesAPI 價錢批次（每月一次、分 7 日，核心型號優先）"""
    import fetch_prices
    key = get_api_key()
    if not key:
        print('❌ 未設定 PRICESAPI_API_KEY 環境變數')
        print('   1. 到 https://pricesapi.io 註冊免費 API key')
        print('   2. GitHub repo 設定 Secrets：PRICESAPI_API_KEY')
        print('   3. 本地測試：export PRICESAPI_API_KEY=pricesapi_xxx')
        return

    meta = fetch_prices.load_meta()
    idx = meta.get('price_batch_idx', 0)
    if not meta.get('price_batch_start') or idx >= fetch_prices.PRICE_BATCH_DAYS:
        print('💰 PricesAPI 批次：唔喺進行中，跳過')
        return
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    models = prioritize_models(load_models())
    limit = get_batch_limit()
    total = min(len(models), limit) if limit and limit > 0 else len(models)
    day_cap = (total + fetch_prices.PRICE_BATCH_DAYS - 1) // fetch_prices.PRICE_BATCH_DAYS
    start = idx * day_cap
    todo = models[start:min((idx + 1) * day_cap, total)]
    print(f'💰 PricesAPI 批次 {idx + 1}/{fetch_prices.PRICE_BATCH_DAYS}：'
          f'{len(todo)} 個型號（每月上限 {total} 個），開始...')

    if not todo:
        idx = fetch_prices.PRICE_BATCH_DAYS
        today = time.strftime('%Y-%m-%d')
        meta['price_batch_idx'] = idx
        meta.pop('price_batch_start', None)
        meta['last_run'] = today
        meta['last_full'] = today
        fetch_prices.save_meta(meta)
        print('🎉 PricesAPI 價錢快照全量更新完成')
        return

    results = load_results()
    today = time.strftime('%Y-%m-%d')
    batch_start = str(meta.get('price_batch_start') or '')
    # 批次被 workflow timeout 中斷後重跑：本批已經完成嘅型號唔好再燒 quota
    # （只跳過今個月呢一批開始之後嘅成功結果；上個月舊快照照樣會刷新）
    todo = [m for m in todo if not (isinstance(results.get(m), dict) and results[m].get('price')
                                    and str(results[m].get('updated') or '') >= batch_start)]

    ok = 0
    consec_fail = 0
    t0 = time.time()
    completed = True
    for i, m in enumerate(todo, 1):
        elapsed = time.time() - t0
        if MAX_BATCH_SECONDS and elapsed > MAX_BATCH_SECONDS:
            print(f'⏰ 已用 {elapsed:.0f}s，超過本批時間預算，保存進度後下次繼續')
            completed = False
            break
        try:
            result = fetch_pricesapi_price(m, api_key=key)
        except Exception:
            result = None
        if result:
            results[m] = result
            ok += 1
        if result is None:
            consec_fail += 1
        else:
            consec_fail = 0
        if i % 10 == 0 or i == len(todo):
            save_results(results)
            el = time.time() - t0
            print(f'  進度 {i}/{len(todo)}（得價 {ok}）· {el:.0f}s', flush=True)
        if i >= 10 and consec_fail >= 10:
            print('⚠️ 連續 10 個 API 失敗，疑似 key/額度/限流問題，中止本批', flush=True)
            save_results(results)
            fetch_prices.set_cooldown()
            sys.exit(1)

    save_results(results)
    if not completed:
        print('⏰ 本批未完成，唔推進批次；下次同批續跑（已完成嘅唔重查）')
        return

    idx += 1
    meta['price_batch_idx'] = idx
    meta['last_run'] = today
    if idx >= fetch_prices.PRICE_BATCH_DAYS:
        meta.pop('price_batch_start', None)
        meta['last_full'] = today
        print(f'🎉 PricesAPI 價錢快照全量更新完成（分 {fetch_prices.PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{idx}/{fetch_prices.PRICE_BATCH_DAYS}），聽日繼續')
    fetch_prices.save_meta(meta)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if '--price-batch' in sys.argv:
        run_price_batch()
    elif len(sys.argv) > 1:
        # 單型號測試：python fetch_pricesapi.py RA-10RF
        if not get_api_key():
            print('用法：先設定 PRICESAPI_API_KEY 環境變數')
            print('  export PRICESAPI_API_KEY=pricesapi_xxx')
            print('然後：python fetch_pricesapi.py RA-10RF')
            sys.exit(1)
        for m in sys.argv[1:]:
            print(m, '→', fetch_pricesapi_price(m))
    else:
        print('用法：python fetch_pricesapi.py --price-batch  或  python fetch_pricesapi.py <型號>')
