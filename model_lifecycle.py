#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
型號生命週期管理（淘汰/黑名單）
- 記錄每次 BigGo 乾淨搜尋結果（有價 / 確實無匹配）
- 連續 N 次乾淨「無市售報價」→ 自動掉入黑名單
- 黑名單內型號保留舊快照，唔再排入更新批次
- 網絡錯誤（None）唔會計入，避免因為 BigGo 封鎖而誤殺型號
"""
import os
import time

from crawl_utils import canonical_model_key, load_json, save_json

BASE = os.path.dirname(os.path.abspath(__file__))
BLACKLIST_PATH = os.path.join(BASE, 'model_blacklist.json')
TRACKING_PATH = os.path.join(BASE, 'model_status.json')

DEFAULT_MISS_THRESHOLD = 2  # 連續 2 個完整更新週期都搵唔到，先自動淘汰


def _today():
    return time.strftime('%Y-%m-%d')


def load_blacklist():
    data = load_json(BLACKLIST_PATH, {})
    if not isinstance(data, dict):
        return {}
    return data.get('models') if isinstance(data.get('models'), dict) else {}


def save_blacklist(models):
    save_json(BLACKLIST_PATH, {
        'version': 1,
        'updated': _today(),
        'models': models,
    }, indent=2)


def as_key(model, brand_of=None):
    """型號／key → canonical key（BRAND|NORM）。

    已經係 canonical key（含 '|'）就用返本身；
    有 brand_of 就用品牌解 canonical；都冇就回傳原始字串（舊行為，兼容測試）。
    """
    model = str(model)
    if '|' in model:
        return model
    if brand_of:
        brand = brand_of(model)
        if brand:
            return canonical_model_key(brand, model)
    return model


def is_blacklisted(model, brand_of=None):
    return as_key(model, brand_of) in load_blacklist()


def get_blacklist_entry(model, brand_of=None):
    return load_blacklist().get(as_key(model, brand_of))


def blacklist_model(model, reason, status='auto_discontinued', source='biggo', kept_price_source=''):
    models = load_blacklist()
    key = as_key(model)
    models[key] = {
        'status': status,
        'reason': reason,
        'source': source,
        'kept_price_source': kept_price_source,
        'blacklisted_at': _today(),
        'note': '保留舊快照，停止更新；如果要恢復更新，請由黑名單刪除呢個型號',
    }
    save_blacklist(models)
    return models[key]


def revive_model(model, brand_of=None):
    """黑名單復活：官方 API 正常查到市售報價 → 移除黑名單 + 清除連續失敗記錄"""
    key = as_key(model, brand_of)
    tracking = load_json(TRACKING_PATH, {})
    new_tracking, new_black, revived = plan_revive(
        key, tracking=tracking if isinstance(tracking, dict) else {}, blacklist=None)
    if not revived:
        return False
    save_blacklist(new_black)
    if new_tracking != tracking:
        save_json(TRACKING_PATH, new_tracking, indent=2)
    print(f'  ♻️ 復活：{key}（黑名單 → 有市售報價）')
    return True


def plan_revive(key, *, tracking=None, blacklist=None):
    """純函數復活：回 (new_tracking, new_blacklist, revived)；唔寫檔。

    `key` 已經係 canonical key（caller 用 as_key 轉好）。
    """
    new_tracking = dict(tracking) if isinstance(tracking, dict) else \
        (load_json(TRACKING_PATH, {}) or {})
    new_black = dict(blacklist) if isinstance(blacklist, dict) else dict(load_blacklist())
    if key not in new_black:
        return new_tracking, new_black, False
    new_black.pop(key, None)
    new_tracking.pop(key, None)
    return new_tracking, new_black, True


def compute_effects(pre_tracking, pre_black, post_tracking, post_black):
    """由 pre/post tracking＋blacklist 計出可驗證的 delta effects（排序固定）。"""
    return {
        'trackingUpserts': {k: v for k, v in post_tracking.items()
                            if pre_tracking.get(k) != v},
        'trackingRemovals': sorted(k for k in pre_tracking if k not in post_tracking),
        'blacklistUpserts': {k: v for k, v in post_black.items()
                             if pre_black.get(k) != v},
        'blacklistRemovals': sorted(k for k in pre_black if k not in post_black),
    }


def filter_active(models, key_of=None):
    """黑名單過濾：key_of 提供時用 canonical key 比對（BRAND|NORM）"""
    black = load_blacklist()
    skipped = [m for m in models if (key_of(m) if key_of else str(m)) in black]
    todo = [m for m in models if (key_of(m) if key_of else str(m)) not in black]
    return todo, skipped


def plan_record_results(results, protected=None, batch_id=None, brand_of=None, *,
                        tracking=None, blacklist=None, today=None):
    """純函數淘汰計劃：回 (new_tracking, new_blacklist)；唔寫任何檔。

    語義同 `record_results` 完全一致（三態、batch_id 去重、protected、threshold）。
    """
    protected = set(protected or ())
    new_tracking = dict(tracking) if isinstance(tracking, dict) else \
        (load_json(TRACKING_PATH, {}) or {})
    if not isinstance(new_tracking, dict):
        new_tracking = {}
    new_black = dict(blacklist) if isinstance(blacklist, dict) else dict(load_blacklist())
    today = today or _today()
    threshold = int(os.environ.get('MODEL_BLACKLIST_MISS_THRESHOLD', DEFAULT_MISS_THRESHOLD))

    for model, has_price in results:
        key = as_key(model, brand_of)
        if key in protected:
            continue
        if has_price:
            new_tracking.pop(key, None)
            continue
        rec = new_tracking.get(key)
        if not isinstance(rec, dict):
            rec = {'first_missed': today, 'misses': 0}
        if batch_id and rec.get('batch_id') == batch_id:
            continue  # 同一批次重跑，唔重複計 miss
        rec['last_checked'] = today
        rec['misses'] = int(rec.get('misses', 0)) + 1
        rec['first_missed'] = rec.get('first_missed') or today
        rec['batch_id'] = batch_id
        new_tracking[key] = rec
        if rec['misses'] >= threshold and key not in new_black:
            new_black[key] = {
                'status': 'auto_discontinued',
                'reason': f'連續 {rec["misses"]} 個更新週期 BigGo 商品搜索都無市售報價',
                'source': 'biggo',
                'kept_price_source': 'biggo_prices.json 如有舊快照會保留',
                'first_missed': rec['first_missed'],
                'last_checked': today,
                'blacklisted_at': today,
                'note': '自動淘汰：保留舊快照，唔再做更新',
            }
    return new_tracking, new_black


def record_results(results, protected=None, batch_id=None, brand_of=None):
    """
    results: [(model_or_key, has_price), ...]
      has_price=True  → 有市售報價，清空連續失敗記錄
      has_price=False → 乾淨無匹配，連續失敗 +1；夠期就自動黑名單
      has_price=None  → 網絡/API 錯誤，唔計
    protected: canonical key 集合（唔會自動淘汰，例如核心 29）
    batch_id: 今次批次 ID；同一批次重跑唔會重複累加 misses
    brand_of: callable（型號 → 品牌原文），用嚟由型號計 canonical key
    """
    tracking = load_json(TRACKING_PATH, {})
    if not isinstance(tracking, dict):
        tracking = {}
    black = load_blacklist()
    new_tracking, new_black = plan_record_results(
        results, protected=protected, batch_id=batch_id, brand_of=brand_of,
        tracking=tracking, blacklist=black)
    if new_tracking != tracking:
        save_json(TRACKING_PATH, new_tracking, indent=2)
    if new_black != black:
        save_blacklist(new_black)
    return len(new_black)


def print_blacklist():
    black = load_blacklist()
    print(f'🚫 型號黑名單（{len(black)} 個，唔再更新）')
    for model, info in sorted(black.items()):
        print(f'  - {model}: {info.get("reason", "")}（{info.get("last_checked", info.get("blacklisted_at", "?"))}）')


if __name__ == '__main__':
    print_blacklist()
