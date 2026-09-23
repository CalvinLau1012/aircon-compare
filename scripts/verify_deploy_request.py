#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證部署請求（每日產物 → Pages pipeline 嘅信任邊界；R7 收緊）。

只喺 Pages build job 內、checkout 之後執行：
  - `--event push|workflow_dispatch`：commit 必須等於 checkout 咗嘅 HEAD，而且係
    origin/master 祖先（human push／master 手動 dispatch 路徑）。
  - `--event repository_dispatch`：經 GitHub API 有界 polling 核實
    `client_payload.sourceRunId`／`sourceRunAttempt` 對應嘅 run：
      * run id／run_attempt 精確等於 payload；workflow path 必須係
        `.github/workflows/daily-update.yml`（API 帶 `@ref` 會安全解析）；
      * 同 repo、event schedule/workflow_dispatch、head_branch master、
        completed + success；`queued`／`in_progress` 只可以短暫存在，timeout 即失敗；
      * 部署 commit 必須只有一個 parent，而且 parent == source run head_sha
        （唔接受任意祖先，亦拒絕 merge commit）；
      * checkout 內 `metadata.json` 必須 `workflowRunId == sourceRunId` 而且
        `commit == source run head_sha`。
  - 任何一項唔符即非零退出（fail-closed）；錯誤／report 唔會包含 token、
    私人 URL 或 API response body。

用法（CI）：
  python scripts/verify_deploy_request.py --event repository_dispatch \
      --commit "$COMMIT" --source-run-id "$RUN_ID" --source-run-attempt "$ATTEMPT" \
      --poll-timeout 300 --poll-interval 10
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
ID_RE = re.compile(r'^[0-9]+$')
DAILY_WORKFLOW_PATH = '.github/workflows/daily-update.yml'
RUNNING_STATES = ('queued', 'in_progress', 'waiting', 'requested', 'pending')


class VerifyError(RuntimeError):
    pass


def _git(*args, cwd=None):
    return subprocess.run(['git'] + list(args), cwd=cwd or BASE,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')


def _default_api(path, token):
    url = 'https://api.github.com' + path
    req = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'aircon-deploy-verify',
        **({'Authorization': f'Bearer {token}'} if token else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise VerifyError(f'GitHub API 連線失敗：{type(e).__name__}') from e


def _normalize_workflow_path(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise VerifyError('source run 缺少 workflow path')
    if any(c in raw for c in ('\x00', '\n', '\r', '\t')):
        raise VerifyError('source run workflow path 有非法字元')
    raw = raw.strip()
    if '@' in raw:
        path, ref = raw.split('@', 1)
        if ref.strip() != 'refs/heads/master':
            raise VerifyError('source run workflow path ref 唔係 refs/heads/master')
        raw = path
    path = raw.strip()
    while path.startswith('./'):
        path = path[2:]
    return path


def _fetch_run(api, repo, run_id, token, poll_interval, timeout, sleep):
    max_polls = max(0, int(timeout // poll_interval)) if poll_interval > 0 else 0
    polls = 0
    while True:
        status, run = api(f'/repos/{repo}/actions/runs/{run_id}', token)
        if status != 200 or not isinstance(run, dict):
            raise VerifyError(f'source run 查詢失敗（HTTP {status}）')
        state = run.get('status')
        if state == 'completed':
            return run
        if state not in RUNNING_STATES:
            raise VerifyError(f'source run 狀態不可信：{state!r}')
        if polls >= max_polls:
            raise VerifyError(f'source run 未完成（timeout，已 poll {polls} 次）')
        polls += 1
        sleep(poll_interval)


def _verify_direct_parent(commit, src_sha, cwd):
    proc = _git('rev-list', '--parents', '-n', '1', commit, cwd=cwd)
    if proc.returncode != 0:
        raise VerifyError('讀取部署 commit parents 失敗')
    tokens = proc.stdout.split()
    if len(tokens) != 2 or tokens[0] != commit:
        raise VerifyError('部署 commit 必須只有一個 parent（拒絕 merge／orphan commit）')
    if tokens[1] != src_sha:
        raise VerifyError('部署 commit parent 唔等於 source run head_sha（拒絕任意祖先）')


def _verify_metadata_binding(metadata_path, source_run_id, src_sha):
    if not metadata_path or not os.path.isfile(metadata_path):
        raise VerifyError('搵唔到 metadata.json，無法綁定 source run')
    try:
        with open(metadata_path, encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        raise VerifyError(f'metadata.json 讀取失敗：{type(e).__name__}') from e
    if not isinstance(meta, dict):
        raise VerifyError('metadata.json 唔係 object')
    if meta.get('workflowRunId') != str(source_run_id):
        raise VerifyError('metadata.workflowRunId 唔等於 source run id')
    if meta.get('commit') != src_sha:
        raise VerifyError('metadata.commit 唔等於 source run head_sha')


def verify(event, commit, repo, source_run_id=None, source_run_attempt=None,
           api=None, token=None, cwd=None, sleep=None, poll_timeout=300,
           poll_interval=10, metadata_path=None):
    if not SHA_RE.match(commit or ''):
        raise VerifyError('commit 唔係 full 40-hex SHA')
    if event not in ('push', 'workflow_dispatch', 'repository_dispatch'):
        raise VerifyError(f'不支援嘅 event：{event}')
    head = _git('rev-parse', 'HEAD', cwd=cwd)
    if head.returncode != 0 or head.stdout.strip() != commit:
        raise VerifyError(f'checkout HEAD 唔等於部署 commit（checkout={head.stdout.strip()[:12]}）')
    merge = subprocess.run(['git', 'merge-base', '--is-ancestor', commit, 'origin/master'],
                           cwd=cwd or BASE, capture_output=True)
    if merge.returncode != 0:
        raise VerifyError('部署 commit 唔係 origin/master 祖先（拒絕未合併／PR 產物）')
    if event != 'repository_dispatch':
        return {'ok': True, 'event': event, 'commit': commit}
    if not ID_RE.match(source_run_id or '') or not ID_RE.match(source_run_attempt or ''):
        raise VerifyError('source run id／attempt 格式錯誤')
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo or ''):
        raise VerifyError('repo 格式錯誤')
    sleep = sleep or time.sleep
    run = _fetch_run(api or _default_api, repo, source_run_id, token,
                     poll_interval, poll_timeout, sleep)
    if str(run.get('id')) != source_run_id:
        raise VerifyError('source run id 唔一致')
    if run.get('run_attempt') != int(source_run_attempt):
        raise VerifyError('source run attempt 唔一致')
    if _normalize_workflow_path(run.get('path')) != DAILY_WORKFLOW_PATH:
        raise VerifyError('source run workflow path 唔係 daily-update.yml')
    if (run.get('repository') or {}).get('full_name') != repo:
        raise VerifyError('source run 唔屬於本 repo')
    if run.get('event') not in ('schedule', 'workflow_dispatch'):
        raise VerifyError(f"source run event 唔可信：{run.get('event')}")
    if run.get('conclusion') != 'success':
        raise VerifyError(f"source run conclusion 唔係 success：{run.get('conclusion')!r}")
    if run.get('head_branch') != 'master':
        raise VerifyError('source run 唔係喺 master')
    src_sha = run.get('head_sha', '')
    if not SHA_RE.match(src_sha):
        raise VerifyError('source run head_sha 格式錯誤')
    _verify_direct_parent(commit, src_sha, cwd)
    meta_path = metadata_path or os.path.join(cwd or BASE, 'metadata.json')
    _verify_metadata_binding(meta_path, source_run_id, src_sha)
    return {'ok': True, 'event': event, 'commit': commit,
            'sourceRunId': source_run_id, 'sourceRunAttempt': source_run_attempt,
            'runAttempt': run.get('run_attempt'), 'workflowPath': DAILY_WORKFLOW_PATH,
            'sourceRunHeadSha': src_sha, 'parent': src_sha}


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='驗證 Pages 部署請求信任邊界（R7）')
    ap.add_argument('--event', required=True)
    ap.add_argument('--commit', required=True)
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    ap.add_argument('--source-run-id', default=None)
    ap.add_argument('--source-run-attempt', default=None)
    ap.add_argument('--metadata', default=None)
    ap.add_argument('--cwd', default=None, help='checkout 根（預設 script repo 根）')
    ap.add_argument('--poll-timeout', type=int, default=300)
    ap.add_argument('--poll-interval', type=int, default=10)
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    try:
        result = verify(args.event, args.commit, args.repo,
                        source_run_id=args.source_run_id,
                        source_run_attempt=args.source_run_attempt,
                        token=token, cwd=args.cwd or BASE,
                        poll_timeout=args.poll_timeout,
                        poll_interval=args.poll_interval,
                        metadata_path=args.metadata)
    except VerifyError as e:
        print(f'❌ 部署請求驗證失敗（fail-closed）：{e}', file=sys.stderr)
        return 1
    if args.report:
        with open(args.report, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"✅ 部署請求有效：event={result['event']} commit={result['commit'][:12]}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
