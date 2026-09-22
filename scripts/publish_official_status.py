#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `run_official_batch.py` machine receipt 投影為公開 pending 狀態檔。

只寫入無絕對路徑／無 secrets 嘅欄位；decision 必須係已知值。寫入原子。
"""
import argparse
import json
import os
import sys

DECISIONS = {
    'advanced',
    'queue-kept-pending-coverage',
    'queue-kept-fail-closed',
}


def main(argv=None):
    ap = argparse.ArgumentParser(description='投影官網核實 pending 狀態')
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        with open(args.receipt, encoding='utf-8') as f:
            receipt = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f'❌ 讀唔到 official receipt：{e}', file=sys.stderr)
        return 1
    if not isinstance(receipt, dict):
        print('❌ official receipt 必須係 object', file=sys.stderr)
        return 1
    decision = receipt.get('decision')
    if decision not in DECISIONS:
        print(f'❌ receipt decision 未知／缺失：{decision!r}', file=sys.stderr)
        return 1
    missing = receipt.get('missingModels')
    if not isinstance(missing, list):
        missing = []
    canon = receipt.get('missingCanonicalModels')
    if not isinstance(canon, list):
        canon = []
    status = {
        'schemaVersion': 1,
        'decision': decision,
        'stage': receipt.get('stage'),
        'pendingCoverage': decision == 'queue-kept-pending-coverage',
        'missingModels': [m for m in missing if isinstance(m, str)],
        'missingCanonicalModels': [m for m in canon if isinstance(m, str)],
        'generatedAt': receipt.get('finishedAt') or receipt.get('startedAt'),
        'source': 'run_official_batch.py',
    }
    out = os.path.abspath(args.out)
    tmp = out + '.tmp'
    try:
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        print(f'❌ 寫入 official status 失敗：{e}', file=sys.stderr)
        return 1
    print(f'✅ official pending status：decision={decision} pending={status["pendingCoverage"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
