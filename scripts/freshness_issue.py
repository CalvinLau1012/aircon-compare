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
import re
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
from monitor_presentation import sanitize_source_states  # noqa: E402

LABEL = 'monitoring:freshness'
TITLE = '[MONITOR] 資料新鮮度、部署與來源健康警報'


def _title_for(result):
    """Name the issue by the hardest real failing axis first."""
    if result.get('stale') is True or result.get('ok') is not True:
        return '[MONITOR] 資料過期／取得時間警報'
    if result.get('postdeployOk') is False or result.get('deploymentMismatch') is True:
        return '[MONITOR] 部署一致性警報'
    return '[MONITOR] 來源健康警報'


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


def _write_report(path, data):
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return None
    except OSError:
        return 'report-write-failed'


def _recovery_marker(fp):
    return '<!-- aircon-freshness-recovery: ' + fp + ' -->'


def find_open_issue(token, repo, label=LABEL):
    q = urllib.parse.urlencode({'state': 'open', 'labels': label, 'per_page': 100})
    items = _api(token, 'GET', f'/repos/{repo}/issues?{q}')
    if not isinstance(items, list):
        raise RuntimeError('GitHub issues 回應唔係 list')
    return items[0] if items else None


def _health_fact(health, expected_identity=None):
    """Strictly validate check_health_summary's producer report contract."""
    if not isinstance(health, dict):
        return False, 'health:invalid', ['health:invalid'], False
    if type(health.get('schemaVersion')) is not int or health['schemaVersion'] != 1:
        return False, 'health:invalid', ['health:invalid'], False
    configured = health.get('configured')
    status = health.get('status')
    ok = health.get('ok')
    if 'failures' not in health or not isinstance(health.get('failures'), list):
        return False, 'health:invalid', ['health:invalid'], False
    failures = health['failures']
    skipped = health.get('skipped', False)
    if not isinstance(configured, bool) or not isinstance(skipped, bool):
        return False, 'health:invalid', ['health:invalid'], False
    if status not in ('healthy', 'unhealthy', 'unknown'):
        return False, 'health:invalid', ['health:invalid'], False
    if ok is True:
        if status != 'healthy' or configured is not True or failures or skipped:
            return False, 'health:invalid', ['health:invalid'], False
    elif ok is False:
        if status != 'unhealthy' or not failures:
            return False, 'health:invalid', ['health:invalid'], False
    elif ok is None:
        if status != 'unknown' or not (configured is False or health.get('skipped') is True):
            return False, 'health:invalid', ['health:invalid'], False
    else:
        return False, 'health:invalid', ['health:invalid'], False
    # The report must be produced in this run and bound to the same identity.
    ident_ok = (isinstance(health.get('sourceRunId'), int)
                and not isinstance(health.get('sourceRunId'), bool)
                and health.get('sourceRunId') > 0
                and isinstance(health.get('headSha'), str)
                and re.fullmatch(r'[0-9a-f]{40}', health.get('headSha'))
                and health.get('sourceEvent') in ('schedule', 'workflow_dispatch'))
    if not ident_ok:
        return False, 'health:identity-invalid', ['health:identity-invalid'], False
    if expected_identity is not None:
        run_id, head, event = expected_identity
        if (health.get('sourceRunId') != run_id or health.get('headSha') != head
                or health.get('sourceEvent') != event):
            return False, 'health:identity-mismatch', ['health:identity-mismatch'], False
    codes = set()
    for item in failures:
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
    if not configured or skipped:
        codes.add('health:unconfigured')
    if ok is True and codes:
        return False, 'health:invalid', ['health:invalid'], False
    if ok is True:
        return True, 'ok', [], True
    if not codes:
        codes.add('health:unhealthy')
    return ok is True, ('ok' if ok is True else ','.join(sorted(codes))), sorted(codes), True


def _postdeploy_fact(post):
    """Strict producer contract; public failure codes never echo raw details."""
    if not isinstance(post, dict):
        return False, None, [], False
    if not isinstance(post.get('ok'), bool) or not isinstance(post.get('failures'), list) \
            or not isinstance(post.get('checks'), list):
        return False, None, [], False
    if not isinstance(post.get('generatedAt'), str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', post['generatedAt']):
        return False, None, [], False
    try:
        from check_freshness import parse_utc_z
        parse_utc_z(post['generatedAt'])
    except ValueError:
        return False, None, [], False
    if not isinstance(post.get('baseUrl'), str) or not post['baseUrl'].strip():
        return False, None, [], False
    if not post['checks'] or not isinstance(post['checks'], list):
        return False, None, [], False
    for entry in post['checks']:
        if not isinstance(entry, dict) or not isinstance(entry.get('pass'), bool) \
                or not isinstance(entry.get('check'), str) or not entry['check']:
            return False, None, [], False
    if post.get('ok') is True and (post['failures'] or not all(e['pass'] for e in post['checks'])):
        return False, None, [], False
    # A successful producer report must include the core deployment checks;
    # a truncated list containing only one passing check is not evidence.
    required = {
        'metadata.expected_schema_valid', 'metadata.source_status_truthful',
        'manifest.paths', 'metadata.http_200', 'metadata.full_object_equal',
        'metadata.online_schema_valid', 'metadata.releasePayloadHash',
        'metadata.datasetHash', 'payload.http.index.html',
        'payload.http.空調對比報告.pdf', 'payload.http.emsd_空調能源標籤.csv',
        'payload.releasePayloadHash', 'payload.csv_datasetHash',
        'payload.csv_online_metadata_hash', 'payload.pdf_magic',
        'payload.pdf_matches_metadata',
    }
    names = [entry['check'] for entry in post['checks']]
    if post['ok'] and (not required.issubset(names) or len(names) != len(set(names))):
        return False, None, [], False
    if post.get('ok') is False and not post['failures']:
        return False, None, [], False
    for entry in post['failures']:
        if not isinstance(entry, dict) or entry.get('pass') is not False:
            return False, None, [], False
    allowed_prefixes = ('metadata.', 'payload.', 'browser.', 'http.', 'repro.')
    ids = []
    for item in post['failures']:
        name = item.get('check') or item.get('name') if isinstance(item, dict) else None
        value = str(name or '')
        code = value if value.startswith(allowed_prefixes) and re.fullmatch(
            r'[A-Za-z0-9_.:-]{1,80}', value) else 'postdeploy:unknown'
        ids.append(code)
    mismatch = any(x.startswith(('metadata.', 'payload.')) for x in ids)
    return post['ok'] is True, {'mismatch': mismatch, 'failures': ids}, ids, True



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
    # Identity is required before any issue read/write side effect.
    identity_ok = (isinstance(args.run_id, str) and args.run_id.isdigit()
                   and int(args.run_id) > 0
                   and isinstance(args.head, str) and re.fullmatch(r'[0-9a-f]{40}', args.head)
                   and args.event in ('schedule', 'workflow_dispatch'))
    if not identity_ok:
        result = {'ok': False, 'stale': False, 'ageSeconds': None,
                  'reason': 'invalid-source-identity'}
        out = {'schemaVersion': 1, 'action': 'blocked-identity',
               'appliedAction': 'none', 'reason': 'invalid-source-identity',
               'check': result, 'issueNumber': None,
               'sourceRunId': None, 'headSha': None, 'sourceEvent': None,
               'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        if _write_report(args.report, out):
            return 1
        return 1
    expected_identity = (int(args.run_id), args.head, args.event)
    result = None
    post_ok = False
    post_known = False
    health_known = False
    health_states = {}
    deployment = None
    health_ok = False
    health_reason = 'health-report-missing'
    health_failures = ['health:missing']
    post_failures = []
    if args.health_report:
        try:
            health = json.load(open(args.health_report, encoding='utf-8'))
            health_ok, health_reason, health_failures, health_known = _health_fact(
                health, expected_identity)
            health_states = sanitize_source_states(health.get('sourceStates')) if health_known else {}
        except (OSError, ValueError, TypeError, AttributeError):
            health_ok = False; health_known = False; health_reason = 'health:invalid'
            health_failures = ['health:invalid']
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
                post_ok, deployment, post_failures, post_known = _postdeploy_fact(post)
                if not post_known:
                    raise ValueError('contract')
            except (OSError, ValueError, TypeError):
                post_ok = False; post_known = False
                result = {'ok': False, 'stale': False, 'ageSeconds': None,
                          'reason': 'postdeploy_report_invalid'}
                post_failures = ['postdeploy:invalid']
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
    result['postdeployKnown'] = post_known
    result['healthSummaryOk'] = health_ok
    result['healthKnown'] = health_known
    if health_states:
        result['sourceStates'] = health_states
    result['healthReason'] = health_reason
    result['healthFailures'] = health_failures
    result['postdeployFailures'] = post_failures
    # Save the check result before any issue read/mutation.  This report is
    # explicitly pending; it is upgraded only after a successful action.
    pre = {'schemaVersion': 1, 'action': 'pending', 'plannedAction': 'pending',
           'appliedAction': 'none', 'reason': 'evidence-saved',
           'check': result, 'issueNumber': None,
           'sourceRunId': int(args.run_id), 'headSha': args.head,
           'sourceEvent': args.event,
           'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if _write_report(args.report, pre):
        return 1
    try:
        existing = find_open_issue(token, args.repo, args.label)
    except RuntimeError as e:
        print(f'❌ 讀取 monitoring issue 失敗：{e}', file=sys.stderr)
        return 1
    plan = plan_issue(existing, result)
    body = (plan.get('body') or
            f"Freshness monitor recovered.\n\n- reason: `{result.get('reason')}`\n"
            f"- ageSeconds: {result.get('ageSeconds')}\n")
    out = {
        'schemaVersion': 1,
        'action': plan['action'],
        'plannedAction': plan['action'],
        'appliedAction': 'none',
        'reason': plan.get('reason'),
        'check': result,
        'issueNumber': existing.get('number') if existing else None,
        'sourceRunId': int(args.run_id),
        'headSha': args.head,
        'sourceEvent': args.event,
        'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    if _write_report(args.report, out):
        return 1
    try:
        if plan['action'] == 'create':
            _api(token, 'POST', f'/repos/{args.repo}/issues',
                 {'title': _title_for(result), 'body': body, 'labels': [args.label]})
        elif plan['action'] == 'update' and existing:
            _api(token, 'PATCH', f"/repos/{args.repo}/issues/{existing['number']}",
                 {'title': _title_for(result), 'body': body})
        elif plan['action'] == 'close' and existing:
            marker = _recovery_marker(plan['fingerprint'])
            body = existing.get('body') or ''
            already = marker in body
            if not already:
                # Persist the retry marker before the public comment.  If the
                # following comment/close fails, the next run can retry close
                # without posting the same recovery comment twice.
                _api(token, 'PATCH', f"/repos/{args.repo}/issues/{existing['number']}",
                     {'body': body + '\n' + marker})
                _api(token, 'POST', f"/repos/{args.repo}/issues/{existing['number']}/comments",
                     {'body': 'Freshness recovered at '
                              + time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
            _api(token, 'PATCH', f"/repos/{args.repo}/issues/{existing['number']}",
                 {'state': 'closed', 'state_reason': 'completed'})
    except RuntimeError as e:
        print(f'❌ 更新 monitoring issue 失敗：{e}', file=sys.stderr)
        return 1
    out['appliedAction'] = plan['action']
    if _write_report(args.report, out):
        return 1
    print(f"✅ freshness issue action={out['action']} reason={out['reason']} "
          f"ok={result.get('ok')} stale={result.get('stale')}")
    fresh_ok = bool(result.get('ok')) and not bool(result.get('stale')) \
        and not bool(result.get('deploymentMismatch'))
    return 0 if (fresh_ok and post_ok and health_ok) else 1


if __name__ == '__main__':
    sys.exit(main())
