#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D8-A：資料新鮮度檢查（threshold = age > 72h）。

- datasetRetrievedAt 必須係有效 UTC Z timestamp；
- missing／invalid／future timestamp 一律 hard failure；
- age > 72h 才 stale（71:59:59 同 72:00:00 都 pass）；
- metadata Schema／payload hash／Pages 一致性由 workflow 內 postdeploy_check 先驗。
"""
import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
import urllib.request

THRESHOLD = _dt.timedelta(hours=72)
REPORT_SCHEMA_VERSION = 1


def parse_utc_z(value):
    if not isinstance(value, str):
        raise ValueError('timestamp 必須係字串')
    m = re.fullmatch(r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?Z', value)
    if not m:
        raise ValueError('timestamp 必須係 UTC Z 格式（可帶小數秒）')
    base = f'{m.group(1)}T{m.group(2)}'
    dt = _dt.datetime.fromisoformat(base).replace(tzinfo=_dt.timezone.utc)
    if m.group(3):
        micro = (m.group(3) + '000000')[:6]
        dt = dt.replace(microsecond=int(micro))
    return dt


def evaluate(metadata, now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if not isinstance(metadata, dict):
        return {'ok': False, 'stale': False, 'ageSeconds': None,
                'reason': 'metadata_not_object'}
    try:
        retrieved = parse_utc_z(metadata.get('datasetRetrievedAt'))
    except ValueError:
        return {'ok': False, 'stale': False, 'ageSeconds': None,
                'reason': 'missing_or_invalid_datasetRetrievedAt'}
    if retrieved > now:
        return {'ok': False, 'stale': False, 'ageSeconds': None,
                'reason': 'future_datasetRetrievedAt'}
    age = (now - retrieved).total_seconds()
    if age > THRESHOLD.total_seconds():
        return {'ok': True, 'stale': True, 'ageSeconds': age, 'reason': 'stale_over_72h'}
    return {'ok': True, 'stale': False, 'ageSeconds': age, 'reason': 'fresh'}


def plan_issue(existing_issue, result):
    """回傳去重告警計劃；existing_issue 係 dict 或 None。"""
    reason = result.get('reason')
    stale_or_bad = (not result.get('ok')) or result.get('stale')
    if not stale_or_bad:
        if existing_issue:
            return {'action': 'close', 'reason': 'recovered'}
        return {'action': 'noop', 'reason': 'fresh'}
    body = (f"Freshness monitor alert.\n\n"
            f"- reason: `{reason}`\n"
            f"- ageSeconds: {result.get('ageSeconds')}\n"
            f"- threshold: age > 72h\n")
    if not existing_issue:
        return {'action': 'create', 'reason': reason, 'body': body}
    if existing_issue.get('body') == body:
        return {'action': 'noop', 'reason': 'duplicate'}
    return {'action': 'update', 'reason': reason, 'body': body}


def fetch_metadata(url, timeout=30):
    sep = '&' if '?' in url else '?'
    req = urllib.request.Request(f'{url}{sep}cb={int(time.time())}',
                                 headers={'Cache-Control': 'no-cache', 'User-Agent': 'aircon-freshness/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='72h 新鮮度檢查')
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--metadata-file')
    src.add_argument('--metadata-url')
    ap.add_argument('--report', required=True)
    args = ap.parse_args(argv)
    try:
        if args.metadata_file:
            with open(args.metadata_file, encoding='utf-8') as f:
                meta = json.load(f)
        else:
            meta = fetch_metadata(args.metadata_url)
    except Exception as e:  # noqa: BLE001 - network／JSON error 一律 fail-closed
        result = {'ok': False, 'stale': False, 'ageSeconds': None,
                  'reason': f'fetch_error:{type(e).__name__}'}
    else:
        result = evaluate(meta)
    report = {
        'schemaVersion': REPORT_SCHEMA_VERSION,
        'thresholdSeconds': int(THRESHOLD.total_seconds()),
        'checkedAt': _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        **result,
    }
    # checkedAt 有機會同測試注入 now 不同；只保留結果判定。
    try:
        out = os.path.abspath(args.report)
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except OSError as e:
        print(f'❌ freshness report 寫入失敗：{e}', file=sys.stderr)
        return 1
    label = '❌' if (not result['ok']) else ('⚠️ stale' if result['stale'] else '✅ fresh')
    print(f"{label} freshness: reason={result['reason']} ageSeconds={result['ageSeconds']} "
          f"threshold={int(THRESHOLD.total_seconds())}s report={out}")
    return 0 if (result['ok'] and not result['stale']) else 1


if __name__ == '__main__':
    sys.exit(main())
