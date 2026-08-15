#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取 Price.com.hk 價格範圍（EMSD 型號）
輸入：emsd_空調能源標籤.csv
輸出：prices.json {型號: "$X,XXX - Y,YYY"}
"""
import csv
import json
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = r'd:\香港窗口式空調查找'
CSV_PATH = os.path.join(BASE, 'emsd_空調能源標籤.csv')
OUT_PATH = os.path.join(BASE, 'prices.json')

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36'


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def fetch_price(model):
    """查一個型號嘅 Price 價格範圍 + 產品 ID；失敗/冇結果返回 None"""
    url = 'https://www.price.com.hk/search.php?g=A&q=' + urllib.parse.quote(model)
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            html = urllib.request.urlopen(req, timeout=12).read().decode('utf-8', 'ignore')
            # 產品 ID（第一個 = 最相關）
            pid_m = re.search(r'product\.php\?p=(\d+)', html)
            pid = pid_m.group(1) if pid_m else None
            # 搵第一個 listing-price-range
            m = re.search(
                r'listing-price-range.*?data-price="([\d.]+)".*?'
                r'(?:data-price="([\d.]+)")?',
                html, re.S)
            if m:
                low = int(float(m.group(1)))
                high = int(float(m.group(2))) if m.group(2) else None
                price = f"${low:,}-{high:,}" if (high and high > low) else f"${low:,}起"
                return {'price': price, 'pid': pid}
            if pid:
                return {'price': None, 'pid': pid}  # 有產品但攞唔到價
            return None
        except Exception:
            time.sleep(1.5)
    return None


def main():
    rows = list(csv.reader(open(CSV_PATH, encoding='utf-8-sig')))[1:]
    rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']
    models = []
    seen = set()
    for r in rows:
        m = r[1].strip()
        k = norm_model(m)
        if not k or k in seen:
            continue
        seen.add(k)
        models.append(m)
    print(f'共 {len(models)} 個型號要查價')

    # 載入已有進度（斷點續跑）
    results = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            old = json.load(f)
        # 舊格式（純價錢字串）轉新格式
        for k, v in old.items():
            results[k] = v if isinstance(v, dict) else {'price': v, 'pid': None}
        print(f'已有 {len(results)} 個結果，跳過')

    todo = [m for m in models if m not in results]
    print(f'仲有 {len(todo)} 個要查，開始...')

    ok = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_price, m): m for m in todo}
        done_count = 0
        for fut in as_completed(futures):
            m = futures[fut]
            try:
                result = fut.result()
            except Exception:
                result = None
            if result and result.get('price'):
                results[m] = result
                ok += 1
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· 用咗 {el:.0f}s')
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    el = time.time() - t0
    print(f'完成！{len(todo)} 個中得價 {ok} 個 · 總價庫 {len(results)} 個 · 用咗 {el:.0f}s')
    print('存於', OUT_PATH)


if __name__ == '__main__':
    main()
