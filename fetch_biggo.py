#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BigGo 香港格價（biggo.hk）價錢快照抓取
- 多商戶報價平台（Price.com.hk 性質一致），公開網頁，無 Cloudflare
- 每月最多一次、分 7 日分批（批次進度共用 fetch_prices 嘅 meta）
輸出：biggo_prices.json {型號: {price: "$X,XXX-YY,YYY", merchants: N, updated: 日期}}
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

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'biggo_prices.json')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36')


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


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
    # 產品區塊按 product-row 分割；區塊內有 title（產品名）同 data-price（價錢）
    for b in re.split(r'ProductItemListPC_product-row', html)[1:]:
        t = re.search(r'title="([^"]+)"', b)
        p = re.search(r'data-price="true">\$([\d,]+(?:\.\d+)?)', b)
        if not t or not p:
            continue
        title = t.group(1).strip()
        # 過濾：產品名必須含型號 + 冷氣相關關鍵字（排除 RF 模組等撞名產品）
        nm, nt = norm_model(model), norm_model(title)
        if len(nm) < 4 or nm not in nt:
            continue
        if not re.search(r'冷氣|空調|air-?con', title, re.I):
            continue
        # 排除配件/服務（遙控器、濾網、支架等）
        if re.search(r'遙控|濾網|過濾|配件|說明書|支架|擋板|防塵|罩|remote|filter|parts?|cover|bracket', title, re.I):
            continue
        try:
            prices.append(int(p.group(1).replace(',', '')))
        except ValueError:
            continue
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


def load_models():
    """EMSD CSV 型號清單（去重）"""
    import csv
    csv_path = os.path.join(BASE, 'emsd_空調能源標籤.csv')
    rows = list(csv.reader(open(csv_path, encoding='utf-8-sig')))[1:]
    rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']
    models, seen = [], set()
    for r in rows:
        m = r[1].strip()
        k = norm_model(m)
        if not k or k in seen:
            continue
        seen.add(k)
        models.append(m)
    return models


def run_price_batch():
    """執行當日 BigGo 價錢批次（每月一次、分 7 日）"""
    import fetch_prices
    meta = fetch_prices.load_meta()
    idx = meta.get('price_batch_idx', 0)
    if not meta.get('price_batch_start') or idx >= fetch_prices.PRICE_BATCH_DAYS:
        print('💰 BigGo 批次：唔喺進行中，跳過')
        return
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    models = load_models()
    n = len(models)
    step = (n + fetch_prices.PRICE_BATCH_DAYS - 1) // fetch_prices.PRICE_BATCH_DAYS
    todo = models[idx * step:(idx + 1) * step]
    print(f'💰 BigGo 批次 {idx + 1}/{fetch_prices.PRICE_BATCH_DAYS}：{len(todo)} 個型號，開始...')

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
                fetch_prices.set_cooldown()
                sys.exit(1)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    idx += 1
    meta['price_batch_idx'] = idx
    if idx >= fetch_prices.PRICE_BATCH_DAYS:
        meta.pop('price_batch_start', None)
        meta['last_full'] = time.strftime('%Y-%m-%d')
        print(f'🎉 BigGo 價錢快照全量更新完成（分 {fetch_prices.PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{idx}/{fetch_prices.PRICE_BATCH_DAYS}），聽日繼續')
    fetch_prices.save_meta(meta)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if '--price-batch' in sys.argv:
        run_price_batch()
    elif len(sys.argv) > 1:
        # 單型號測試：python fetch_biggo.py RA-10RF
        for m in sys.argv[1:]:
            print(m, '→', fetch_biggo_price(m))
    else:
        print('用法：python fetch_biggo.py --price-batch  或  python fetch_biggo.py <型號>')
