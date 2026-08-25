#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""價錢快照批次 meta 共用工具（fetch_biggo / fetch_pricesapi / fetch_prices / workflow 共用）

- prices_meta.json 讀寫
- 每月一次、分 7 日嘅批次進度（start / active / slice / advance）
- 限流冷卻期 + 部署/每日檢查打卡
"""
import json
import os
import time

BASE = os.path.dirname(os.path.abspath(__file__))
META_PATH = os.path.join(BASE, 'prices_meta.json')
COOLDOWN_HOURS = 48  # 限流冷卻期：熔斷後 48 小時內唔再試
PRICE_BATCH_DAYS = 7


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


def get_batch_todo(models, meta=None, days=PRICE_BATCH_DAYS, limit=None):
    """批次共用：計出今日要查嘅型號 slice。
    回 None 代表批次未啟動/已完成；否則回 (todo, idx, total)。
    """
    meta = meta or load_meta()
    idx = meta.get('price_batch_idx', 0)
    if not meta.get('price_batch_start') or idx >= days:
        return None
    total = min(len(models), limit) if limit and limit > 0 else len(models)
    day_cap = (total + days - 1) // days
    start = idx * day_cap
    todo = models[start:min((idx + 1) * day_cap, total)]
    return todo, idx, total


def advance_batch(meta, days=PRICE_BATCH_DAYS, today=None):
    """批次共用：完成今日 slice 後推進 idx / 完結清理。
    回 True 代表 7 日批次全部完成。
    """
    today = today or time.strftime('%Y-%m-%d')
    idx = meta.get('price_batch_idx', 0) + 1
    meta['price_batch_idx'] = idx
    meta['last_run'] = today
    if idx >= days:
        meta.pop('price_batch_start', None)
        meta['last_full'] = today
        return True
    return False


def deploy_stamp(stamp=None):
    """記錄今次成功部署嘅日期時間（香港時間）"""
    meta = load_meta()
    if not stamp:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    meta['last_deploy'] = stamp
    save_meta(meta)
    print(f'✅ 部署時間已記錄：{stamp}')


def checkin():
    """每日檢查打卡：只更新 last_check（俾網頁顯示每日檢查時間），唔當數據更新"""
    meta = load_meta()
    today = time.strftime('%Y-%m-%d')
    meta['last_check'] = today
    if not meta.get('last_run'):
        meta['last_run'] = today
    save_meta(meta)
    print(f'📅 每日檢查已記錄：{today}')
