#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查詢 BigGo 價錢批次狀態（workflow 專用；損毀 meta 唔可以當「未啟動」跳過）。

退出碼：
  0 = 批次進行中（可以跑今日 slice）
  1 = 未進行中（有效 meta；正常跳過）
  2 = meta 讀取／契約錯誤（必須阻斷，唔可以靜默跳過）
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    try:
        import batch_utils
        active = batch_utils.price_batch_active()
    except Exception as e:  # noqa: BLE001 - 契約錯誤要阻斷
        print(f'❌ prices_meta.json 無法安全讀取（唔會當未啟動跳過）：{e}', file=sys.stderr)
        return 2
    if active:
        print('BigGo 價錢批次進行中')
        return 0
    print('BigGo 價錢批次未啟動／已完成')
    return 1


if __name__ == '__main__':
    sys.exit(main())
