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
import random
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, 'emsd_空調能源標籤.csv')
OUT_PATH = os.path.join(BASE, 'prices.json')
META_PATH = os.path.join(BASE, 'prices_meta.json')

# 誠實 Bot UA（列明專案來源，方便網站管理員聯絡）
UA = ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
      '+https://github.com/CalvinLau1012/aircon-compare) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36')


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def fetch_price(model):
    """查一個型號嘅 Price 價格範圍 + 產品 ID；退避重試，遵從 Retry-After，失敗返回 None"""
    url = 'https://www.price.com.hk/search.php?g=A&q=' + urllib.parse.quote(model)
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.2, 0.7))  # 隨機抖動，避免突發流量
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
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                wait = int(e.headers.get('Retry-After') or 0) or 10 * (attempt + 1)
                time.sleep(wait)  # 被限流：遵從 Retry-After，退避後再試
            elif e.code in (403, 429):
                return None  # 連續被拒，唔再硬碰
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def detect_mode():
    """判斷今日係全量刷新定平日補缺（供 GitHub Actions 偵測 job 呼叫）"""
    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, encoding='utf-8') as f:
            meta = json.load(f)
    try:
        last_full_ts = time.mktime(time.strptime(meta.get('last_full', ''), '%Y-%m-%d'))
        full_due = (time.time() - last_full_ts) > 6 * 86400
    except Exception:
        full_due = True
    return 'full' if full_due else 'daily'


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

    # 每週先做一次全量刷新，平日只補缺（降低每日請求量，減少被限流風險）
    full_due = (detect_mode() == 'full')
    todo = models if full_due else [m for m in models if m not in results]
    print(('本週全量刷新' if full_due else '平日補缺模式') + f'：{len(todo)} 個要查，開始...')

    ok = 0
    touched = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
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
            if result is not None:
                touched += 1
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· 用咗 {el:.0f}s')
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)

    # 限流熔斷：大規模抓取但成功率太低 → 疑似被封，放棄本次更新，保留現有數據
    if len(todo) >= 50 and touched < len(todo) * 0.5:
        print(f'⚠️ 疑似被官方限流（成功 {touched}/{len(todo)}），中止更新，保留現有數據')
        sys.exit(1)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    today = time.strftime('%Y-%m-%d')
    meta['last_run'] = today
    if full_due:
        meta['last_full'] = today
    with open(META_PATH, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    el = time.time() - t0
    print(f'完成！{len(todo)} 個中得價 {ok} 個 · 總價庫 {len(results)} 個 · 用咗 {el:.0f}s')
    print('存於', OUT_PATH)


if __name__ == '__main__':
    main()
