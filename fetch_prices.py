#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量抓取 Price.com.hk 價格範圍（EMSD 型號）
- ⚠️ Price.com.hk 已被 Cloudflare 封，本腳本只保留做舊快照維護；
  主力價錢源已改為 fetch_pricesapi.py（批次 meta 仍然由呢度共用）
輸入：emsd_空調能源標籤.csv
輸出：prices.json {型號: "$X,XXX - Y,YYY"}
"""
import json
import os
import random
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

from crawl_utils import BOT_UA as UA, norm_model, load_models

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, 'emsd_空調能源標籤.csv')
OUT_PATH = os.path.join(BASE, 'prices.json')
META_PATH = os.path.join(BASE, 'prices_meta.json')
COOLDOWN_HOURS = 48  # 限流冷卻期：熔斷後 48 小時內唔再試


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


def load_meta():
    """讀取 meta（唔存在就空）"""
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_meta(meta):
    with open(META_PATH, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)


def set_cooldown():
    """限流熔斷後寫入冷卻期（48 小時內唔再試）"""
    meta = load_meta()
    meta['blocked_until'] = int(time.time() + COOLDOWN_HOURS * 3600)
    save_meta(meta)
    print(f'🕐 已設冷卻期：{COOLDOWN_HOURS} 小時內唔再抓取（至 ' + time.strftime('%Y-%m-%d %H:%M', time.localtime(meta['blocked_until'])) + '）')


def detect_mode():
    """判斷今日係全量刷新/平日補缺/冷卻期（供 GitHub Actions 偵測 job 呼叫）"""
    meta = load_meta()
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        return 'cooldown'
    try:
        last_full_ts = time.mktime(time.strptime(meta.get('last_full', ''), '%Y-%m-%d'))
        full_due = (time.time() - last_full_ts) > 6 * 86400
    except Exception:
        full_due = True
    return 'full' if full_due else 'daily'


# ===== 價錢快照分批更新（每月最多一次，分 7 日）=====
PRICE_BATCH_DAYS = 7


def start_price_batch():
    """啟動價錢快照分批更新：有新機且本月未做過先啟動（分 7 日，每日一批）"""
    meta = load_meta()
    month = time.strftime('%Y-%m')
    if meta.get('last_price_month') == month:
        print(f'💰 價錢更新：本月已做過（{month}），等下個月先再更新')
        return False
    if meta.get('price_batch_start') and meta.get('price_batch_idx', 0) < PRICE_BATCH_DAYS:
        print('💰 價錢更新：分批進行中，唔重複啟動')
        return False
    meta['price_batch_start'] = time.strftime('%Y-%m-%d')
    meta['price_batch_idx'] = 0
    meta['last_price_month'] = month
    save_meta(meta)
    print(f'💰 價錢更新已啟動：分 {PRICE_BATCH_DAYS} 日分批進行（本月 {month} 唔再重複）')
    return True


def price_batch_active():
    """價錢分批更新係咪進行中（供 workflow 判斷）"""
    meta = load_meta()
    return bool(meta.get('price_batch_start')) and meta.get('price_batch_idx', 0) < PRICE_BATCH_DAYS


def run_price_batch():
    """執行當日價錢批次（全量分 7 片，每日一片）"""
    meta = load_meta()
    idx = meta.get('price_batch_idx', 0)
    if not meta.get('price_batch_start') or idx >= PRICE_BATCH_DAYS:
        print('💰 價錢批次：唔喺進行中，跳過')
        return
    blocked = meta.get('blocked_until')
    if blocked and time.time() < blocked:
        print('🕐 冷卻期內，跳過本批（之後批次會繼續）')
        return

    models = load_models()
    n = len(models)
    step = (n + PRICE_BATCH_DAYS - 1) // PRICE_BATCH_DAYS
    todo = models[idx * step:(idx + 1) * step]
    print(f'💰 價錢批次 {idx + 1}/{PRICE_BATCH_DAYS}：{len(todo)} 個型號，開始...')

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

    idx += 1
    meta['price_batch_idx'] = idx
    if idx >= PRICE_BATCH_DAYS:
        meta.pop('price_batch_start', None)
        meta['last_full'] = time.strftime('%Y-%m-%d')
        print(f'🎉 價錢快照全量更新完成（分 {PRICE_BATCH_DAYS} 日）')
    else:
        print(f'💰 本批完成（{idx}/{PRICE_BATCH_DAYS}），聽日繼續')
    save_meta(meta)


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
