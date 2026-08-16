#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BigGo 香港格價（官方公開 JSON API）價錢快照抓取
- 主力價錢源（同 BigGo MCP Server 用同一組商品搜索 API）
- 改用 BigGo 官方 JSON API（api.biggo.com），唔再解析 HTML、唔會撞 Cloudflare
- 端點：GET https://api.biggo.com/api/v1/spa/search/{型號}/product
  必要 headers：site=biggo.hk、region=hk
  來源：Funmula-Corp/biggo-mcp-server（官方開源客戶端，同一 API）
- 每月最多一次、分 7 日分批（批次進度共用 fetch_prices 嘅 meta）
輸出：biggo_prices.json {型號: {price, merchants, url, updated}}
  merchants = 過濾 + 商戶去重後嘅有效報價數
過濾規則：型號精確匹配 + 冷氣關鍵字 + 配件排除 + 商戶去重（同店同價同名）
"""
import json
import os
import random
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawl_utils import BOT_UA as UA, load_json, norm_model, load_models, save_json
from price_utils import format_price_range, is_ac_title, norm_title, num_price as _num_price

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'biggo_prices.json')

API = 'https://api.biggo.com/api/v1/spa/search/{}/product'

def _get(url):
    """帶抖動+退避嘅 GET，回傳解析好嘅 JSON dict；失敗回 None"""
    for attempt in range(3):
        try:
            time.sleep(random.uniform(0.4, 1.0))
            req = urllib.request.Request(url, headers={
                'User-Agent': UA,
                'Accept': 'application/json',
                'Accept-Language': 'zh-HK,zh;q=0.9',
                'Referer': 'https://biggo.hk/',
                'site': 'biggo.hk',
                'region': 'hk',
            })
            raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', 'ignore')
            return json.loads(raw)
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
    """搜一個型號。有價回 dict；查得到但無匹配回 False；網絡/限流錯誤回 None"""
    url = API.format(urllib.parse.quote(model, safe=''))
    data = _get(url)
    if data is None:
        return None
    nm = norm_model(model)
    if len(nm) < 4:
        return False
    prices = []
    seen = set()
    for it in data.get('list') or []:
        if not isinstance(it, dict):
            continue
        if it.get('is_offline') or it.get('is_expired'):
            continue
        title = (it.get('title') or '').strip()
        # 型號精確匹配 + 冷氣關鍵字 + 配件排除（共用 price_utils 規則）
        if not is_ac_title(title, nm):
            continue
        p = _num_price(it.get('price'))
        if p is None:
            continue
        store = ((it.get('store') or {}).get('name') or '').strip()
        # 商戶去重：同店 + 同價 + 同名標題算同一報價
        key = (store, p, norm_title(title))
        if key in seen:
            continue
        seen.add(key)
        prices.append(p)
    if not prices:
        return False
    price = format_price_range(prices)
    return {
        'price': price,
        'merchants': len(prices),
        'url': 'https://biggo.hk/s/?q=' + urllib.parse.quote(model),
        'updated': time.strftime('%Y-%m-%d'),
    }


def run_price_batch():
    """執行當日 BigGo 價錢批次（每月一次、分 7 日）"""
    import fetch_prices
    meta = fetch_prices.load_meta()
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    models = load_models()
    batch = fetch_prices.get_batch_todo(models, meta)
    if batch is None:
        print('💰 BigGo 批次：唔喺進行中，跳過')
        return
    todo, idx, total = batch
    print(f'💰 BigGo 批次 {idx + 1}/{fetch_prices.PRICE_BATCH_DAYS}：{len(todo)}/{total} 個型號，開始...')

    results = load_json(OUT_PATH, {})
    if not isinstance(results, dict):
        results = {}

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
            elif result is None:
                consec_fail += 1
            else:
                consec_fail = 0
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· {el:.0f}s', flush=True)
                save_json(OUT_PATH, results)
            if done_count >= 40 and consec_fail >= 40:
                print('⚠️ 連續 40 個失敗，疑似被限流，中止本批', flush=True)
                fetch_prices.set_cooldown()
                sys.exit(1)

    save_json(OUT_PATH, results)

    finished = fetch_prices.advance_batch(meta)
    if finished:
        print(f'🎉 BigGo 價錢快照全量更新完成（分 {fetch_prices.PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{meta.get("price_batch_idx")}/{fetch_prices.PRICE_BATCH_DAYS}），聽日繼續')
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
