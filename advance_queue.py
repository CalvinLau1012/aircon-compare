#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分批更新進度推進（fail-closed、可重試）：
- stage 1 → 2（新機官網核實：第一批完成，聽日做第二批）
- stage 2 → 0（第二批完成，清空隊列）＋啟動價錢快照批次（每月最多一次）

冪等次序（2→0）：先確保 price meta 已啟動，成功／已啟動後才清 queue。
- price meta 讀／寫失敗 → queue 保持 stage 2 及原 models bytes，非零退出；
- price batch 已啟動／本月已完成（回傳無需新啟動）視為成功，可重試；
- price meta 已更新但 queue 保存失敗 → 重跑時 start 會報「已啟動」，再清 queue 即完成。
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
        # 先啟動 price meta（成功或已啟動皆可），之後才清 queue；確保 queue 唔會無故遺失
        try:
            started = _start_price_batch_idempotent()
        except Exception as e:  # noqa: BLE001 - meta 讀／寫失敗要阻斷，queue 保留
            print(f'❌ 價錢批次啟動失敗：{e}；queue 保持 stage 2（未清空），可重試', file=sys.stderr)
            return 1
        q2 = dict(q)
        q2['stage'] = 0
        q2['models'] = []
        try:
            save(q2)
        except (RuntimeError, ValueError) as e:
            print(f'❌ queue 保存失敗：{e}；price meta 已處理（started={started}），'
                  '重跑會安全完成（唔會重複破壞）', file=sys.stderr)
            return 1
        print(f'分批進度：stage {old} → {q2["stage"]}（price batch started={started}）')
        return 0
    print('分批進度：stage 0，唔使推進')
    return 0


if __name__ == '__main__':
    sys.exit(main())
