#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate a sanitised source-health summary (private endpoint or file).

GitHub never needs a private host/path in the repo: the endpoint is supplied by
a repository secret.  If unconfigured, this entrypoint records an UNKNOWN/not-wired
report and exits non-zero: absence of a health endpoint is not a PASS.  If
configured, it validates the versioned receipt, full snapshot hashes, official
status, actual deployment binding, and age.
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
import source_health_contract  # noqa: E402
from monitor_presentation import sanitize_source_states  # noqa: E402


def _load(url=None, path=None, timeout=20):
    if path:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={'Accept': 'application/json',
                                               'User-Agent': 'aircon-health/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def evaluate(summary, *, now=None, max_age_hours=48, expected_metadata=None):
    """Validate the sanitised private health summary, not a raw sourceStatus.

    Schema v2 adds a proved ``partial`` (pure official coverage pending) warning.
    A v1 summary is still accepted only with its original all-healthy semantics
    and is never upgraded to partial.  Missing/unknown evidence stays fail-closed.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(summary, dict) or summary.get('kind') != 'aircon-source-health-summary':
        return {'ok': None, 'status': 'unknown',
                'reason': 'raw-status-or-unsupported-summary-without-health-check',
                'failures': ['summary:unsupported-or-missing-health-evidence']}
    failures = []
    partial = False
    warnings = []
    pending = None
    version = summary.get('schemaVersion')
    if version not in (1, source_health_contract.SUMMARY_SCHEMA_VERSION):
        failures.append('summary:schemaVersion-unsupported')
    if version == source_health_contract.SUMMARY_SCHEMA_VERSION:
        if summary.get('status') == 'partial':
            try:
                pending = source_health_contract.validate_pending_summary(summary)
                partial = True
                warnings = list(summary.get('warnings') or [])
            except (source_health_contract.ContractError, ValueError) as exc:
                failures.append('summary:partial-evidence-invalid')
                warnings.append(f'summary:partial-proof:{type(exc).__name__}')
        elif summary.get('status') not in ('healthy', 'unhealthy', 'unknown'):
            failures.append('summary:status-unsupported')
    checked = None
    try:
        checked = dt.datetime.fromisoformat(str(summary.get('checkedAt')).replace('Z', '+00:00'))
    except ValueError:
        pass
    if checked is None or checked.tzinfo is None or checked.utcoffset() != dt.timedelta(0):
        failures.append('summary:checkedAt-invalid')
    elif checked > now + dt.timedelta(minutes=5):
        failures.append('summary:checkedAt-future')
    elif (now - checked).total_seconds() > max_age_hours * 3600:
        failures.append('summary:checkedAt-stale')
    if summary.get('ok') is not True or summary.get('status') not in ('healthy', 'partial'):
        failures.append('summary:not-healthy')
    if summary.get('failures'):
        failures.append('summary:contains-failures')
    if partial and summary.get('ok') is not True:
        failures.append('summary:partial-not-ok')
    if not partial and version == source_health_contract.SUMMARY_SCHEMA_VERSION \
            and summary.get('status') == 'healthy' and summary.get('warnings'):
        failures.append('summary:healthy-with-warnings')
    if not (isinstance(summary.get('sourceStatusHash'), str)
            and source_status.SHA_RE.match(summary['sourceStatusHash'])):
        failures.append('summary:sourceStatusHash-invalid')
    try:
        gen = dt.datetime.fromisoformat(str(summary.get('sourceStatusGeneratedAt')).replace('Z', '+00:00'))
        if gen.tzinfo is None or gen.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (ValueError, TypeError):
        failures.append('summary:sourceStatusGeneratedAt-invalid')
    for field in ('releasePayloadHash', 'emsdSnapshotHash', 'biggoSnapshotHash',
                  'officialSnapshotHash', 'officialManifestHash'):
        value = summary.get(field)
        if not (isinstance(value, str) and source_status.SHA_RE.match(value)):
            failures.append(f'summary:{field}-invalid')
    for field in ('build', 'deploymentReceipt'):
        value = summary.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(f'summary:{field}-missing')
    if summary.get('officialManifestHash') != summary.get('officialSnapshotHash'):
        failures.append('summary:official-manifest-snapshot-hash-mismatch')
    sources = summary.get('sources')
    if not isinstance(sources, dict) or set(sources) != {'emsd', 'biggo', 'official'}:
        failures.append('summary:sources-missing')
    else:
        for name in ('emsd', 'biggo', 'official'):
            block = sources.get(name) or {}
            expected_status = 'pending' if (partial and name == 'official') else 'ok'
            if block.get('status') != expected_status:
                failures.append(f'summary:{name}-not-ok')
            if partial and name == 'official' \
                    and block.get('safeReason') != 'coverage-pending':
                failures.append('summary:official-pending-reason')
            h = block.get('snapshotHash')
            if not (isinstance(h, str) and source_status.SHA_RE.match(h)):
                failures.append(f'summary:{name}-snapshotHash-invalid')
            elif h != summary.get(name + 'SnapshotHash'):
                failures.append(f'summary:{name}-snapshotHash-binding-mismatch')
    if isinstance(expected_metadata, dict):
        for field, key in (('build', 'build'), ('releasePayloadHash', 'releasePayloadHash'),
                           ('emsdSnapshotHash', 'datasetHash')):
            if expected_metadata.get(key) != summary.get(field):
                failures.append(f'summary:{field}-metadata-binding-mismatch')
    ok = not failures
    if ok and partial:
        status = 'partial'
    elif ok:
        status = 'healthy'
    else:
        status = 'unhealthy'
    return {'ok': ok, 'status': status, 'partial': bool(ok and partial),
            'warnings': warnings if ok and partial else [],
            'pending': pending if ok and partial else None,
            'failures': failures[:20],
            # Optional presentation-only facts; they never decide `ok`.
            'sourceStates': sanitize_source_states(summary.get('sources')),
            'checkedAt': summary.get('checkedAt')}

def main(argv=None):
    ap = argparse.ArgumentParser(description='validate sanitised health summary')
    ap.add_argument('--url', default=os.environ.get('AIRCON_HEALTH_SUMMARY_URL', ''))
    ap.add_argument('--file', default=None)
    ap.add_argument('--report', required=True)
    ap.add_argument('--max-age-hours', type=float, default=48)
    ap.add_argument('--metadata', default=None,
                    help='optional expected metadata for build/payload/EMSD binding')
    args = ap.parse_args(argv)
    report = {'schemaVersion': 1, 'configured': bool(args.url or args.file),
              'status': 'unknown'}
    if not args.url and not args.file:
        report.update({'ok': None, 'skipped': True,
                       'reason': 'health-summary-not-wired',
                       'requiredAction': 'configure AIRCON_HEALTH_SUMMARY_URL secret'})
    else:
        try:
            summary = _load(args.url, args.file)
            expected = None
            if args.metadata:
                with open(args.metadata, encoding='utf-8') as f:
                    expected = json.load(f)
            report.update(evaluate(summary, max_age_hours=args.max_age_hours,
                                   expected_metadata=expected))
        except Exception as e:  # noqa: BLE001
            report.update({'ok': False, 'status': 'unhealthy',
                           'failures': [f'fetch:{type(e).__name__}']})
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
