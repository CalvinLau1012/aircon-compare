#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo stage artifact CI 守門／警報（P0）。

用法：
  python scripts/verify_biggo_stage_artifacts.py --repo . --status <status.json>
      → 四個 canonical 檔有 diff 時，必須有 runner status 證明已完成（published／
        imported ＋ localApply applied/noop）；否則 rc=1，push 前阻斷。
  python scripts/verify_biggo_stage_artifacts.py --alert-only --status <status.json>
      → needs_review／publish uncertain／apply failure 等必須人手處理的狀態 → rc=1
        （可見紅色；EMSD 日常發布不受影響）。
  python scripts/verify_biggo_stage_artifacts.py --repo . --expect-clean
      → 網絡階段前檢查四檔必須乾淨（regression tripwire）。

輸出只含狀態、計數、hash 短碼，唔含 secrets／私人識別。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

PATHS = ['biggo_prices.json', 'prices_meta.json', 'model_blacklist.json',
         'model_status.json']
CONFIRMED_STATUSES = {'completed', 'completed-recovered', 'completed-idempotent-applied'}
ALERT_STATUSES = {'needs-review', 'blocked-intent-persist', 'completed-apply-failed',
                  'completed-legacy-manual', 'completed-bundle-unavailable',
                  'blocked-incomplete', 'blocked-quota-window-unsupported',
                  'blocked-stage-gap', 'blocked-force-cycle-incomplete', 'blocked-stale'}


def _changed_paths(repo):
    r = subprocess.run(['git', '-C', repo, 'status', '--porcelain', '-z', '--'] + PATHS,
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f'git status 失敗（rc={r.returncode}）')
    out = []
    for token in r.stdout.split(b'\0'):
        if not token:
            continue
        text = token.decode('utf-8', 'replace')
        out.append(text)
    return out


def _load_status(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _summary(status):
    return {
        'status': status.get('status'),
        'localApply': status.get('localApply'),
        'published': bool(status.get('published')),
        'imported': bool(status.get('imported')),
        'alert': bool(status.get('alert')),
        'requests': status.get('requests'),
    }


def run_guard(repo, status_path, expect_clean=False):
    changed = _changed_paths(repo)
    if expect_clean:
        if changed:
            print(f'❌ 網絡階段前 canonical 檔已被改動：{changed}')
            return 1
        print('✅ BigGo canonical 四檔乾淨（pre-network check）')
        return 0
    if not changed:
        print('✅ BigGo canonical 四檔無 diff；guard pass')
        return 0
    status = _load_status(status_path)
    if status is None:
        print(f'::error::BigGo canonical 四檔有 diff 但冇有效 status artifact：{changed}')
        return 1
    detail = _summary(status)
    print(f'BigGo guard：diff={changed} status={detail}')
    if status.get('status') not in CONFIRMED_STATUSES:
        print(f"::error::BigGo diff 未確認（status={status.get('status')}）；阻止 push")
        return 1
    if status.get('localApply') not in ('applied', 'noop'):
        print(f"::error::BigGo localApply={status.get('localApply')}；阻止 push")
        return 1
    if not (status.get('published') or status.get('imported')):
        print('::error::BigGo status 冇 published／imported 證據；阻止 push')
        return 1
    print('✅ BigGo canonical 四檔 diff 已確認；guard pass')
    return 0


def run_alert(status_path):
    status = _load_status(status_path)
    if status is None:
        print('❌ BigGo alert：status artifact 缺失／無效（fail-closed）')
        return 1
    if not isinstance(status.get('status'), str) or not status.get('status').strip():
        print('❌ BigGo alert：status artifact 冇有效 status（fail-closed）')
        return 1
    detail = _summary(status)
    print(f'BigGo alert：{detail}')
    if status.get('alert') is True or status.get('status') in ALERT_STATUSES:
        print(f"::error::BigGo 需要人手覆核：status={status.get('status')} "
              f"rerun={status.get('rerun')}")
        return 1
    print('✅ BigGo 無需人手覆核')
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='BigGo stage artifact guard／alert')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--status', default=None)
    ap.add_argument('--alert-only', action='store_true')
    ap.add_argument('--expect-clean', action='store_true')
    args = ap.parse_args(argv)
    try:
        if args.alert_only:
            return run_alert(args.status)
        return run_guard(args.repo, args.status, expect_clean=args.expect_clean)
    except RuntimeError as e:
        print(f'❌ guard 執行錯誤：{e}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
