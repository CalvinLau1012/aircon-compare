#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a sanitised source-health summary (private endpoint or file).

GitHub never needs a private host/path in the repo: the endpoint is supplied by
a repository secret.  If unconfigured, this entrypoint records that fact and
exits 0 (no fabricated health).  If configured, it validates the versioned
receipt, EMSD snapshot binding, official presence, and optional age.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
import source_status  # noqa: E402


def _load(url=None, path=None, timeout=20):
    if path:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={'Accept': 'application/json',
                                               'User-Agent': 'aircon-health/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def evaluate(status, *, now=None, max_age_hours=48):
    errors = source_status.validate_status(status)
    if errors:
        return {'ok': False, 'failures': ['schema:' + e for e in errors[:10]]}
    now = now or dt.datetime.now(dt.timezone.utc)
    failures = []
    emsd = status.get('emsd') or {}
    official = status.get('official') or {}
    for name in ('emsd', 'biggo', 'official'):
        block = status.get(name) or {}
        st = block.get('status')
        if st != 'ok':
            failures.append(f'{name}:{st}:{block.get("safeReason")}')
        if not block.get('snapshotHash') and name in ('emsd', 'biggo'):
            failures.append(f'{name}:snapshotHash-missing')
    gen = status.get('generatedAt')
    try:
        gen_dt = dt.datetime.fromisoformat(str(gen).replace('Z', '+00:00')) if gen else None
    except ValueError:
        gen_dt = None
    if gen_dt is None:
        failures.append('status:generatedAt-missing-or-invalid')
    elif gen_dt > now + dt.timedelta(minutes=5):
        failures.append('status:generatedAt-future')
    elif (now - gen_dt).total_seconds() > max_age_hours * 3600:
        failures.append('status:generatedAt-stale')
    pub = status.get('publish') or {}
    if not pub.get('deployedAt') or not pub.get('deploymentReceipt'):
        failures.append('publish:no-verified-deployment-receipt')
    if pub.get('deployedAt') and not pub.get('deploymentReceipt'):
        failures.append('publish:deployedAt-without-receipt')
    for name in ('emsd', 'biggo', 'official'):
        block = status.get(name) or {}
        ts = block.get('lastAppliedAt') or block.get('lastAcquiredAt')
        if not ts:
            continue
        try:
            when = dt.datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            failures.append(f'{name}:timestamp-invalid')
            continue
        if when > now + dt.timedelta(minutes=5):
            failures.append(f'{name}:timestamp-future')
        if (now - when).total_seconds() > max_age_hours * 3600:
            failures.append(f'{name}:age>{max_age_hours}h')
    return {'ok': not failures, 'failures': failures[:20],
            'checkedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ')}


def main(argv=None):
    ap = argparse.ArgumentParser(description='validate sanitised health summary')
    ap.add_argument('--url', default=os.environ.get('AIRCON_HEALTH_SUMMARY_URL', ''))
    ap.add_argument('--file', default=None)
    ap.add_argument('--report', required=True)
    ap.add_argument('--max-age-hours', type=float, default=48)
    args = ap.parse_args(argv)
    report = {'schemaVersion': 1, 'configured': bool(args.url or args.file),
              'status': 'unknown'}
    if not args.url and not args.file:
        report.update({'ok': None, 'skipped': True,
                       'reason': 'health-summary-not-wired',
                       'requiredAction': 'configure AIRCON_HEALTH_SUMMARY_URL secret'})
    else:
        try:
            status = _load(args.url, args.file)
            report.update(evaluate(status, max_age_hours=args.max_age_hours))
        except Exception as e:  # noqa: BLE001
            report.update({'ok': False, 'failures': [f'fetch:{type(e).__name__}']})
    try:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)) or '.', exist_ok=True)
        tmp = args.report + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, args.report)
    except OSError as e:
        print(f'❌ health report 寫入失敗：{e}', file=sys.stderr)
        return 1
    print(f"health summary: configured={report['configured']} ok={report.get('ok')} "
          f"failures={report.get('failures', [])}")
    return 0 if report.get('ok') is True else 1


if __name__ == '__main__':
    sys.exit(main())
