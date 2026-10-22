#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分批更新進度推進（fail-closed、可重試）：
- stage 1 → 2（新機官網核實：第一批完成，聽日做第二批）
- stage 2 → 0（第二批完成，清空隊列）

官網 queue 與價格需求已分離：advance_queue 只處理官網分批隊列，
永遠唔會啟動／推進價格批次。價格批次只由 price_demand_entry 根據
「未處理新機 + 本月未做過」獨立判斷，因此官網純 coverage pending
不會再阻住下一輪價格工作，也不會靠清 queue 換取 price 啟動。
任何讀取／解析／寫入錯誤都非零退出，唔會默默重置 stage。
"""
import os
import sys

QUEUE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'update_queue.json')


def load():
    """讀取分批更新隊列（共用 queue_utils 契約；錯誤 raise 唔 reset）。"""
    from queue_utils import load_queue
    return load_queue(QUEUE_PATH)


def save(q):
    """原子保存分批更新隊列（共用 queue_utils 契約）。"""
    from queue_utils import save_queue
    save_queue(q, QUEUE_PATH)


def _start_price_batch_idempotent():
    """啟動 price batch；True=新啟動，False=已啟動／本月已完成（可重試成功）。"""
    import batch_utils
    return batch_utils.start_price_batch()


def main():
    try:
        q = load()
    except (RuntimeError, ValueError) as e:
        print(f'❌ 分批進度推進失敗（唔會重置隊列）：{e}', file=sys.stderr)
        return 1
    old = q['stage']
    if old == 1:
        q['stage'] = 2
        try:
            save(q)
        except (RuntimeError, ValueError) as e:
            print(f'❌ 分批進度推進失敗（唔會重置隊列）：{e}', file=sys.stderr)
            return 1
        print(f'分批進度：stage {old} → {q["stage"]}')
        return 0
    if old == 2:
        # Only the official queue transitions here.  Price demand/cycle is a
        # separate entry and is deliberately not consulted or mutated.
        q2 = dict(q)
        q2['stage'] = 0
        q2['models'] = []
        try:
            save(q2)
        except (RuntimeError, ValueError) as e:
            print(f'❌ queue 保存失敗：{e}；queue 保持 stage 2，可重試', file=sys.stderr)
            return 1
        print(f'分批進度：stage {old} → {q2["stage"]}（價格入口獨立處理）')
        return 0
    print('分批進度：stage 0，唔使推進')
    return 0


if __name__ == '__main__':
    sys.exit(main())
