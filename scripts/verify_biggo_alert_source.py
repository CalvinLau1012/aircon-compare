#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核實 BigGo 警報 workflow_run 的來源 run 係受信任 daily（唯讀）。

只接受：
  - workflow name/path == 每日 daily-update.yml；
  - event ∈ {schedule, workflow_dispatch}（拒絕 PR／fork）；
  - conclusion == success（daily 本身綠；BigGo 警報唔應拉紅 source run）；
  - head_repository.full_name == 目前 repository（同 repo，非 fork）；
  - head_branch == master；
  - head_sha 係完整 40-hex；
  - 冇 pull_requests 關聯；
  - 可選比對 expected run id。

輸出只含錯誤原因（唔含 token／私人識別）。rc：0 接受；1 拒絕；2 輸入錯誤。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DAILY_WORKFLOW_NAME = '每日偵測 · 新機分批更新'
DAILY_WORKFLOW_PATH = '.github/workflows/daily-update.yml'
ALLOWED_EVENTS = ('schedule', 'workflow_dispatch')
# daily 完成即可（success 正常；failure 可能係 guard 阻斷未確認 diff）；
# alert workflow 唔會 deploy，只讀 artifact。cancelled／skipped 一律拒絕。
ALLOWED_CONCLUSIONS = ('success', 'failure')


def verify(payload, expected_run_id=None):
    errors = []
    if not isinstance(payload, dict):
        return ['event payload 唔係 object']
    wr = payload.get('workflow_run')
    if not isinstance(wr, dict):
        return ['event payload 冇 workflow_run']
    if wr.get('name') != DAILY_WORKFLOW_NAME:
        errors.append(f"workflow name 唔 match：{wr.get('name')!r}")
    if wr.get('path') != DAILY_WORKFLOW_PATH:
        errors.append(f"workflow path 唔 match：{wr.get('path')!r}")
    if wr.get('event') not in ALLOWED_EVENTS:
        errors.append(f"event 唔受信任：{wr.get('event')!r}")
    if wr.get('conclusion') not in ALLOWED_CONCLUSIONS:
        errors.append(f"conclusion 唔受信任：{wr.get('conclusion')!r}")
    head_repo = (wr.get('head_repository') or {}).get('full_name')
    base_repo = (payload.get('repository') or {}).get('full_name')
    if not head_repo or head_repo != base_repo:
        errors.append(f'head_repository 唔係本 repo：{head_repo!r} != {base_repo!r}')
    if wr.get('head_branch') != 'master':
        errors.append(f"head_branch 唔係 master：{wr.get('head_branch')!r}")
    sha = wr.get('head_sha') or ''
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        errors.append(f'head_sha 唔係完整 40-hex：{sha!r}')
    if wr.get('pull_requests'):
        errors.append('workflow_run 帶 pull_requests（拒絕 PR 來源）')
    if expected_run_id is not None and str(wr.get('id')) != str(expected_run_id):
        errors.append(f"run id 唔 match：{wr.get('id')!r} != {expected_run_id!r}")
    return errors


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='核實 BigGo 警報來源 run')
    ap.add_argument('--event', required=True, help='workflow event JSON path')
    ap.add_argument('--run-id', default=None, help='expected workflow_run id')
    args = ap.parse_args(argv)
    try:
        with open(args.event, encoding='utf-8') as f:
            payload = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ event 讀取／解析失敗：{type(e).__name__}', file=sys.stderr)
        return 2
    errors = verify(payload, expected_run_id=args.run_id)
    if errors:
        print('❌ BigGo 警報來源 run 唔受信任（fail-closed）：', file=sys.stderr)
        for err in errors:
            print(f'  - {err}', file=sys.stderr)
        return 1
    print('✅ BigGo 警報來源 run 受信任（success／master／本 repo／非 PR）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
