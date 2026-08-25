#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分批更新進度推進：
- stage 1 → 2（新機官網核實：第一批完成，聽日做第二批）
- stage 2 → 0（第二批完成，清空隊列）
- stage 0：冇新機，唔做嘢
"""
import json
import os

QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'update_queue.json')


def load():
    try:
        with open(QUEUE_PATH, encoding='utf-8') as f:
            q = json.load(f)
            if isinstance(q, dict) and 'stage' in q:
                return q
    except Exception:
        pass
    return {'stage': 0, 'models': []}


def main():
    q = load()
    old = q['stage']
    if old == 1:
        q['stage'] = 2
    elif old == 2:
        q['stage'] = 0
        q['models'] = []
    with open(QUEUE_PATH, 'w', encoding='utf-8') as f:
        json.dump(q, f, ensure_ascii=False)
    print(f'分批進度：stage {old} → {q["stage"]}')
    # 官網核實全部完成（2→0）：啟動價錢快照分批更新（每月最多一次）
    if old == 2:
        import batch_utils
        batch_utils.start_price_batch()


if __name__ == '__main__':
    main()
