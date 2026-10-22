#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Private read-only source-health monitor (no external notifications).

Reads a versioned `source_status.json`, validates its sanitised schema, and
checks that its hashes still bind to the live local CSV / price snapshot.
Missing or corrupt receipts are unhealthy.  Dedup state is durable and
recoverable; a delayed schedule is recorded but never triggers a rerun.

Notification adapters are intentionally not called in this implementation.
The report/state are safe for an existing private channel or a sanitised
GitHub health summary to consume later.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
for _p in (BASE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import source_status  # noqa: E402
import official_coverage  # noqa: E402
from biggo_canonical import sha256_json  # noqa: E402

OFFICIAL_OUTPUTS = {
    1: ('official_specs.json', 'shew_official.json', 'rasonic_official.json'),
    2: ('carrier_official.json', 'general_official.json', 'specs.json'),
}


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _load_optional(path):
    """Missing optional receipt is absent; a present corrupt receipt still raises."""
    if not path or not os.path.exists(path):
        return None
    return _load(path)


def _raw_token(paths):
    h = hashlib.sha256()
    for path in paths:
        h.update(os.fspath(path).encode('utf-8'))
        try:
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(1 << 20), b''):
                    h.update(chunk)
        except OSError:
            h.update(b'<missing>')
        h.update(b'\0')
    return 'sha256:' + h.hexdigest()


def _price_inputs(repo, *, demand_path, biggo_path, sync_path, snapshot_path,
                  anchor_path, meta_path):
    """Read a fresh, validated price input set plus a raw prestate token."""
    import price_demand as _demand_mod
    paths = [meta_path, demand_path, biggo_path, sync_path, snapshot_path,
             os.path.join(repo, 'model_status.json'),
             os.path.join(repo, 'model_blacklist.json'), anchor_path]
    token = _raw_token(paths)
    demand = _demand_mod.load_required(demand_path)
    biggo = _load_optional(biggo_path) or {}
    sync = _load_optional(sync_path) or {}
    snapshot_hash = None
    if os.path.isfile(snapshot_path):
        try:
            snapshot_hash = sha256_json(_load(snapshot_path))
        except (OSError, ValueError):
            snapshot_hash = None
    anchor_doc = None
    if os.path.exists(anchor_path):
        cached = _load(anchor_path)
        if isinstance(cached, dict) and cached.get('kind') == 'aircon-price-anchor-cache':
            anchor_doc = cached.get('anchor')
        else:
            anchor_doc = cached
    tracking = _load_optional(os.path.join(repo, 'model_status.json'))
    blacklist = _load_optional(os.path.join(repo, 'model_blacklist.json'))
    models = (blacklist or {}).get('models') if isinstance(blacklist, dict) else None
    post_state = {
        'trackingHash': sha256_json(tracking) if isinstance(tracking, dict) else None,
        'blacklistHash': sha256_json(models) if isinstance(models, dict) else None,
    }
    ce = os.environ.get('AIRCON_PRICE_CYCLE_ENABLED') == '1'
    sc = os.environ.get('AIRCON_PRICE_SHARED_CYCLE') == '1'
    if ce != sc:
        raise ValueError('price-mode-inconsistent')
    return {
        'meta': _load(meta_path), 'demand': demand, 'biggo': biggo,
        'completed_sync': sync, 'snapshot_hash': snapshot_hash,
        'anchor': anchor_doc, 'shared': ce and sc,
        'post_state': post_state,
    }, token


def _atomic_json(path, data):
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _parse_utc(value):
    if not isinstance(value, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(_dt.timezone.utc)
    except ValueError:
        return None


def _official_manifest_errors(official_doc, official, repo, *, retained_stage=False):
    """Verify the official status manifest against actual output bytes/JSON."""
    if not isinstance(official_doc, dict):
        return ['official:status-doc-missing']
    errors = []
    decision = official_doc.get('decision')
    claimed_ok = official.get('status') == 'ok'
    if claimed_ok and decision != 'advanced':
        errors.append('official:ok-without-advanced-status')
    manifest = official_doc.get('outputManifest')
    claimed_hash = official.get('snapshotHash')
    if not isinstance(manifest, dict):
        if claimed_hash or claimed_ok:
            errors.append('official:manifest-missing')
        return errors
    stage = manifest.get('stage')
    if manifest.get('schemaVersion') != 1 or stage not in OFFICIAL_OUTPUTS:
        errors.append('official:manifest-schema-or-stage-invalid')
        return errors
    if not retained_stage and official_doc.get('stage') != stage:
        errors.append('official:status-stage-mismatch')
    files = manifest.get('files')
    allowed = set(OFFICIAL_OUTPUTS[stage]) | {'mecan_official.json', 'gilman_official.json'}
    selected = official_doc.get('selectedOutputs')
    if selected is not None:
        targets = official_doc.get('targetModels')
        if not isinstance(targets, list) or not targets \
                or len(targets) != len(set(targets)) \
                or any(not isinstance(x, str) or not x for x in targets):
            errors.append('official:target-profile-invalid')
            return errors
        try:
            from run_official_batch import route_for_model
            from publish_official_status import SOURCE_OUTPUTS
        except Exception:
            errors.append('official:target-profile-unavailable')
            return errors
        recomputed = {m: route_for_model(m) for m in targets}
        expected = {SOURCE_OUTPUTS[r['source']] for r in recomputed.values()
                    if r.get('supported') is True and r.get('source') in SOURCE_OUTPUTS}
        if set(selected) != expected:
            errors.append('official:selected-outputs-target-mismatch')
            return errors
        reported = official_doc.get('sourceRouting')
        if reported not in (None, {}) and reported != recomputed:
            errors.append('official:source-routing-profile-mismatch')
            return errors
        if not isinstance(selected, list) or not selected \
                or len(selected) != len(set(selected)) \
                or any(x not in allowed for x in selected):
            errors.append('official:selected-outputs-invalid')
            return errors
        expected = set(selected)
    else:
        expected = set(OFFICIAL_OUTPUTS[stage])
    if not isinstance(files, dict) or set(files) != expected:
        errors.append('official:manifest-output-set-mismatch')
        return errors
    for name, meta in files.items():
        if not isinstance(meta, dict):
            errors.append(f'official:{name}:metadata-invalid'); continue
        h = meta.get('sha256')
        size = meta.get('size')
        if not (isinstance(h, str) and source_status.SHA_RE.match(h)):
            errors.append(f'official:{name}:short-or-missing-hash'); continue
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            errors.append(f'official:{name}:size-invalid'); continue
        path = os.path.join(repo, name)
        if os.path.islink(path) or not os.path.isfile(path):
            errors.append(f'official:{name}:actual-missing'); continue
        actual = open(path, 'rb').read()
        if len(actual) != size:
            errors.append(f'official:{name}:actual-size-mismatch')
        if 'sha256:' + hashlib.sha256(actual).hexdigest() != h:
            errors.append(f'official:{name}:actual-hash-mismatch')
        try:
            data = json.loads(actual.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            errors.append(f'official:{name}:actual-json-invalid')
            continue
        if not isinstance(data, (dict, list)) or not data:
            errors.append(f'official:{name}:actual-json-empty')
            continue
        entries = list(data.values()) if isinstance(data, dict) else data
        has_evidence = False
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for k, v in entry.items():
                if k.endswith('_error') or k in ('url', 'model'):
                    continue
                if (isinstance(v, str) and v.strip()) or (isinstance(v, bool) and v) \
                        or (isinstance(v, (list, dict)) and v) \
                        or (isinstance(v, (int, float)) and not isinstance(v, bool) and v):
                    has_evidence = True
                    break
            if has_evidence:
                break
        if not has_evidence:
            errors.append(f'official:{name}:actual-evidence-missing')
    body = {'schemaVersion': 1, 'stage': stage, 'files': files}
    # Canonical JSON from biggo_canonical is the producer contract.
    from biggo_canonical import canonical_json_bytes, sha256_id
    want = sha256_id(canonical_json_bytes(body))
    if manifest.get('manifestHash') != want:
        errors.append('official:manifest-hash-mismatch')
    if claimed_hash and manifest.get('manifestHash') != claimed_hash:
        errors.append('official:snapshot-hash-manifest-mismatch')
    if claimed_ok:
        if not source_status._valid_ts(official_doc.get('lastSuccessAt')):
            errors.append('official:success-time-missing')
        elif official.get('lastAcquiredAt') != official_doc.get('lastSuccessAt'):
            errors.append('official:success-time-binding-mismatch')
    return errors


def evaluate(*, status, repo, now=None, max_age_hours=36, schedule_hkt='00:30',
             require_deployed=False, deployment_receipt=None, metadata=None, web_root=None,
             official_status_doc=None, price_status_doc=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    failures = []
    if not isinstance(status, dict):
        return {'ok': False, 'fingerprint': 'receipt:invalid',
                'failures': ['receipt:not-object'], 'delayMinutes': None}
    errors = source_status.validate_status(status)
    if errors:
        return {'ok': False, 'fingerprint': 'receipt:invalid',
                'failures': ['receipt:invalid'] + errors[:5], 'delayMinutes': None}
    csv_path = os.path.join(repo, 'emsd_空調能源標籤.csv')
    price_path = os.path.join(repo, 'biggo_prices.json')
    emsd = status.get('emsd') or {}
    biggo = status.get('biggo') or {}
    if biggo.get('snapshotHash'):
        if not os.path.isfile(price_path):
            failures.append('binding:biggo-snapshot-missing')
        else:
            try:
                live = sha256_json(_load(price_path))
            except (OSError, ValueError):
                live = None
                failures.append('binding:biggo-snapshot-corrupt')
            if live is not None and live != biggo['snapshotHash']:
                failures.append('binding:biggo-snapshot-hash-mismatch')
    if emsd.get('snapshotHash'):
        if not os.path.isfile(csv_path):
            failures.append('binding:emsd-csv-missing')
        elif _sha256_file(csv_path) != emsd['snapshotHash']:
            failures.append('binding:emsd-csv-hash-mismatch')
    official = status.get('official') or {}
    price_errors = []
    price_receipt = None
    if price_status_doc is not None:
        try:
            import price_status as _price_status
            price_receipt = _price_status.validate_receipt(price_status_doc, now=now)
        except Exception as e:  # noqa: BLE001 - malformed evidence never green
            price_errors.append('price:receipt-invalid')
            price_receipt = None
        else:
            ev = price_receipt.get('evidence') or {}
            live_price = None
            if os.path.isfile(price_path):
                try:
                    live_price = sha256_json(_load(price_path))
                except (OSError, ValueError):
                    live_price = None
            actual = price_receipt.get('status')
            if actual == 'blocked':
                price_errors.append('price:' + str(price_receipt.get('safeReason')))
            elif price_receipt.get('healthy') is not True:
                price_errors.append('price:' + str(price_receipt.get('safeReason')))
            if not (isinstance(ev.get('snapshotHash'), str)
                    and source_status.SHA_RE.match(ev['snapshotHash'])):
                price_errors.append('price:snapshot-evidence-missing')
            if live_price is not None and ev.get('snapshotHash') != live_price:
                price_errors.append('price:snapshot-binding-mismatch')
            if biggo.get('snapshotHash') and ev.get('snapshotHash') != biggo.get('snapshotHash'):
                price_errors.append('price:source-status-snapshot-mismatch')
            determined = _parse_utc(price_receipt.get('determinedAt'))
            if determined is None:
                price_errors.append('price:determined-time-invalid')
            elif (now - determined).total_seconds() > max_age_hours * 3600:
                price_errors.append('price:receipt-stale')
    coverage = None
    if isinstance(official_status_doc, dict) and \
            official_status_doc.get('decision') == 'queue-kept-pending-coverage':
        coverage = official_coverage.expected_pending(
            official_status_doc, repo=repo, metadata=metadata, now=now,
            enforce_age=True, max_age_hours=max_age_hours, require_emsd=True)
        if coverage.get('trusted'):
            # The source-status projection must be bound to the same
            # observation; an outer status edit cannot turn it green.
            if official.get('status') != 'pending' or official.get('safeReason') != 'coverage-pending':
                failures.append('official:coverage-status-binding-mismatch')
            if official.get('lastAttemptAt') != coverage.get('observedAt'):
                failures.append('official:coverage-observation-time-mismatch')
            if official.get('snapshotHash') != coverage.get('officialSnapshotHash'):
                failures.append('official:coverage-snapshot-binding-mismatch')
            if official.get('snapshotId') != ((coverage.get('previousSnapshot') or {}).get('snapshotId')):
                failures.append('official:coverage-snapshot-id-binding-mismatch')
        else:
            failures.append('official:coverage-pending-untrusted')
            failures.extend('official:coverage-observation-' + e
                            for e in (coverage.get('errors') or [])[:5])
    for name, block in (('emsd', emsd), ('biggo', biggo), ('official', official)):
        st = block.get('status')
        if name == 'official' and coverage is not None and coverage.get('trusted'):
            pass
        elif st != 'ok':
            failures.append(f'{name}:{st}:{block.get("safeReason")}')
        if st == 'ok':
            h = block.get('snapshotHash')
            if not (isinstance(h, str) and source_status.SHA_RE.match(h)):
                failures.append(f'{name}:ok-without-full-snapshotHash')
        for field in ('lastAttemptAt', 'lastAcquiredAt', 'lastAppliedAt'):
            when = _parse_utc(block.get(field))
            if when is None:
                continue
            if when > now + _dt.timedelta(minutes=5):
                failures.append(f'{name}:{field}-future')
            if field in ('lastAcquiredAt', 'lastAppliedAt'):
                # A verified price receipt replaces the generic price-age
                # criterion.  EMSD/official/workflow heartbeat freshness and
                # all real provider failures stay independent and enforced.
                if name == 'biggo' and price_receipt is not None:
                    continue
                if (now - when).total_seconds() > max_age_hours * 3600:
                    failures.append(f'{name}:age>{max_age_hours}h')
    if coverage is not None and coverage.get('trusted'):
        observed = _parse_utc(coverage.get('observedAt'))
        if observed is None:
            failures.append('official:observation-time-invalid')
        elif (now - observed).total_seconds() > max_age_hours * 3600:
            failures.append(f'official:observation-age>{max_age_hours}h')
    generated = _parse_utc(status.get('generatedAt'))
    if generated is None:
        failures.append('status:generatedAt-missing-or-invalid')
    elif generated > now + _dt.timedelta(minutes=5):
        failures.append('status:generatedAt-future')
    elif (now - generated).total_seconds() > max_age_hours * 3600:
        failures.append('status:generatedAt-stale')
    if official.get('status') == 'ok' or official.get('snapshotHash'):
        failures.extend(_official_manifest_errors(official_status_doc, official, repo,
                            retained_stage=bool(coverage and coverage.get('trusted'))))
    publish = status.get('publish') or {}
    deployed = _parse_utc(publish.get('deployedAt'))
    if require_deployed:
        if deployed is None:
            failures.append('publish:no-deployment-receipt')
        if not isinstance(deployment_receipt, dict):
            failures.append('publish:deployment-receipt-missing')
        else:
            if not source_status.deployment_matches(metadata, deployment_receipt):
                failures.append('publish:deployment-receipt-not-bound')
            if publish.get('deploymentReceipt') != deployment_receipt.get('receiptId'):
                failures.append('publish:deployment-receipt-id-mismatch')
            live = deployment_receipt.get('liveHashes') or {}
            if not web_root:
                failures.append('publish:web-root-missing-for-binding')
            else:
                for name, want in live.items():
                    path = os.path.join(web_root, name)
                    if not os.path.isfile(path):
                        failures.append(f'publish:live-file-missing:{name}')
                    elif _sha256_file(path) != want:
                        failures.append(f'publish:live-hash-mismatch:{name}')
    delay_minutes = None
    if deployed is not None:
        # Informational only: scheduled 00:30 HKT is not a deadline and never
        # causes an automatic rerun.  Record actual delay for operators.
        hkt = deployed.astimezone(_dt.timezone(_dt.timedelta(hours=8)))
        scheduled = hkt.replace(hour=int(schedule_hkt.split(':')[0]),
                                minute=int(schedule_hkt.split(':')[1]),
                                second=0, microsecond=0)
        delay_minutes = round((hkt - scheduled).total_seconds() / 60, 1)
    failures.extend(price_errors)
    fp = 'ok' if not failures else 'unhealthy:' + ','.join(sorted(set(f.split(':')[0] for f in failures)))
    return {'ok': not failures, 'fingerprint': fp,
            'failures': failures[:20], 'delayMinutes': delay_minutes,
            'coverage': coverage,
            'coveragePending': bool(coverage and coverage.get('trusted')),
            'coverageMissingCount': (coverage or {}).get('missingCount')
            if isinstance(coverage, dict) else None,
            'schedule': {'planned': schedule_hkt + ' HKT',
                         'actualDeployedAt': (status.get('publish') or {}).get('deployedAt'),
                         'delayIsInformationalOnly': True}}


def plan_dedup(state, result, now):
    """Durable dedup/recovery state; same unhealthy fingerprint is a noop."""
    now_s = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    state = state if isinstance(state, dict) else {}
    active = bool(state.get('active'))
    fp = result.get('fingerprint')
    if result.get('ok'):
        action = 'recovered' if active else 'noop'
        return {'action': action, 'active': False, 'fingerprint': fp,
                'firstSeenAt': None, 'lastSeenAt': now_s}, action
    if active and state.get('fingerprint') == fp:
        return {'action': 'noop', 'active': True, 'fingerprint': fp,
                'firstSeenAt': state.get('firstSeenAt') or now_s,
                'lastSeenAt': now_s}, 'noop'
    action = 'update' if active else 'alert'
    return {'action': action, 'active': True, 'fingerprint': fp,
            'firstSeenAt': now_s, 'lastSeenAt': now_s}, action


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='read-only source health monitor')
    ap.add_argument('--status', default=os.path.join(BASE, 'source_status.json'))
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--state', default=os.path.join(BASE, '.source-health-state.json'))
    ap.add_argument('--report', default=os.path.join(BASE, 'source_health_report.json'))
    ap.add_argument('--summary', default=os.path.join(BASE, 'source_health_summary.json'))
    ap.add_argument('--max-age-hours', type=float, default=36)
    ap.add_argument('--schedule-hkt', default='00:30')
    ap.add_argument('--require-deployed', action='store_true',
                    help='post-deploy mode: deployedAt/deploymentReceipt must be bound to this build/payload/all4 hashes')
    ap.add_argument('--deployment-receipt', default=None)
    ap.add_argument('--metadata', default=None)
    ap.add_argument('--web-root', default=None)
    ap.add_argument('--official-status-file', default=None)
    ap.add_argument('--price-demand', default=None)
    ap.add_argument('--price-status', default=None)
    ap.add_argument('--price-status-out', default=None)
    ap.add_argument('--biggo-status', default=None)
    ap.add_argument('--completed-sync', default=None)
    ap.add_argument('--anchor-file', default=None)
    ap.add_argument('--now', default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.now:
        try:
            now = _dt.datetime.fromisoformat(args.now.replace('Z', '+00:00'))
            if now.tzinfo is None:
                raise ValueError
            now = now.astimezone(_dt.timezone.utc)
        except ValueError:
            print('❌ invalid --now', file=sys.stderr)
            return 2
    else:
        now = _dt.datetime.now(_dt.timezone.utc)
    try:
        status = _load(args.status)
    except (OSError, ValueError) as e:
        status = None
        load_error = f'{type(e).__name__}'
    else:
        load_error = None
    official_doc = None
    price_status_doc = None
    price_mode = False
    price_demand_path = args.price_demand or os.path.join(args.repo, 'price_demand.json')
    price_status_path = args.price_status or os.path.join(args.repo, 'price_status.json')
    price_mode = (bool(os.environ.get('AIRCON_PRICE_QUERY_MODE'))
                  or os.environ.get('AIRCON_PRICE_CYCLE_ENABLED') == '1'
                  or os.environ.get('AIRCON_PRICE_SHARED_CYCLE') == '1'
                  or os.path.exists(price_demand_path)
                  or os.path.exists(price_status_path)
                  or bool(args.price_status_out))
    price_race = False
    if status is not None and price_mode:
        try:
            import price_demand as _demand_mod
            import price_status as _price_status_mod
            meta_path = os.path.join(args.repo, 'prices_meta.json')
            demand_path = price_demand_path
            biggo_path = args.biggo_status or os.path.join(
                args.repo, 'biggo_stage_status.json')
            sync_path = args.completed_sync or os.path.join(
                args.repo, 'biggo_completed_sync.json')
            snapshot_path = os.path.join(args.repo, 'biggo_prices.json')
            anchor_path = args.anchor_file or os.path.join(
                args.repo, 'price_anchor.json')
            inputs1, token1 = _price_inputs(
                args.repo, demand_path=demand_path, biggo_path=biggo_path,
                sync_path=sync_path, snapshot_path=snapshot_path,
                anchor_path=anchor_path, meta_path=meta_path)
            candidate = _price_status_mod.build_receipt(now=now, **inputs1)
            _price_status_mod.validate_receipt(candidate, now=now)
            # Genuine re-read: derive again from a second disk snapshot and
            # compare both parsed facts and raw bytes before publishing.
            inputs2, token2 = _price_inputs(
                args.repo, demand_path=demand_path, biggo_path=biggo_path,
                sync_path=sync_path, snapshot_path=snapshot_path,
                anchor_path=anchor_path, meta_path=meta_path)
            rebuilt = _price_status_mod.build_receipt(now=now, **inputs2)
            if token1 != token2 or inputs1 != inputs2 or rebuilt != candidate:
                price_race = True
                price_status_doc = {'schemaVersion': 1,
                                    'kind': 'aircon-price-status',
                                    'status': 'blocked',
                                    'safeReason': 'invalid-status-input',
                                    'healthy': False,
                                    'errorClass': 'inputs-changed'}
            else:
                _price_status_mod.verify_receipt(candidate, inputs=inputs2, now=now)
                price_status_doc = candidate
                _atomic_json(args.price_status_out or price_status_path, candidate)
        except Exception as e:  # noqa: BLE001 - any evidence failure is red
            price_status_doc = {'schemaVersion': 1,
                                'kind': 'aircon-price-status',
                                'status': 'blocked',
                                'safeReason': 'invalid-status-input',
                                'healthy': False,
                                'errorClass': type(e).__name__}
    if status is None:
        result = {'ok': False, 'fingerprint': 'receipt:missing-or-corrupt',
                  'failures': [f'receipt:{load_error}'], 'delayMinutes': None}
    else:
        try:
            receipt = _load(args.deployment_receipt) if args.deployment_receipt else None
            metadata = _load(args.metadata) if args.metadata else None
            official_doc = _load(args.official_status_file) if args.official_status_file else None
        except (OSError, ValueError) as e:
            result = {'ok': False, 'fingerprint': 'receipt:invalid',
                      'failures': [f'receipt-aux:{type(e).__name__}'],
                      'delayMinutes': None}
        else:
            result = evaluate(status=status, repo=args.repo, now=now,
                          max_age_hours=args.max_age_hours,
                          schedule_hkt=args.schedule_hkt,
                          require_deployed=args.require_deployed,
                              deployment_receipt=receipt, metadata=metadata,
                              web_root=args.web_root, official_status_doc=official_doc,
                              price_status_doc=price_status_doc)
            if price_race:
                result['ok'] = False
                result['failures'] = list(result.get('failures') or []) + ['price:inputs-busy']
                result['fingerprint'] = 'unhealthy:price'
    try:
        state = _load(args.state)
    except (OSError, ValueError):
        state = {}
    new_state, action = plan_dedup(state, result, now)
    report = {'schemaVersion': 1, 'checkedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
              'action': action, 'check': result, 'state': new_state,
              'notifications': {'sent': False, 'adapter': 'none-during-implementation'}}
    # Sanitised summary for the optional GitHub health endpoint.  It is
    # rewritten on every outcome (including corruption), never left as old green.
    coverage = result.get('coverage') if isinstance(result, dict) else None
    coverage_trusted = bool(isinstance(coverage, dict) and coverage.get('trusted'))
    official_block = (status.get('official') or {}) if isinstance(status, dict) else {}
    if coverage_trusted:
        coverage_status = 'pending'
    elif official_block.get('status') == 'ok':
        coverage_status = 'verified'
    else:
        coverage_status = 'unavailable'
    if coverage_trusted:
        coverage_doc = {
            'status': 'pending',
            'pending': True,
            'expected': True,
            'missingCount': coverage.get('missingCount'),
            'missingModels': coverage.get('missingModels') or [],
            'missingCanonicalModels': coverage.get('missingCanonicalModels') or [],
            'scriptCoveragePending': coverage.get('scriptCoveragePending') or [],
            'observation': coverage.get('observation'),
            'observationHash': coverage.get('observationHash'),
            'observedAt': coverage.get('observedAt'),
            'stage': coverage.get('stage'),
            'targetCount': coverage.get('targetCount'),
            'queueHash': coverage.get('queueHash'),
            'emsdSnapshotHash': coverage.get('emsdSnapshotHash'),
            'officialSnapshotHash': coverage.get('officialSnapshotHash'),
            'officialManifestHash': coverage.get('officialManifestHash'),
        }
        official_snapshot = coverage.get('officialSnapshotHash')
        official_manifest = coverage.get('officialManifestHash')
    else:
        coverage_doc = {
            'status': coverage_status,
            'pending': False,
            'expected': False,
            'missingCount': None,
            'missingModels': [],
            'missingCanonicalModels': [],
            'scriptCoveragePending': [],
            'observation': None,
            'observationHash': None,
            'observedAt': None,
            'stage': official_block.get('stage'),
            'targetCount': None,
            'queueHash': None,
            'emsdSnapshotHash': None,
            'officialSnapshotHash': official_block.get('snapshotHash'),
            'officialManifestHash': ((official_doc or {}).get('outputManifest') or {}).get('manifestHash') if isinstance(official_doc, dict) else None,
        }
        official_snapshot = coverage_doc['officialSnapshotHash']
        official_manifest = coverage_doc['officialManifestHash']
    operations_ok = result.get('ok') is True
    summary = {
        'schemaVersion': 3 if price_status_doc is not None else 2,
        'kind': 'aircon-source-health-summary',
        'checkedAt': report['checkedAt'],
        'ok': operations_ok,
        'status': 'healthy' if operations_ok else 'unhealthy',
        'operations': {
            'ok': operations_ok,
            'status': 'healthy' if operations_ok else 'unhealthy',
            'failures': list(result.get('failures') or [])[:20],
            'coverageExpectedPending': coverage_trusted,
        },
        'coverage': coverage_doc,
        'failures': list(result.get('failures') or [])[:20],
        'reason': 'coverage-pending' if (operations_ok and coverage_trusted)
                  else result.get('reason'),
        'sourceStatusHash': sha256_json(status) if isinstance(status, dict) else None,
        'sourceStatusGeneratedAt': status.get('generatedAt') if isinstance(status, dict) else None,
        'build': (status.get('publish') or {}).get('build') if isinstance(status, dict) else None,
        'releasePayloadHash': (status.get('publish') or {}).get('releasePayloadHash') if isinstance(status, dict) else None,
        'deploymentReceipt': (status.get('publish') or {}).get('deploymentReceipt') if isinstance(status, dict) else None,
        'emsdSnapshotHash': (status.get('emsd') or {}).get('snapshotHash') if isinstance(status, dict) else None,
        'biggoSnapshotHash': (status.get('biggo') or {}).get('snapshotHash') if isinstance(status, dict) else None,
        'officialSnapshotHash': official_snapshot,
        'officialManifestHash': official_manifest,
        'sources': {name: {'status': (status.get(name) or {}).get('status'),
                           'snapshotHash': (status.get(name) or {}).get('snapshotHash'),
                           'safeReason': (status.get(name) or {}).get('safeReason')}
                    for name in ('emsd', 'biggo', 'official')} if isinstance(status, dict) else {},
        'schedule': {'planned': args.schedule_hkt + ' HKT',
                     'delayIsInformationalOnly': True},
    }
    if price_status_doc is not None:
        # v3 requires the actual receipt, not merely a status word.  Missing or
        # malformed receipt evidence is unhealthy, never default-healthy.
        summary['price'] = price_status_doc
        summary['priceHealthy'] = price_status_doc.get('healthy') is True \
            and price_status_doc.get('status') != 'blocked'
        summary['operations']['priceHealthy'] = summary['priceHealthy']
        if not summary['priceHealthy']:
            summary['operations']['ok'] = False
            summary['operations']['status'] = 'unhealthy'
            summary['ok'] = False
            summary['status'] = 'unhealthy'
    if price_race:
        # Do not overwrite a previously valid summary with a half-mixed read.
        try:
            _atomic_json(args.state, new_state)
            _atomic_json(args.report, report)
        except OSError as e:
            print(f'❌ source health report/state 寫入失敗：{e}', file=sys.stderr)
            return 1
        print('source health: busy (price inputs changed during derive); old summary preserved')
        return 1
    try:
        _atomic_json(args.state, new_state)
        _atomic_json(args.report, report)
        _atomic_json(args.summary, summary)
    except OSError as e:
        print(f'❌ source health report/state/summary 寫入失敗：{e}', file=sys.stderr)
        return 1
    print(f"source health: ok={result.get('ok')} action={action} "
          f"fingerprint={result.get('fingerprint')} delayMinutes={result.get('delayMinutes')}")
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
