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
import official_coverage  # noqa: E402
from monitor_presentation import sanitize_source_states  # noqa: E402


def _load(url=None, path=None, timeout=20):
    if path:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request(url, headers={'Accept': 'application/json',
                                               'User-Agent': 'aircon-health/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _evaluate_v1(summary, *, now=None, max_age_hours=48, expected_metadata=None):
    """Original v1 contract: only all-sources-ok summaries can pass."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if not isinstance(summary, dict) or summary.get('kind') != 'aircon-source-health-summary':
        return {'ok': None, 'status': 'unknown',
                'reason': 'raw-status-or-unsupported-summary-without-health-check',
                'failures': ['summary:unsupported-or-missing-health-evidence']}
    failures = []
    if summary.get('schemaVersion') != 1:
        failures.append('summary:schemaVersion-unsupported')
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
    if summary.get('ok') is not True or summary.get('status') != 'healthy':
        failures.append('summary:not-healthy')
    if summary.get('failures'):
        failures.append('summary:contains-failures')
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
            if block.get('status') != 'ok':
                failures.append(f'summary:{name}-not-ok')
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
    return {'ok': not failures, 'status': 'healthy' if not failures else 'unhealthy',
            'failures': failures[:20],
            # Optional presentation-only facts; they never decide `ok`.
            'sourceStates': sanitize_source_states(summary.get('sources')),
            'checkedAt': summary.get('checkedAt')}

def _common_clock_errors(summary, now, max_age_hours):
    failures = []
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
    if not (isinstance(summary.get('sourceStatusHash'), str)
            and source_status.SHA_RE.match(summary['sourceStatusHash'])):
        failures.append('summary:sourceStatusHash-invalid')
    try:
        gen = dt.datetime.fromisoformat(str(summary.get('sourceStatusGeneratedAt')).replace('Z', '+00:00'))
        if gen.tzinfo is None or gen.utcoffset() != dt.timedelta(0):
            raise ValueError
    except (ValueError, TypeError):
        failures.append('summary:sourceStatusGeneratedAt-invalid')
    for field in ('releasePayloadHash', 'emsdSnapshotHash', 'biggoSnapshotHash'):
        value = summary.get(field)
        if not (isinstance(value, str) and source_status.SHA_RE.match(value)):
            failures.append(f'summary:{field}-invalid')
    for field in ('build', 'deploymentReceipt'):
        value = summary.get(field)
        if not isinstance(value, str) or not value.strip():
            failures.append(f'summary:{field}-missing')
    return failures, checked


def _evaluate_v2(summary, *, now=None, max_age_hours=48, expected_metadata=None):
    """v2: operations health and coverage freshness are explicit and separate."""
    now = now or dt.datetime.now(dt.timezone.utc)
    failures, checked = _common_clock_errors(summary, now, max_age_hours)
    if summary.get('ok') is not True or summary.get('status') != 'healthy':
        failures.append('summary:not-healthy')
    if summary.get('failures'):
        failures.append('summary:contains-failures')
    operations = summary.get('operations')
    if not isinstance(operations, dict):
        failures.append('summary:operations-missing')
        operations = {}
    else:
        if operations.get('ok') is not True or operations.get('status') != 'healthy':
            failures.append('summary:operations-not-healthy')
        if operations.get('failures'):
            failures.append('summary:operations-contain-failures')
        if type(operations.get('coverageExpectedPending')) is not bool:
            failures.append('summary:operations-coverage-flag-invalid')
    coverage = summary.get('coverage')
    if not isinstance(coverage, dict):
        failures.append('summary:coverage-missing')
        coverage = {}
    sources = summary.get('sources')
    if not isinstance(sources, dict) or set(sources) != {'emsd', 'biggo', 'official'}:
        failures.append('summary:sources-missing')
        sources = {}
    for name in ('emsd', 'biggo'):
        block = sources.get(name) or {}
        if block.get('status') != 'ok':
            failures.append(f'summary:{name}-not-ok')
        h = block.get('snapshotHash')
        if not (isinstance(h, str) and source_status.SHA_RE.match(h)):
            failures.append(f'summary:{name}-snapshotHash-invalid')
        elif h != summary.get(name + 'SnapshotHash'):
            failures.append(f'summary:{name}-snapshotHash-binding-mismatch')
    status = coverage.get('status')
    if status == 'pending':
        if coverage.get('pending') is not True or coverage.get('expected') is not True:
            failures.append('summary:coverage-pending-not-marked-expected')
        if operations.get('coverageExpectedPending') is not True:
            failures.append('summary:operations-coverage-flag-mismatch')
        observation = coverage.get('observation')
        if not isinstance(observation, dict):
            failures.append('summary:coverage-observation-missing')
        else:
            errors = official_coverage.validate_observation_structure(observation)
            failures.extend('summary:coverage-observation-' + e for e in errors[:10])
            body = {k: v for k, v in observation.items() if k != 'identityHash'}
            from biggo_canonical import canonical_json_bytes, sha256_id
            if observation.get('identityHash') != sha256_id(canonical_json_bytes(body)):
                failures.append('summary:coverage-observation-identity-invalid')
            if coverage.get('observationHash') != observation.get('identityHash'):
                failures.append('summary:coverage-observation-hash-mismatch')
            obs_at = None
            try:
                obs_at = dt.datetime.fromisoformat(str(observation.get('observedAt')).replace('Z', '+00:00'))
                if obs_at.tzinfo is None or obs_at.utcoffset() != dt.timedelta(0):
                    raise ValueError
            except (ValueError, TypeError):
                failures.append('summary:coverage-observedAt-invalid')
            if obs_at is not None:
                if obs_at > now + dt.timedelta(minutes=5):
                    failures.append('summary:coverage-observedAt-future')
                elif (now - obs_at).total_seconds() > max_age_hours * 3600:
                    failures.append('summary:coverage-observedAt-stale')
                if checked is not None and obs_at > checked + dt.timedelta(minutes=5):
                    failures.append('summary:coverage-observedAt-after-check')
            missing = coverage.get('missingModels')
            missing_canon = coverage.get('missingCanonicalModels')
            count = coverage.get('missingCount')
            if not isinstance(missing, list) or not isinstance(missing_canon, list)                     or type(count) is not int or count < 0:
                failures.append('summary:coverage-missing-set-invalid')
            else:
                if len(missing) != count or len(missing_canon) != count:
                    failures.append('summary:coverage-missing-count-mismatch')
                from crawl_utils import norm_model
                if {norm_model(m) for m in missing_canon} != {norm_model(m) for m in missing}:
                    failures.append('summary:coverage-missing-canonical-mismatch')
            if type(coverage.get('targetCount')) is not int or coverage.get('targetCount') <= 0:
                failures.append('summary:coverage-target-count-invalid')
            if coverage.get('stage') not in (1, 2):
                failures.append('summary:coverage-stage-invalid')
            for field in ('queueHash', 'emsdSnapshotHash'):
                value = coverage.get(field)
                if not (isinstance(value, str) and source_status.SHA_RE.match(value)):
                    failures.append(f'summary:coverage-{field}-invalid')
            prev = coverage.get('officialSnapshotHash')
            manifest = coverage.get('officialManifestHash')
            if isinstance(observation, dict):
                obs_queue = observation.get('queue') or {}
                obs_prev = observation.get('previousSnapshot') or {}
                if missing != obs_queue.get('missingModels'):
                    failures.append('summary:coverage-missing-set-binding-mismatch')
                if missing_canon != obs_queue.get('missingCanonicalModels'):
                    failures.append('summary:coverage-missing-canonical-binding-mismatch')
                if count != len(obs_queue.get('missingModels') or []):
                    failures.append('summary:coverage-missing-count-binding-mismatch')
                if coverage.get('scriptCoveragePending') != (obs_queue.get('scriptCoveragePending') or []):
                    failures.append('summary:coverage-script-pending-binding-mismatch')
                if coverage.get('observedAt') != observation.get('observedAt'):
                    failures.append('summary:coverage-observedAt-binding-mismatch')
                if coverage.get('stage') != observation.get('stage'):
                    failures.append('summary:coverage-stage-binding-mismatch')
                if coverage.get('targetCount') != len(obs_queue.get('models') or []):
                    failures.append('summary:coverage-target-count-binding-mismatch')
                if coverage.get('queueHash') != obs_queue.get('hash'):
                    failures.append('summary:coverage-queue-hash-binding-mismatch')
                if coverage.get('emsdSnapshotHash') != (observation.get('emsd') or {}).get('snapshotHash'):
                    failures.append('summary:coverage-emsd-hash-binding-mismatch')
                if prev != obs_prev.get('snapshotHash'):
                    failures.append('summary:coverage-official-snapshot-binding-mismatch')
                if manifest != ((obs_prev.get('outputManifest') or {}).get('manifestHash')):
                    failures.append('summary:coverage-official-manifest-binding-mismatch')
            if prev is None and manifest is not None:
                failures.append('summary:coverage-manifest-without-snapshot')
            if prev is not None:
                if not (isinstance(prev, str) and source_status.SHA_RE.match(prev)):
                    failures.append('summary:coverage-officialSnapshotHash-invalid')
                if manifest != prev:
                    failures.append('summary:coverage-official-manifest-snapshot-mismatch')
            if summary.get('officialSnapshotHash') != prev:
                failures.append('summary:officialSnapshotHash-binding-mismatch')
            if summary.get('officialManifestHash') != manifest:
                failures.append('summary:officialManifestHash-binding-mismatch')
            official = sources.get('official') or {}
            if official.get('status') != 'pending' or official.get('safeReason') != 'coverage-pending':
                failures.append('summary:official-pending-binding-mismatch')
            if official.get('snapshotHash') != prev:
                failures.append('summary:official-snapshot-binding-mismatch')
    elif status == 'verified':
        if coverage.get('pending') is not False or coverage.get('expected') is not False:
            failures.append('summary:coverage-verified-not-marked-complete')
        if operations.get('coverageExpectedPending') is not False:
            failures.append('summary:operations-coverage-flag-mismatch')
        official = sources.get('official') or {}
        if official.get('status') != 'ok':
            failures.append('summary:official-not-ok')
        prev = summary.get('officialSnapshotHash')
        manifest = summary.get('officialManifestHash')
        if not (isinstance(prev, str) and source_status.SHA_RE.match(prev)):
            failures.append('summary:officialSnapshotHash-invalid')
        if not (isinstance(manifest, str) and source_status.SHA_RE.match(manifest)):
            failures.append('summary:officialManifestHash-invalid')
        if prev != manifest:
            failures.append('summary:official-manifest-snapshot-hash-mismatch')
        if official.get('snapshotHash') != prev:
            failures.append('summary:official-snapshot-hash-binding-mismatch')
    else:
        failures.append('summary:coverage-not-healthy')
    if isinstance(expected_metadata, dict):
        for field, key in (('build', 'build'), ('releasePayloadHash', 'releasePayloadHash'),
                           ('emsdSnapshotHash', 'datasetHash')):
            if expected_metadata.get(key) != summary.get(field):
                failures.append(f'summary:{field}-metadata-binding-mismatch')
    return {'ok': not failures, 'status': 'healthy' if not failures else 'unhealthy',
            'failures': failures[:20],
            'sourceStates': sanitize_source_states(summary.get('sources')),
            'coveragePending': bool(status == 'pending' and not failures),
            'coverageExpected': bool(status == 'pending' and coverage.get('expected') is True),
            'coverageMissingCount': coverage.get('missingCount') if status == 'pending' else None,
            'coverageObservationHash': coverage.get('observationHash') if status == 'pending' else None,
            'checkedAt': summary.get('checkedAt')}


def _evaluate_v3(summary, *, now=None, max_age_hours=48, expected_metadata=None):
    """v3: v2 operations/coverage plus a live-derived price status receipt.

    The price receipt is mandatory.  Its self hash is not sufficient: status,
    reason, healthy flag, timing and evidence bindings must all be internally
    consistent and bound to the summary's actual BigGo snapshot.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    if summary.get('schemaVersion') != 3:
        return {'ok': False, 'status': 'unhealthy',
                'failures': ['summary:schemaVersion-unsupported'], 'sourceStates': {}}
    base = _evaluate_v2(summary, now=now, max_age_hours=max_age_hours,
                        expected_metadata=expected_metadata)
    failures = list(base.get('failures') or [])
    receipt = summary.get('price')
    if not isinstance(receipt, dict):
        failures.append('summary:price-receipt-missing')
    else:
        try:
            import price_status
            price_status.validate_receipt(receipt, now=now)
        except Exception as e:  # noqa: BLE001
            failures.append(f'summary:price-receipt-invalid:{type(e).__name__}')
        else:
            determined = None
            try:
                determined = dt.datetime.fromisoformat(
                    str(receipt.get('determinedAt')).replace('Z', '+00:00'))
                if determined.tzinfo is None or determined.utcoffset() != dt.timedelta(0):
                    raise ValueError
            except (ValueError, TypeError):
                failures.append('summary:price-determinedAt-invalid')
            if determined is not None:
                if determined > now + dt.timedelta(minutes=5):
                    failures.append('summary:price-determinedAt-future')
                elif (now - determined).total_seconds() > max_age_hours * 3600:
                    failures.append('summary:price-receipt-stale')
                checked = None
                try:
                    checked = dt.datetime.fromisoformat(
                        str(summary.get('checkedAt')).replace('Z', '+00:00'))
                except (ValueError, TypeError):
                    checked = None
                if checked is not None and determined > checked + dt.timedelta(minutes=5):
                    failures.append('summary:price-determinedAt-after-check')
            ev = receipt.get('evidence') or {}
            sources = summary.get('sources') or {}
            biggo = sources.get('biggo') or {}
            if receipt.get('status') == 'blocked' or receipt.get('healthy') is not True:
                failures.append('summary:price-not-healthy:' + str(receipt.get('safeReason')))
            if summary.get('priceHealthy') is not True:
                failures.append('summary:price-healthy-flag-mismatch')
            if (summary.get('operations') or {}).get('priceHealthy') is not True:
                failures.append('summary:operations-price-healthy-mismatch')
            if ev.get('snapshotHash') != summary.get('biggoSnapshotHash'):
                failures.append('summary:price-snapshot-binding-mismatch')
            if ev.get('snapshotHash') != biggo.get('snapshotHash'):
                failures.append('summary:price-source-snapshot-binding-mismatch')
            if receipt.get('status') == 'active' and receipt.get('healthy') is not True:
                failures.append('summary:active-price-not-healthy')
            if receipt.get('status') == 'month-completed' and receipt.get('stage') != 7:
                failures.append('summary:month-completed-stage-invalid')
    # De-duplicate while retaining deterministic order.
    failures = list(dict.fromkeys(failures))
    return {'ok': not failures, 'status': 'healthy' if not failures else 'unhealthy',
            'failures': failures[:30],
            'sourceStates': sanitize_source_states(summary.get('sources')),
            'coveragePending': bool(base.get('coveragePending')),
            'coverageExpected': bool(base.get('coverageExpected')),
            'coverageMissingCount': base.get('coverageMissingCount'),
            'coverageObservationHash': base.get('coverageObservationHash'),
            'priceStatus': receipt.get('status') if isinstance(receipt, dict) else None,
            'priceHealthy': bool(isinstance(receipt, dict)
                                 and receipt.get('healthy') is True
                                 and receipt.get('status') != 'blocked'),
            'checkedAt': summary.get('checkedAt')}


def evaluate(summary, *, now=None, max_age_hours=48, expected_metadata=None):
    """Dispatch by explicit schema version; v1/v2 keep their original meaning."""
    try:
        if isinstance(summary, dict) and summary.get('schemaVersion') == 3:
            return _evaluate_v3(summary, now=now, max_age_hours=max_age_hours,
                                expected_metadata=expected_metadata)
        if isinstance(summary, dict) and summary.get('schemaVersion') == 2:
            return _evaluate_v2(summary, now=now, max_age_hours=max_age_hours,
                                expected_metadata=expected_metadata)
        return _evaluate_v1(summary, now=now, max_age_hours=max_age_hours,
                            expected_metadata=expected_metadata)
    except (TypeError, ValueError, KeyError, AttributeError):
        return {'ok': False, 'status': 'unhealthy',
                'failures': ['summary:invalid-structure'],
                'coveragePending': False, 'sourceStates': {}}


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
