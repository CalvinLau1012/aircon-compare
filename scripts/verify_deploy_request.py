#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證部署請求（每日產物 → Pages pipeline 嘅信任邊界）。

只喺 Pages build job 內、checkout 之後執行：
  - `--event push|workflow_dispatch`：commit 必須等於 checkout 咗嘅 HEAD，而且係
    origin/master 祖先（human push／master 手動 dispatch 路徑）；
  - `--event repository_dispatch`：除咗上面，仲要經 GitHub API 核實
    `client_payload.sourceRunId` 對應一個 completed + success、event 係
    schedule/workflow_dispatch、head_branch 係 master、同屬本 repo 嘅 run，
    而且該 run 嘅 head_sha 係今次部署 commit 嘅祖先（綁定「成功 run → 產物」）；
  - 任何一項唔符即非零退出（fail-closed）；錯誤訊息唔會包含 token 或 URL 憑證。

用法（CI）：
  python scripts/verify_deploy_request.py --event repository_dispatch \
      --commit "$COMMIT" --source-run-id "$RUN_ID" --source-run-attempt "$ATTEMPT"
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
ID_RE = re.compile(r'^[0-9]+$')


class VerifyError(RuntimeError):
    pass


def _git(*args, cwd=None):
    proc = subprocess.run(['git'] + list(args), cwd=cwd or BASE,
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
    return proc


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


def verify(event, commit, repo, source_run_id=None, source_run_attempt=None,
           api=None, token=None, cwd=None):
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
    if not ID_RE.match(source_run_id or '') or not ID_RE.match(source_run_attempt or '1'):
        raise VerifyError('source run id／attempt 格式錯誤')
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo or ''):
        raise VerifyError('repo 格式錯誤')
    status, run = (api or _default_api)(f'/repos/{repo}/actions/runs/{source_run_id}', token)
    if status != 200 or not isinstance(run, dict):
        raise VerifyError(f'source run 查詢失敗（HTTP {status}）')
    if (run.get('repository') or {}).get('full_name') != repo:
        raise VerifyError('source run 唔屬於本 repo')
    if run.get('event') not in ('schedule', 'workflow_dispatch'):
        raise VerifyError(f"source run event 唔可信：{run.get('event')}")
    if run.get('status') != 'completed' or run.get('conclusion') != 'success':
        raise VerifyError('source run 未成功完成')
    if run.get('head_branch') != 'master':
        raise VerifyError('source run 唔係喺 master')
    src_sha = run.get('head_sha', '')
    if not SHA_RE.match(src_sha):
        raise VerifyError('source run head_sha 格式錯誤')
    merge = subprocess.run(['git', 'merge-base', '--is-ancestor', src_sha, commit],
                           cwd=cwd or BASE, capture_output=True)
    if merge.returncode != 0:
        raise VerifyError('source run head_sha 唔係部署 commit 祖先（冇綁到成功 run）')
    return {'ok': True, 'event': event, 'commit': commit,
            'sourceRunId': source_run_id, 'sourceRunAttempt': source_run_attempt,
            'sourceRunHeadSha': src_sha}


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='驗證 Pages 部署請求信任邊界')
    ap.add_argument('--event', required=True)
    ap.add_argument('--commit', required=True)
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    ap.add_argument('--source-run-id', default=None)
    ap.add_argument('--source-run-attempt', default=None)
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    try:
        result = verify(args.event, args.commit, args.repo,
                        source_run_id=args.source_run_id,
                        source_run_attempt=args.source_run_attempt,
                        token=token)
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
