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

from crawl_utils import load_json, save_json

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


def is_blacklisted(model):
    return str(model) in load_blacklist()


def get_blacklist_entry(model):
    return load_blacklist().get(str(model))


def blacklist_model(model, reason, status='auto_discontinued', source='biggo', kept_price_source=''):
    models = load_blacklist()
    models[str(model)] = {
        'status': status,
        'reason': reason,
        'source': source,
        'kept_price_source': kept_price_source,
        'blacklisted_at': _today(),
        'note': '保留舊快照，停止更新；如果要恢復更新，請由黑名單刪除呢個型號',
    }
    save_blacklist(models)
    return models[str(model)]


def filter_active(models):
    black = load_blacklist()
    skipped = [m for m in models if str(m) in black]
    todo = [m for m in models if str(m) not in black]
    return todo, skipped


def record_results(results, protected=None):
    """
    results: [(model, has_price), ...]
      has_price=True  → 有市售報價，清空連續失敗記錄
      has_price=False → 乾淨無匹配，連續失敗 +1；夠期就自動黑名單
      has_price=None  → 網絡/API 錯誤，唔計
    protected: 唔會自動淘汰嘅型號集合（例如核心 29）
    """
    protected = set(protected or ())
    tracking = load_json(TRACKING_PATH, {})
    if not isinstance(tracking, dict):
        tracking = {}
    black = load_blacklist()
    today = _today()
    threshold = int(os.environ.get('MODEL_BLACKLIST_MISS_THRESHOLD', DEFAULT_MISS_THRESHOLD))
    changed = False

    for model, has_price in results:
        model = str(model)
        if model in protected:
            continue
        if has_price:
            if model in tracking:
                tracking.pop(model, None)
                changed = True
            continue
        rec = tracking.get(model)
        if not isinstance(rec, dict):
            rec = {'first_missed': today, 'misses': 0}
        rec['last_checked'] = today
        rec['misses'] = int(rec.get('misses', 0)) + 1
        rec['first_missed'] = rec.get('first_missed') or today
        tracking[model] = rec
        changed = True
        if rec['misses'] >= threshold and model not in black:
            black[model] = {
                'status': 'auto_discontinued',
                'reason': f'連續 {rec["misses"]} 個更新週期 BigGo 商品搜索都無市售報價',
                'source': 'biggo',
                'kept_price_source': 'biggo_prices.json 如有舊快照會保留',
                'first_missed': rec['first_missed'],
                'last_checked': today,
                'blacklisted_at': today,
                'note': '自動淘汰：保留舊快照，唔再做更新',
            }
            changed = True

    if changed:
        save_json(TRACKING_PATH, tracking, indent=2)
        if len(black) != len(load_blacklist()):
            save_blacklist(black)
    return len(black)


def print_blacklist():
    black = load_blacklist()
    print(f'🚫 型號黑名單（{len(black)} 個，唔再更新）')
    for model, info in sorted(black.items()):
        print(f'  - {model}: {info.get("reason", "")}（{info.get("last_checked", info.get("blacklisted_at", "?"))}）')


if __name__ == '__main__':
    print_blacklist()
