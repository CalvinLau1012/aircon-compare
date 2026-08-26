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
- batch_utils：prices_meta 讀寫、批次切片 get_batch_todo / 推進 advance_batch
"""
import base64
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawl_utils import norm_model, load_models
from price_utils import num_price as _num_price, is_ac_title
from batch_utils import (PRICE_BATCH_DAYS, load_meta, save_meta, set_cooldown,
                         get_batch_todo, advance_batch)
from model_lifecycle import load_blacklist, filter_active, revive_model

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'biggo_prices.json')

# 官方 JSON API（product search 需登入認證；2026-08-26 起免登入通道已關閉，見 docs/DECISIONS.md D10）
API_URL = 'https://api.biggo.com/api/v1/spa/search/{q}/product'
AUTH_URL = 'https://api.biggo.com/auth/v1/token'
API_HEADERS = {'Content-Type': 'application/json', 'site': 'biggo.hk', 'region': 'hk'}
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')

# access_token 快取（client credentials；55 分鐘 TTL，token 一般 60 分鐘有效）
_TOKEN = {'value': None, 'expires': 0.0}


def _get_access_token():
    """用 BIGGO_CLIENT_ID/SECRET 攞 access_token（免費官方認證；冇配置就 None = 免登入 fallback）"""
    cid = os.environ.get('BIGGO_CLIENT_ID', '').strip()
    csec = os.environ.get('BIGGO_CLIENT_SECRET', '').strip()
    if not cid or not csec:
        return None
    now = time.time()
    if _TOKEN['value'] and now < _TOKEN['expires']:
        return _TOKEN['value']
    cred = base64.b64encode(f'{cid}:{csec}'.encode()).decode()
    data = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
    req = urllib.request.Request(AUTH_URL, data=data, headers={
        'Authorization': f'Basic {cred}',
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': UA,
    })
    tok = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8')).get('access_token')
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
    """喺發請求前等待全局冷卻結束"""
    with _LOCK:
        remain = _COOLDOWN_UNTIL - time.time()
    while remain > 0:
        time.sleep(min(remain, 5))
        with _LOCK:
            remain = _COOLDOWN_UNTIL - time.time()


def _wait_pace():
    """全局最小請求間隔（主動限速，避免觸發 API rate limit）"""
    global _NEXT_SLOT
    with _LOCK:
        wait = _NEXT_SLOT - time.time()
        _NEXT_SLOT = max(time.time(), _NEXT_SLOT) + MIN_PACE
    if wait > 0:
        time.sleep(wait)


def _api_search(model, jitter=(0.2, 0.6)):
    """官方 API 搜尋：回 (data, reachable)
    reachable=True  → API 有正常回覆（data 可能係空結果 = 乾淨無匹配）
    reachable=False → 網絡/限流錯誤（唔計入淘汰統計）
    """
    for attempt in range(5):
        try:
            _wait_cooldown()
            _wait_pace()
            headers = {'User-Agent': UA, **API_HEADERS, 'Accept': 'application/json'}
            token = _get_access_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
            req = urllib.request.Request(API_URL.format(q=urllib.parse.quote(model, safe='')), headers=headers)
            data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode('utf-8', 'ignore'))
            return data, True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # require_login（免登入通道關閉）唔係冷卻問題；有 token 就照舊冷卻重試
                wait = int(e.headers.get('Retry-After') or 0) or 60
                print(f'  ⏳ 429 限流：全局冷卻 {wait}s 後重試（{model}，第 {attempt + 1} 次）', flush=True)
                _global_cooldown(wait)
            elif e.code in (403,):
                _global_cooldown(60)
            else:
                time.sleep(5 * (attempt + 1))
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None, False


def fetch_biggo_price(model):
    """官方 API 搜一個型號，回傳 {price, merchants, url} 或 None"""
    data, _reachable = _api_search(model)
    if not data:
        return None
    nm = norm_model(model)
    prices = []
    for it in data.get('list', []):
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
            prices.append(p)
    if not prices:
        return None
    lo, hi = min(prices), max(prices)
    price = f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起'
    return {
        'price': price,
        'merchants': len(prices),
        'url': 'https://biggo.hk/s/?q=' + urllib.parse.quote(model),
        'updated': time.strftime('%Y-%m-%d'),
    }


def protected_models():
    """受保護型號：核心 29 + 有官方網店價型號（唔會自動淘汰，治理要求）"""
    protected = set()
    try:
        from models_data import MODELS
        for m in MODELS:
            protected.add(norm_model(m))
    except Exception:
        pass
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
                    protected.add(norm_model(k))
        except Exception:
            pass
    return protected


def run_force_batch(limit=None):
    """一次性強行全量批次（測試用）：唔分 7 日，一次過查晒全部非黑名單型號
    - 淘汰確認：乾淨無報價計 misses（閾值 2 先自動黑名單）；網絡錯誤唔計
    - 核心 29 + 官方網店價型號受保護，唔會淘汰
    - 唔推進每月批次進度；只記 meta['last_force_batch'] 審計痕跡
    """
    if not run_smoke():
        print('❌ 強行批次中止：smoke 唔過（BigGo API 對當前 IP 唔友好）')
        sys.exit(1)

    from model_lifecycle import record_results
    all_models = load_models()
    todo, skipped = filter_active(all_models)
    if limit:
        todo = todo[:limit]
    print(f'🚀 強行全量批次開始：{len(todo)} 個型號（已排除黑名單 {len(skipped)} 個）')

    results = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            results = json.load(f)

    protected = protected_models()
    before_black = set(load_blacklist())
    got = []        # 有價
    clean_miss = []  # API 正常但乾淨無匹配
    net_err = []    # 網絡/限流錯誤
    consec_fail = 0
    t0 = time.time()

    def _one(model):
        data, reachable = _api_search(model)
        if not reachable:
            return model, None, False
        if not data:
            return model, None, True
        nm = norm_model(model)
        prices = []
        for it in data.get('list', []):
            title = (it.get('title') or '').strip()
            if len(nm) < 4 or not is_ac_title(title, nm):
                continue
            nindex = it.get('nindex') or ''
            if not nindex.startswith('hk_'):
                continue
            p = _num_price(it.get('price'))
            if p:
                prices.append(p)
        if not prices:
            return model, None, True
        lo, hi = min(prices), max(prices)
        price = f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起'
        return model, {'price': price, 'merchants': len(prices),
                       'url': 'https://biggo.hk/s/?q=' + urllib.parse.quote(model),
                       'updated': time.strftime('%Y-%m-%d')}, True

    aborted = False
    ex = ThreadPoolExecutor(max_workers=2)
    try:
        futures = {ex.submit(_one, m): m for m in todo}
        done_count = 0
        for fut in as_completed(futures):
            model, result, ok = fut.result()
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
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)
            # 連續失敗就全局冷卻 90s 再繼續；要 40 連錯先中止（冷卻後仍全錯 = 真係唔友好）
            if consec_fail >= 12 and done_count < len(todo):
                print(f'  ⏳ 連續 {consec_fail} 個失敗：全局冷卻 90s 再繼續', flush=True)
                _global_cooldown(90)
                consec_fail = 0
            if done_count >= 40 and consec_fail >= 40:
                print('⚠️ 連續 40 個網絡錯誤，疑似被限流，中止本批', flush=True)
                set_cooldown()
                aborted = True
                break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    if aborted:
        print(f'  （中止前已得價 {len(got)} · 無報價 {len(clean_miss)} · 錯誤 {len(net_err)}）', flush=True)
        sys.exit(1)

    # 淘汰確認（閾值 2；網絡錯誤唔計；受保護唔淘汰）
    rec = [(m, True) for m in got] + [(m, False) for m in clean_miss]
    record_results(rec, protected=protected)
    after_black = set(load_blacklist())
    new_black = sorted(after_black - before_black)
    el = time.time() - t0
    print(f'\n🏁 強行全量批次完成（{el:.0f}s）')
    print(f'  ✅ 得價：{len(got)} ｜ 📭 乾淨無報價：{len(clean_miss)} ｜ ⚠️ 網絡錯誤：{len(net_err)}')
    if new_black:
        print(f'  🚫 新自動淘汰：{len(new_black)} 個 — {new_black}')
    else:
        print('  🚫 新自動淘汰：0 個')
    protected_miss = [m for m in clean_miss if norm_model(m) in protected]
    if protected_miss:
        print(f'  🛡 受保護而唔淘汰（無報價）：{len(protected_miss)} 個 — {protected_miss[:20]}')
    if clean_miss:
        print(f'  📭 無報價樣本（前 20）：{clean_miss[:20]}')
    if net_err:
        print(f'  ⚠️ 網絡錯誤樣本（前 10）：{net_err[:10]}')

    # ===== 黑名單復核：確認「不再賣」狀態（API 查到有價 → 復活） =====
    black = sorted(load_blacklist())
    if limit:
        black = black[:limit]
    if black:
        print(f'\n🔎 黑名單復核開始：{len(black)} 個型號確認「不再賣」狀態...')
        revived, confirmed, blk_err = [], [], []
        consec_fail = 0
        ex = ThreadPoolExecutor(max_workers=2)
        try:
            futures = {ex.submit(_one, m): m for m in black}
            done = 0
            for fut in as_completed(futures):
                model, result, ok = fut.result()
                if result:
                    results[model] = result
                    revive_model(model)
                    revived.append(model)
                    consec_fail = 0
                elif ok:
                    confirmed.append(model)
                    consec_fail = 0
                else:
                    blk_err.append(model)
                    consec_fail += 1
                done += 1
                if done % 100 == 0:
                    print(f'  復核 {done}/{len(black)}（復活 {len(revived)} · 確認不再賣 {len(confirmed)} · 錯誤 {len(blk_err)}）', flush=True)
                    with open(OUT_PATH, 'w', encoding='utf-8') as f:
                        json.dump(results, f, ensure_ascii=False)
                # 連續失敗就全局冷卻 90s 再繼續（復核可以慢慢嚟）
                if consec_fail >= 12 and done < len(black):
                    print(f'  ⏳ 復核連續 {consec_fail} 個失敗：全局冷卻 90s 再繼續', flush=True)
                    _global_cooldown(90)
                    consec_fail = 0
                if done >= 40 and consec_fail >= 40:
                    print('⚠️ 復核階段連續 40 個網絡錯誤，中止復核（已確認嘅結果保留）', flush=True)
                    break
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        with open(OUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False)
        print(f'\n🔎 黑名單復核完成：♻️ 復活 {len(revived)} ｜ ✅ 確認不再賣 {len(confirmed)} ｜ ⚠️ 網絡錯誤 {len(blk_err)}')
        if revived:
            print(f'  ♻️ 復活清單（前 30）：{revived[:30]}')
        if confirmed:
            print(f'  ✅ 確認樣本（前 10）：{confirmed[:10]}')

    meta = load_meta()
    meta['last_force_batch'] = time.strftime('%Y-%m-%d %H:%M:%S')
    save_meta(meta)
    return True


def run_price_batch():
    """執行當日 BigGo 價錢批次（每月一次、分 7 日；切片/推進由 batch_utils 共用）"""
    meta = load_meta()
    batch = get_batch_todo(load_models(), meta)
    if not batch:
        print('💰 BigGo 批次：唔喺進行中，跳過')
        return
    todo, idx, total = batch
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    print(f'💰 BigGo 批次 {idx + 1}/{PRICE_BATCH_DAYS}：{len(todo)}/{total} 個型號，開始...')

    results = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            results = json.load(f)

    ok = 0
    consec_fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_biggo_price, m): m for m in todo}
        done_count = 0
        for fut in as_completed(futures):
            m = futures[fut]
            try:
                result = fut.result()
            except Exception:
                result = None
            if result:
                results[m] = result
                ok += 1
                consec_fail = 0
            else:
                consec_fail += 1
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· {el:.0f}s', flush=True)
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)
            if done_count >= 40 and consec_fail >= 40:
                print('⚠️ 連續 40 個失敗，疑似被限流，中止本批', flush=True)
                set_cooldown()
                sys.exit(1)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    done = advance_batch(meta)
    save_meta(meta)
    if done:
        print(f'🎉 BigGo 價錢快照全量更新完成（分 {PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{meta["price_batch_idx"]}/{PRICE_BATCH_DAYS}），聽日繼續')


def run_smoke():
    """連線煙霧測試：抓一個熱門型號確認 BigGo 對當前 IP 友好（批次前一定要過）"""
    test = 'RA-10RF'
    try:
        r = fetch_biggo_price(test)
    except Exception:
        r = None
    if r and r.get('price'):
        print(f'✅ BigGo smoke test 通過：{test} → {r["price"]}（{r.get("merchants", 0)} 商戶）')
        return True
    print(f'⚠️ BigGo smoke test 失敗（{test} 攞唔到價）——可能被 GitHub IP 限流，建議跳過本批')
    return False


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if '--smoke' in sys.argv:
        sys.exit(0 if run_smoke() else 1)
    if '--force-batch' in sys.argv:
        i = sys.argv.index('--force-batch')
        lim = None
        if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit():
            lim = int(sys.argv[i + 1])
        run_force_batch(lim)
    elif '--price-batch' in sys.argv:
        run_price_batch()
    elif len(sys.argv) > 1:
        # 單型號測試：python fetch_biggo.py RA-10RF
        for m in sys.argv[1:]:
            print(m, '→', fetch_biggo_price(m))
    else:
        print('用法：python fetch_biggo.py --smoke  /  --price-batch  /  --force-batch [N]  /  <型號>')
