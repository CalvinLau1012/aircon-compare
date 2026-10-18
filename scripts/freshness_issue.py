#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D8-A：新鮮度／部署一致性告警去重。

讀 online metadata +（可選）postdeploy report；以 check_freshness.plan_issue 決定
create／update／close／noop；只維護單一 label 嘅 open issue。Token 只由環境讀取，
唔會寫入 report 或 log。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
for _p in (BASE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from check_freshness import evaluate, plan_issue  # noqa: E402

LABEL = 'monitoring:freshness'
TITLE = '[MONITOR] 資料新鮮度 / 部署一致性警報'


def _api(token, method, path, data=None):
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request('https://api.github.com' + path, data=body, method=method,
                                 headers={
                                     'Authorization': f'Bearer {token}',
                                     'Accept': 'application/vnd.github+json',
                                     'User-Agent': 'aircon-freshness-monitor/1.0',
                                 })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8')
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        # 唔輸出 response body（可能含平台資訊）
        raise RuntimeError(f'GitHub API {method} {path} 失敗：HTTP {e.code}')


def find_open_issue(token, repo, label=LABEL):
    q = urllib.parse.urlencode({'state': 'open', 'labels': label, 'per_page': 100})
    items = _api(token, 'GET', f'/repos/{repo}/issues?{q}')
    if not isinstance(items, list):
        raise RuntimeError('GitHub issues 回應唔係 list')
    return items[0] if items else None


def _safe_health_fact(health):
    """Map the real check_health_summary failures/status to fixed safe codes."""
    if not isinstance(health, dict):
        return False, 'health-report-invalid'
    if health.get('ok') is True:
        return True, 'ok'
    values = health.get('failures')
    codes = set()
    if isinstance(values, list):
        for item in values:
            text = str(item or '').lower()
            if text.startswith('fetch:'):
                codes.add('health:fetch')
            elif text.startswith('summary:'):
                token = text.split(':', 1)[1]
                if any(x in token for x in ('emsd', 'biggo', 'official')):
                    for source in ('emsd', 'biggo', 'official'):
                        if source in token:
                            codes.add('health:' + source)
                elif 'hash' in token or 'manifest' in token or 'binding' in token:
                    codes.add('health:integrity')
                elif 'not-healthy' in token or 'failures' in token:
                    codes.add('health:unhealthy')
                else:
                    codes.add('health:unknown')
            else:
                codes.add('health:unknown')
    sources = health.get('sources')
    if isinstance(sources, dict):
        for name in ('emsd', 'biggo', 'official'):
            block = sources.get(name)
            if not isinstance(block, dict) or block.get('status') != 'ok':
                codes.add('health:' + name)
    if health.get('configured') is False or health.get('skipped') is True:
        codes.add('health:unconfigured')
    if not codes:
        codes.add('health:unhealthy')
    return False, ','.join(sorted(codes))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='新鮮度監控告警去重')
    ap.add_argument('--metadata-url', required=True)
    ap.add_argument('--repo', required=True, help='owner/name')
    ap.add_argument('--postdeploy-report', default=None)
    ap.add_argument('--health-report', default=None)
    ap.add_argument('--report', required=True)
    ap.add_argument('--label', default=LABEL)
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--head', default=None)
    ap.add_argument('--event', default=None)
    args = ap.parse_args(argv)
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if not token:
        print('❌ 缺少 GITHUB_TOKEN／GH_TOKEN', file=sys.stderr)
        return 1
    result = None
    post_ok = False
    deployment = None
    health_ok = False
    health_reason = 'health-report-missing'
    if args.health_report:
        try:
            health = json.load(open(args.health_report, encoding='utf-8'))
            health_ok, health_reason = _safe_health_fact(health)
        except (OSError, ValueError, TypeError, AttributeError):
            health_ok = False
            health_reason = 'health-report-invalid'
            result = {'ok': False, 'stale': False, 'ageSeconds': None,
                      'reason': 'health_report_invalid'}
    if args.postdeploy_report:
        if not os.path.isfile(args.postdeploy_report):
            post_ok = False
            result = {'ok': False, 'stale': False, 'ageSeconds': None,
                      'reason': 'postdeploy_report_missing'}
        else:
            try:
                post = json.load(open(args.postdeploy_report, encoding='utf-8'))
                if not isinstance(post, dict):
                    raise ValueError('not an object')
                post_ok = post.get('ok') is True and not post.get('failures')
                failures = post.get('failures') or []
                if not isinstance(failures, list):
                    raise ValueError('failures not list')
                ids = [str(f)[:120] for f in failures]
                mismatch = any(f.startswith(('metadata.', 'payload.')) for f in ids)
                deployment = {'mismatch': mismatch, 'failures': ids}
            except (OSError, ValueError, TypeError):
                post_ok = False
                result = {'ok': False, 'stale': False, 'ageSeconds': None,
                          'reason': 'postdeploy_report_invalid'}
    if result is None:
        try:
            from check_freshness import fetch_metadata
            meta = fetch_metadata(args.metadata_url)
        except Exception as e:  # noqa: BLE001
            result = {'ok': False, 'stale': False, 'ageSeconds': None,
                      'reason': f'fetch_error:{type(e).__name__}'}
        else:
            result = evaluate(meta, deployment=deployment)
    result['postdeployOk'] = post_ok
    result['healthSummaryOk'] = health_ok
    result['healthReason'] = health_reason
    try:
        existing = find_open_issue(token, args.repo, args.label)
    except RuntimeError as e:
        print(f'❌ 讀取 monitoring issue 失敗：{e}', file=sys.stderr)
        return 1
    plan = plan_issue(existing, result)
    body = (plan.get('body') or
            f"Freshness monitor recovered.\n\n- reason: `{result.get('reason')}`\n"
            f"- ageSeconds: {result.get('ageSeconds')}\n")
    try:
        if plan['action'] == 'create':
            _api(token, 'POST', f'/repos/{args.repo}/issues',
                 {'title': TITLE, 'body': body, 'labels': [args.label]})
        elif plan['action'] == 'update' and existing:
            _api(token, 'PATCH', f"/repos/{args.repo}/issues/{existing['number']}",
                 {'body': body})
        elif plan['action'] == 'close' and existing:
            _api(token, 'POST', f"/repos/{args.repo}/issues/{existing['number']}/comments",
                 {'body': 'Freshness recovered at '
                          + time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
            _api(token, 'PATCH', f"/repos/{args.repo}/issues/{existing['number']}",
                 {'state': 'closed', 'state_reason': 'completed'})
    except RuntimeError as e:
        print(f'❌ 更新 monitoring issue 失敗：{e}', file=sys.stderr)
        return 1
    out = {
        'schemaVersion': 1,
        'action': plan['action'],
        'reason': plan.get('reason'),
        'check': result,
        'issueNumber': existing.get('number') if existing else None,
        'sourceRunId': int(args.run_id) if args.run_id and args.run_id.isdigit() else None,
        'headSha': args.head,
        'sourceEvent': args.event,
        'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    try:
        tmp = args.report + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.report)
    except OSError as e:
        print(f'❌ 監控告警 report 寫入失敗：{e}', file=sys.stderr)
        return 1
    print(f"✅ freshness issue action={out['action']} reason={out['reason']} "
          f"ok={result.get('ok')} stale={result.get('stale')}")
    fresh_ok = bool(result.get('ok')) and not bool(result.get('stale')) \
        and not bool(result.get('deploymentMismatch'))
    return 0 if (fresh_ok and post_ok and health_ok) else 1


if __name__ == '__main__':
    sys.exit(main())
