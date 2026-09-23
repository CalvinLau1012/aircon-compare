#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""喺每日更新成功 push 之後，精確 dispatch Pages 部署。

GitHub 文件：用 workflow 預設 `GITHUB_TOKEN` 執行 `git push`，唔會觸發
`push` workflow run；但 `repository_dispatch` 係例外，仍然會建立新 run。
所以 daily 用呢個腳本，帶住**已 push 嘅新產物 commit** 同**來源 run id** 去
dispatch `Pages 部署（Actions）`；Pages workflow 會自行用 GitHub API 核實
來源 run 成功、commit 係 master 祖先，先至建置／部署。

安全：
  - 只讀 `token-env`（預設 GITHUB_TOKEN / GH_TOKEN），唔會寫入 log；
  - 唔會 fallback 去「最新 master」，commit 必須係明確 full 40-hex；
  - API 失敗即非零退出（daily run 會紅，唔會靜默當已部署）。
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

SHA_RE = re.compile(r'^[0-9a-f]{40}$')
ID_RE = re.compile(r'^[0-9]+$')


class DispatchError(RuntimeError):
    pass


def dispatch(repo, commit, source_run_id, source_run_attempt, token,
             event_type='aircon-pages-deploy', api=None):
    if not re.match(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$', repo or ''):
        raise DispatchError('repo 格式錯誤')
    if not SHA_RE.match(commit or ''):
        raise DispatchError('commit 必須係 full 40-hex SHA')
    if not ID_RE.match(source_run_id or '') or not ID_RE.match(source_run_attempt or ''):
        raise DispatchError('source run id／attempt 必須係數字')
    if not token:
        raise DispatchError('缺少 GitHub token（GITHUB_TOKEN／GH_TOKEN）')
    payload = {
        'event_type': event_type,
        'client_payload': {
            'commit': commit,
            'sourceRunId': source_run_id,
            'sourceRunAttempt': source_run_attempt,
        },
    }
    body = json.dumps(payload).encode('utf-8')
    if api is None:
        def api(path, data):
            req = urllib.request.Request(
                'https://api.github.com' + path, data=data, method='POST',
                headers={'Accept': 'application/vnd.github+json',
                         'X-GitHub-Api-Version': '2022-11-28',
                         'User-Agent': 'aircon-dispatch-pages',
                         'Content-Type': 'application/json',
                         'Authorization': f'Bearer {token}'})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status
            except urllib.error.HTTPError as e:
                return e.code
            except (urllib.error.URLError, TimeoutError) as e:
                raise DispatchError(f'GitHub API 連線失敗：{type(e).__name__}') from e
    status = api(f'/repos/{repo}/dispatches', body)
    if status not in (204,):
        raise DispatchError(f'dispatch 失敗（HTTP {status}）')
    return {'ok': True, 'eventType': event_type, 'commit': commit,
            'sourceRunId': source_run_id, 'sourceRunAttempt': source_run_attempt}


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='dispatch Pages 部署（精確 commit）')
    ap.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
    ap.add_argument('--commit', required=True)
    ap.add_argument('--source-run-id', required=True)
    ap.add_argument('--source-run-attempt', required=True)
    ap.add_argument('--event-type', default='aircon-pages-deploy')
    ap.add_argument('--token-env', default='GITHUB_TOKEN')
    args = ap.parse_args(argv)
    token = os.environ.get(args.token_env) or os.environ.get('GH_TOKEN')
    try:
        result = dispatch(args.repo, args.commit, args.source_run_id,
                          args.source_run_attempt, token,
                          event_type=args.event_type)
    except (DispatchError, OSError) as e:
        print(f'❌ Pages dispatch 失敗（未當已部署）：{e}', file=sys.stderr)
        return 1
    print(f"✅ 已 dispatch Pages 部署：commit={result['commit'][:12]} "
          f"sourceRun={result['sourceRunId']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
