#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取 Price.com.hk 價格範圍（EMSD 型號）
輸入：emsd_空調能源標籤.csv
輸出：prices.json {型號: "$X,XXX - Y,YYY"}

共享工具（唔重複造輪）：
- crawl_utils：UA / norm_model / load_models
- batch_utils：prices_meta 讀寫、批次進度、冷卻期（fetch_biggo / fetch_pricesapi / workflow 一齊用）
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

from crawl_utils import BOT_UA as UA, load_models, norm_model
from batch_utils import (COOLDOWN_HOURS, PRICE_BATCH_DAYS, load_meta, save_meta,
                         set_cooldown, detect_mode, start_price_batch,
                         price_batch_active, get_batch_todo, advance_batch)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'prices.json')


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


# ===== 價錢快照批次 meta/進度全部由 batch_utils 共用（re-export 保持舊介面）=====
# load_meta / save_meta / set_cooldown / detect_mode / start_price_batch /
# price_batch_active / get_batch_todo / advance_batch / PRICE_BATCH_DAYS / COOLDOWN_HOURS


def run_price_batch():
    """執行當日價錢批次（全量分 7 片，每日一片；切片/推進由 batch_utils 共用）"""
    meta = load_meta()
    batch = get_batch_todo(load_models(), meta)
    if not batch:
        print('💰 價錢批次：唔喺進行中，跳過')
        return
    todo, idx, total = batch
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    print(f'💰 價錢批次 {idx + 1}/{PRICE_BATCH_DAYS}：{len(todo)}/{total} 個型號，開始...')

    results = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            old = json.load(f)
        for k, v in old.items():
            results[k] = v if isinstance(v, dict) else {'price': v, 'pid': None}

    ok = 0
    consec_fail = 0
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
            if result is None:
                consec_fail += 1
            else:
                consec_fail = 0
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· {el:.0f}s')
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)
            if done_count >= 40 and consec_fail >= 40:
                print('⚠️ 連續 40 個請求失敗，疑似被限流，中止本批（保留現有數據）')
                set_cooldown()
                sys.exit(1)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)

    done = advance_batch(meta)
    save_meta(meta)
    if done:
        print(f'🎉 價錢快照全量更新完成（分 {PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{meta["price_batch_idx"]}/{PRICE_BATCH_DAYS}），聽日繼續')


def main():
    if '--price-batch' in sys.argv:
        run_price_batch()
        return
    # 冷卻期：48 小時內唔再抓取（尊重官方限流），直接跳過
    if detect_mode() == 'cooldown':
        print('🕐 冷卻期內，跳過 Price 抓取（48 小時後自動恢復）')
        return

    models = load_models()
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
    consec_fail = 0
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
            # 快速熔斷：連續大量失敗（連續 40 個攞唔到嘢）→ 疑似被封，提早中止
            if result is None:
                consec_fail += 1
            else:
                consec_fail = 0
            done_count += 1
            if done_count % 50 == 0:
                el = time.time() - t0
                print(f'  進度 {done_count}/{len(todo)}（得價 {ok}）· 用咗 {el:.0f}s')
                with open(OUT_PATH, 'w', encoding='utf-8') as f:
                    json.dump(results, f, ensure_ascii=False)
            if done_count >= 40 and consec_fail >= 40:
                print(f'⚠️ 連續 {consec_fail} 個請求失敗，疑似被官方限流，提早中止，保留現有數據')
                set_cooldown()
                sys.exit(1)

    # 限流熔斷：大規模抓取但成功率太低 → 疑似被封，放棄本次更新，保留現有數據
    if len(todo) >= 50 and touched < len(todo) * 0.5:
        print(f'⚠️ 疑似被官方限流（成功 {touched}/{len(todo)}），中止更新，保留現有數據')
        set_cooldown()
        sys.exit(1)

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False)
    today = time.strftime('%Y-%m-%d')
    meta = load_meta()
    meta['last_run'] = today
    if full_due:
        meta['last_full'] = today
    meta.pop('blocked_until', None)  # 成功抓取：解除冷卻期
    save_meta(meta)
    el = time.time() - t0
    print(f'完成！{len(todo)} 個中得價 {ok} 個 · 總價庫 {len(results)} 個 · 用咗 {el:.0f}s')
    print('存於', OUT_PATH)


if __name__ == '__main__':
    main()
