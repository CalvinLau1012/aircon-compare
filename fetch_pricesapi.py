#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PricesAPI 香港格價快照抓取（核心 29 型號驗收 + 後備）
- 主力價錢源已用返 BigGo（fetch_biggo.py）；PricesAPI 只做細規模核心 29 驗收/後備
- 端點：GET https://api.pricesapi.io/api/v1/products/search
  參數：q=型號、country=hk、limit=5、offers_limit=20
  認證：Authorization: Bearer $PRICESAPI_API_KEY
- 免費額度：1,000 calls/月、10 req/min；冷查詢需 30–90s，所以 timeout 用 100s
- `--core`：直接做一次核心 29 型號驗收（唔經批次 meta，適合人手/選用 workflow）
- 批次模式仍共用 batch_utils 嘅 meta，但預設只查核心 29 個型號
輸出：pricesapi_prices.json {型號: {price, merchants, url, updated, source}}
過濾規則：型號精確匹配 + 冷氣關鍵字 + 配件排除 + HKD 報價 + 商戶去重
"""
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from crawl_utils import BOT_UA as UA, load_json, norm_model, load_models, save_json
import batch_utils
import model_lifecycle
from price_utils import currency_code, format_price_range, is_ac_title, norm_title, num_price as _num_price

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
RATE_LIMIT_INTERVAL = _env_float('PRICESAPI_RATE_LIMIT_INTERVAL', 11)  # 官方 10 req/min；11s 間隔（約 5.5/min）更保守

# 免費版每月 1,000 calls；核心 29 驗收 + retry 只佔好少 quota。
# 有需要先設 PRICESAPI_BATCH_LIMIT 調大；0 代表唔設上限。
DEFAULT_BATCH_LIMIT = 29
MAX_BATCH_SECONDS = _env_int('PRICESAPI_BATCH_MAX_SECONDS', 50 * 60)

_request_lock = threading.Lock()
_last_request_start = 0.0


def get_api_key():
    """讀取 API key；只放環境變數，唔可以 commit 入 repo"""
    return os.environ.get('PRICESAPI_API_KEY', '').strip()


def _rate_limit_wait():
    """官方 10 req/min：我哋用 11s 間隔（約 5.5/min）留緩衝"""
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
        # 型號精確匹配 + 冷氣關鍵字 + 配件排除（共用 price_utils 規則）
        if not is_ac_title(title, nm):
            continue
        cand_currency = currency_code(cand.get('currency'))
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
            cur = currency_code(off.get('currency')) or cand_currency
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
    url = next((u for u in urls if u), '')
    if not url:
        url = 'https://www.google.com/search?q=' + urllib.parse.quote(model + ' 價錢')
    return {
        'price': format_price_range(prices),
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
    """核心 29 個型號（models_data.MODELS），每月必定優先更新"""
    try:
        import models_data
        return {norm_model(m.get('model')) for m in models_data.MODELS if m.get('model')}
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
    data = load_json(OUT_PATH, {})
    return data if isinstance(data, dict) else {}


def save_results(results):
    save_json(OUT_PATH, results, indent=2)


def run_price_batch():
    """執行當日 PricesAPI 價錢批次（每月一次、分 7 日，核心型號優先）"""
    key = get_api_key()
    if not key:
        print('❌ 未設定 PRICESAPI_API_KEY 環境變數')
        print('   1. 到 https://pricesapi.io 註冊免費 API key')
        print('   2. GitHub repo 設定 Secrets：PRICESAPI_API_KEY')
        print('   3. 本地測試：export PRICESAPI_API_KEY=pricesapi_xxx')
        return

    meta = batch_utils.load_meta()
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    models = prioritize_models(load_models())
    batch = batch_utils.get_batch_todo(models, meta, limit=get_batch_limit())
    if batch is None:
        print('💰 PricesAPI 批次：唔喺進行中，跳過')
        return
    todo, idx, total = batch
    print(f'💰 PricesAPI 批次 {idx + 1}/{batch_utils.PRICE_BATCH_DAYS}：'
          f'{len(todo)}/{total} 個型號，開始...')

    if not todo:
        idx = batch_utils.PRICE_BATCH_DAYS
        today = time.strftime('%Y-%m-%d')
        meta['price_batch_idx'] = idx
        meta.pop('price_batch_start', None)
        meta['last_run'] = today
        meta['last_full'] = today
        batch_utils.save_meta(meta)
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
            batch_utils.set_cooldown()
            sys.exit(1)

    save_results(results)
    if not completed:
        print('⏰ 本批未完成，唔推進批次；下次同批續跑（已完成嘅唔重查）')
        return

    finished = batch_utils.advance_batch(meta, today=today)
    if finished:
        print(f'🎉 PricesAPI 價錢快照全量更新完成（分 {batch_utils.PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{meta.get("price_batch_idx")}/{batch_utils.PRICE_BATCH_DAYS}），聽日繼續')
    batch_utils.save_meta(meta)


def core_models():
    """核心 29 型號清單（保持 models_data.MODELS 次序）"""
    try:
        import models_data
        return [m['model'] for m in models_data.MODELS if m.get('model')]
    except Exception:
        return []


def run_core_check():
    """PricesAPI 核心 29 型號驗收：唔掂批次 meta，只更新 pricesapi_prices.json"""
    key = get_api_key()
    if not key:
        print('❌ 未設定 PRICESAPI_API_KEY 環境變數')
        return
    models = core_models()
    if not models:
        print('❌ 讀取核心型號清單失敗（models_data.MODELS）')
        return
    print(f'🔎 PricesAPI 核心 {len(models)} 型號驗收，開始...')

    results = load_results()
    today = time.strftime('%Y-%m-%d')
    todo = [m for m in models if not (isinstance(results.get(m), dict) and results[m].get('price')
                                      and results[m].get('updated') == today)]
    todo, skipped = model_lifecycle.filter_active(todo)
    if skipped:
        print(f'🚫 跳過黑名單核心驗收：{len(skipped)} 個')
    if not todo:
        print('✅ 今日核心 29 型號已全部驗收過，唔重複燒 quota')
        return

    ok = 0
    consec_fail = 0
    t0 = time.time()
    for i, m in enumerate(todo, 1):
        if MAX_BATCH_SECONDS and time.time() - t0 > MAX_BATCH_SECONDS:
            print('⏰ 超過時間預算，保存進度後停止')
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
        if i % 5 == 0 or i == len(todo):
            save_results(results)
            print(f'  進度 {i}/{len(todo)}（得價 {ok}）· {time.time() - t0:.0f}s', flush=True)
        if i >= 5 and consec_fail >= 5:
            print('⚠️ 連續 5 個 API 失敗，停止核心驗收（保留已有結果）', flush=True)
            save_results(results)
            return

    save_results(results)
    print(f'✅ PricesAPI 核心驗收完成：{len(todo)} 個查詢，{ok} 個有價 → {OUT_PATH}')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if '--core' in sys.argv:
        run_core_check()
    elif '--price-batch' in sys.argv:
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
