#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BigGo 香港格價（biggo.hk）價錢快照抓取
- 多商戶報價平台（Price.com.hk 性質一致），公開網頁，無 Cloudflare
- 每月最多一次、分 7 日分批（批次進度共用 batch_utils）
輸出：biggo_prices.json {型號: {price: "$X,XXX-YY,YYY", merchants: N, updated: 日期}}

共享工具（同 fetch_pricesapi 一套規則，唔會走樣）：
- crawl_utils：norm_model / load_models
- price_utils：num_price / is_ac_title（冷氣關鍵字 + 配件排除同一套）
- batch_utils：prices_meta 讀寫、批次切片 get_batch_todo / 推進 advance_batch
"""
import json
import os
import random
import re
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

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'biggo_prices.json')

# BigGo 實測用瀏覽器 UA 先至穩定（bot UA 會被唔同對待）
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')


def _get(url):
    """帶抖動+退避嘅 GET"""
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.4, 1.0))
            req = urllib.request.Request(url, headers={
                'User-Agent': UA, 'Accept-Language': 'zh-HK,zh;q=0.9',
                'Referer': 'https://biggo.hk/'})
            return urllib.request.urlopen(req, timeout=15).read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                wait = int(e.headers.get('Retry-After') or 0) or 10 * (attempt + 1)
                time.sleep(wait)
            elif e.code in (403, 429):
                return None
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def fetch_biggo_price(model):
    """搜一個型號，回傳 {price, merchants, url} 或 None"""
    html = _get('https://biggo.hk/s/?q=' + urllib.parse.quote(model))
    if not html:
        return None
    prices = []
    nm = norm_model(model)
    # 產品區塊按 product-row 分割；區塊內有 title（產品名）同 data-price（價錢）
    for b in re.split(r'ProductItemListPC_product-row', html)[1:]:
        t = re.search(r'title="([^"]+)"', b)
        p = re.search(r'data-price="true">\$([\d,]+(?:\.\d+)?)', b)
        if not t or not p:
            continue
        title = t.group(1).strip()
        # 共用過濾規則（同 PricesAPI 一套）：型號精確匹配 + 冷氣關鍵字 + 排除配件
        if len(nm) < 4 or not is_ac_title(title, nm):
            continue
        price = _num_price(p.group(1).replace(',', ''))
        if price:
            prices.append(price)
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
    if '--price-batch' in sys.argv:
        run_price_batch()
    elif len(sys.argv) > 1:
        # 單型號測試：python fetch_biggo.py RA-10RF
        for m in sys.argv[1:]:
            print(m, '→', fetch_biggo_price(m))
    else:
        print('用法：python fetch_biggo.py --smoke  /  --price-batch  /  <型號>')
