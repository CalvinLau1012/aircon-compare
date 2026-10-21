#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure, versioned decision support for official (brand/agent) coverage.

The EMSD registration/supply record, retail availability and full official
specification coverage are different facts.  This module only classifies the
*enrichment coverage* part of an official batch.  It never invents a missing
specification, availability, or a complete official snapshot.

A trusted ``queue-kept-pending-coverage`` receipt carries an observation that
binds the actual queue, EMSD input bytes, routes, script receipts, staged and
retained output bytes/evidence.  A legacy pending receipt without that
observation is still displayable as pending, but it is *not* treated as a
healthy/expected pending operation.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time

from biggo_canonical import canonical_json_bytes, sha256_id
from crawl_utils import COVERAGE_PENDING_REASONS, norm_model

SCHEMA_VERSION = 1
KIND = 'aircon-official-coverage-observation'
DECISION = 'queue-kept-pending-coverage'
SAFE_REASONS = frozenset(COVERAGE_PENDING_REASONS) | {
    'stage-source-did-not-cover-target',
    'target-not-in-configured-comfee-list',
    'source-route-not-configured',
}
SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')


class ObservationError(ValueError):
    pass


def _norm_sha(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if SHA_RE.match(value):
        return value
    if len(value) == 64 and all(c in '0123456789abcdef' for c in value):
        return 'sha256:' + value
    return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _atomic_json(path, data):
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + f'.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _valid_utc(value, *, now=None, future_minutes=5):
    if not isinstance(value, str) or not UTC_RE.match(value):
        return False
    try:
        dt = _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    if dt.tzinfo is None or dt.utcoffset() != _dt.timedelta(0):
        return False
    if now is not None and dt > now + _dt.timedelta(minutes=future_minutes):
        return False
    return True


def _utc_now(now=None):
    if now is None:
        return _dt.datetime.now(_dt.timezone.utc)
    if isinstance(now, _dt.datetime):
        if now.tzinfo is None:
            raise ValueError('now 必須帶時區')
        return now.astimezone(_dt.timezone.utc)
    raise ValueError('now 必須係 datetime')


def _norm_list(values, label, errors, *, allow_empty=True):
    if not isinstance(values, list):
        errors.append(f'{label} 必須係 array')
        return None
    out = []
    seen = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            errors.append(f'{label} 有無效 entry')
            return None
        key = norm_model(value)
        if key in seen:
            errors.append(f'{label} 有 canonical 重複')
            return None
        seen.add(key)
        out.append(value)
    if not out and not allow_empty:
        errors.append(f'{label} 必須非空')
        return None
    return out


def _canon_set(values, label, errors):
    norm = _norm_list(values, label, errors)
    return None if norm is None else {norm_model(v) for v in norm}


def _entry_has_evidence(entry):
    if not isinstance(entry, dict) or not entry:
        return False
    for key, value in entry.items():
        if key.endswith('_error') or key in ('url', 'model'):
            continue
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, (list, dict)) and value:
            return True
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            return True
    return False


def _output_facts(path):
    """Re-read an actual JSON output; never trust a claimed hash alone."""
    if not os.path.isfile(path) or os.path.islink(path):
        return {'sha256': None, 'size': None, 'validJson': False,
                'evidenceModels': [], 'error': 'missing'}
    try:
        body = open(path, 'rb').read()
    except OSError:
        return {'sha256': None, 'size': None, 'validJson': False,
                'evidenceModels': [], 'error': 'unreadable'}
    try:
        data = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return {'sha256': 'sha256:' + hashlib.sha256(body).hexdigest(),
                'size': len(body), 'validJson': False,
                'evidenceModels': [], 'error': 'invalid-json'}
    if not isinstance(data, (dict, list)) or not data:
        return {'sha256': 'sha256:' + hashlib.sha256(body).hexdigest(),
                'size': len(body), 'validJson': False,
                'evidenceModels': [], 'error': 'empty-or-wrong-type'}
    entries = list(data.values()) if isinstance(data, dict) else data
    models = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not _entry_has_evidence(entry):
            continue
        model = entry.get('model')
        if not isinstance(model, str) or not model.strip():
            # dict-keyed outputs (the production format) do not repeat model
            # inside every entry; the caller maps keys separately below.
            continue
        models.append(norm_model(model))
    return {'sha256': 'sha256:' + hashlib.sha256(body).hexdigest(),
            'size': len(body), 'validJson': True,
            'evidenceModels': sorted(set(models)), 'error': None}


def _output_models(path):
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {norm_model(k): _entry_has_evidence(v) for k, v in data.items()}
    out = {}
    for value in data:
        if isinstance(value, dict):
            out[norm_model(value.get('model', ''))] = _entry_has_evidence(value)
    return out


def _facts_with_models(path):
    facts = _output_facts(path)
    if not facts['validJson']:
        return facts
    try:
        models = _output_models(path)
    except (OSError, ValueError):
        facts.update(validJson=False, evidenceModels=[], error='invalid-json')
        return facts
    facts['evidenceModels'] = sorted(m for m, ok in models.items() if ok)
    return facts


def _safe_marker(marker):
    """Only public-safe, machine-readable fields may enter an observation."""
    if not isinstance(marker, dict):
        return None
    allowed = (
        'schemaVersion', 'script', 'attempted', 'succeeded', 'failed', 'skipped',
        'succeededModels', 'failedModels', 'alreadyVerified', 'covers',
        'failureReasons', 'coveragePendingModels', 'coveragePendingReasons',
        'catalogPages', 'cataloguePagesAttempted', 'cataloguePagesFailed',
        'catalogueFailureReasons', 'pageCounts', 'targetsFound',
        'sourceAcquiredAt', 'sourceAcquiredAtKind', 'cataloguePagesFromCache',
    )
    return {key: marker[key] for key in allowed if key in marker}


def _marker_errors(marker, script, live_models, staged_models):
    errors = []
    if not isinstance(marker, dict):
        return ['marker 唔係 object']
    if type(marker.get('schemaVersion')) is not int or marker.get('schemaVersion') != 1:
        errors.append('marker schemaVersion 必須係 1')
    if marker.get('script') != script:
        errors.append('marker script 唔符')
    counts = {}
    for key in ('attempted', 'succeeded', 'failed'):
        value = marker.get(key)
        if type(value) is not int or value < 0:
            errors.append(f'marker {key} 必須係非負整數')
        counts[key] = value if type(value) is int else None
    if all(v is not None for v in counts.values()) and counts['attempted'] != counts['succeeded'] + counts['failed']:
        errors.append('marker counts 唔一致')
    succeeded = _canon_set(marker.get('succeededModels'), 'succeededModels', errors)
    failed = _canon_set(marker.get('failedModels'), 'failedModels', errors)
    already = _canon_set(marker.get('alreadyVerified'), 'alreadyVerified', errors)
    covers = _canon_set(marker.get('covers'), 'covers', errors)
    if None in (succeeded, failed, already, covers):
        return errors
    if counts.get('succeeded') is not None and len(succeeded) != counts['succeeded']:
        errors.append('len(succeededModels) 唔符')
    if counts.get('failed') is not None and len(failed) != counts['failed']:
        errors.append('len(failedModels) 唔符')
    skipped = marker.get('skipped')
    if skipped is not None:
        if type(skipped) is not int or skipped < 0:
            errors.append('skipped 必須係非負整數')
        elif skipped != len(already):
            errors.append('len(alreadyVerified) 唔符 skipped')
    if covers != succeeded | already:
        errors.append('covers 唔等於 succeeded+already')
    if succeeded & failed:
        errors.append('模型同時 succeeded 同 failed')
    reasons = marker.get('failureReasons')
    reason_map = {}
    if reasons is not None:
        if not isinstance(reasons, dict):
            errors.append('failureReasons 必須係 object')
        else:
            for key, value in reasons.items():
                if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                    errors.append('failureReasons 有無效 entry')
                    continue
                reason_map[norm_model(key)] = value.strip()
            if set(reason_map) != failed:
                errors.append('failureReasons 同 failedModels 唔一致')
    elif failed:
        errors.append('有 failed 但冇 failureReasons')
    pending = _canon_set(marker.get('coveragePendingModels') or [], 'coveragePendingModels', errors)
    pending_reasons = marker.get('coveragePendingReasons') or {}
    pending_map = {}
    if not isinstance(pending_reasons, dict):
        errors.append('coveragePendingReasons 必須係 object')
    else:
        for key, value in pending_reasons.items():
            if not isinstance(key, str) or not isinstance(value, str) or not value.strip():
                errors.append('coveragePendingReasons 有無效 entry')
                continue
            pending_map[norm_model(key)] = value.strip()
    if pending is None:
        pending = set()
    if pending != set(pending_map):
        errors.append('coveragePendingModels 同 reasons 唔一致')
    if not pending <= failed:
        errors.append('coveragePendingModels 必須係 failed subset')
    unknown = {v for v in pending_map.values() if v not in SAFE_REASONS}
    if unknown:
        errors.append(f'coverage pending reason 未知：{sorted(unknown)}')
    hard = failed - pending
    if hard:
        errors.append(f'有未確認 coverage 嘅硬失敗：{sorted(hard)[:5]}')
    if pending_map:
        mismatch = {m for m, reason in pending_map.items()
                    if reason_map.get(m) != reason}
        if mismatch:
            errors.append('coveragePendingReasons 同 failureReasons 唔一致')
    # All claimed covers need real evidence.  A cached alreadyVerified model
    # must still exist in the retained live output, not only in an old claim.
    def _has(models, model):
        if isinstance(models, dict):
            return bool(models.get(model))
        return model in models

    for model in succeeded:
        if not (_has(staged_models, model) or _has(live_models, model)):
            errors.append(f'succeeded 但輸出冇 evidence：{model}')
    for model in already:
        if not _has(live_models, model):
            errors.append(f'cached alreadyVerified 但仍冇 live evidence：{model}')
    return errors


def _runtime():
    import run_official_batch
    return run_official_batch


def _expected_plan(stage, targets, target_scoped, *, stages=None, plan_fn=None):
    if stages is None or plan_fn is None:
        rob = _runtime()
        stages = stages if stages is not None else rob.STAGES
        plan_fn = plan_fn or rob.target_scoped_plan
    plan = stages[stage]
    return (plan_fn(plan, targets) if target_scoped else
            {'scripts': list(plan['scripts']), 'outputs': list(plan['outputs'])})


def _manifest_errors(manifest, stage, repo):
    errors = []
    if not isinstance(manifest, dict) or manifest.get('schemaVersion') != 1:
        return ['previous outputManifest schemaVersion 必須係 1']
    if manifest.get('stage') != stage:
        errors.append('previous outputManifest stage 唔符')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        return errors + ['previous outputManifest files 缺失']
    for name, meta in files.items():
        if not isinstance(name, str) or not isinstance(meta, dict):
            errors.append('previous outputManifest file entry 無效')
            continue
        digest = meta.get('sha256')
        size = meta.get('size')
        if not (isinstance(digest, str) and SHA_RE.match(digest)):
            errors.append(f'previous outputManifest {name} hash 唔係 full sha256')
            continue
        if type(size) is not int or size <= 0:
            errors.append(f'previous outputManifest {name} size 無效')
            continue
        path = os.path.join(repo, name)
        if not os.path.isfile(path) or os.path.islink(path):
            errors.append(f'previous snapshot 檔案缺失：{name}')
            continue
        body = open(path, 'rb').read()
        if len(body) != size:
            errors.append(f'previous snapshot size 唔符：{name}')
        if 'sha256:' + hashlib.sha256(body).hexdigest() != digest:
            errors.append(f'previous snapshot hash 唔符：{name}')
        try:
            data = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            errors.append(f'previous snapshot JSON 損壞：{name}')
            continue
        if not isinstance(data, (dict, list)) or not data:
            errors.append(f'previous snapshot JSON 空白／類型錯：{name}')
    body = {'schemaVersion': 1, 'stage': stage, 'files': files}
    if manifest.get('manifestHash') != sha256_id(canonical_json_bytes(body)):
        errors.append('previous outputManifest manifestHash 唔一致')
    return errors


def _previous_snapshot(previous_status, stage, repo):
    if not isinstance(previous_status, dict):
        return None
    claimed = previous_status.get('snapshotHash')
    manifest = previous_status.get('outputManifest')
    if claimed is None and manifest is None:
        return None
    if previous_status.get('decision') != 'advanced':
        raise ObservationError('previous status 有 snapshot 但 decision 唔係 advanced')
    if not (isinstance(claimed, str) and SHA_RE.match(claimed)):
        raise ObservationError('previous snapshotHash 唔係 full sha256')
    errors = _manifest_errors(manifest, stage, repo)
    if errors:
        raise ObservationError('; '.join(errors))
    if manifest.get('manifestHash') != claimed:
        raise ObservationError('previous snapshotHash 同 manifestHash 唔一致')
    last_success = previous_status.get('lastSuccessAt')
    if not _valid_utc(last_success):
        raise ObservationError('previous lastSuccessAt 無效')
    snapshot_id = previous_status.get('snapshotId')
    if not isinstance(snapshot_id, str) or not snapshot_id.strip():
        raise ObservationError('previous snapshotId 無效')
    return {'snapshotId': snapshot_id, 'snapshotHash': claimed,
            'lastSuccessAt': last_success, 'outputManifest': manifest}


def _recompute_coverage(targets, routes, scripts):
    """Derive covers/missing/pending/reasons from actual script markers."""
    covers = set()
    pending = set()
    script_reasons = {}
    for entry in scripts:
        marker = entry.get('marker') or {}
        for model in marker.get('covers') or []:
            if isinstance(model, str) and model.strip():
                covers.add(norm_model(model))
        for model in marker.get('coveragePendingModels') or []:
            if isinstance(model, str) and model.strip():
                pending.add(norm_model(model))
        for key, value in (marker.get('coveragePendingReasons') or {}).items():
            if isinstance(key, str) and isinstance(value, str):
                script_reasons[norm_model(key)] = value.strip()
    missing = [m for m in targets if norm_model(m) not in covers]
    reasons = {}
    for model in missing:
        reasons[model] = script_reasons.get(norm_model(model)) or (
            (routes.get(model) or {}).get('reason')
            or 'stage-source-did-not-cover-target')
    canonical_to_original = {norm_model(m): m for m in targets}
    pending_original = [canonical_to_original.get(m, m) for m in sorted(pending)]
    for model in pending_original:
        if model in reasons:
            continue
        reasons[model] = script_reasons.get(norm_model(model)) or (
            (routes.get(model) or {}).get('reason')
            or 'stage-source-did-not-cover-target')
    return covers, missing, pending_original, reasons


def _base_structure_errors(obs, *, expected_plan=None, routes_override=None):
    errors = []
    if not isinstance(obs, dict):
        return ['observation 唔係 object']
    if type(obs.get('schemaVersion')) is not int or obs.get('schemaVersion') != SCHEMA_VERSION:
        errors.append('observation schemaVersion 唔符')
    if obs.get('kind') != KIND:
        errors.append('observation kind 唔符')
    if obs.get('decision') != DECISION:
        errors.append('observation decision 唔符')
    if type(obs.get('stage')) is not int or obs.get('stage') not in (1, 2):
        errors.append('observation stage 唔符')
    if type(obs.get('targetScoped')) is not bool:
        errors.append('observation targetScoped 唔係 bool')
    if not _valid_utc(obs.get('observedAt')):
        errors.append('observation observedAt 無效')
    if obs.get('observedAtKind') not in ('batch-attempt', 'cache-replay'):
        errors.append('observation observedAtKind 無效')
    queue = obs.get('queue')
    if not isinstance(queue, dict):
        return errors + ['observation queue 缺失']
    if type(queue.get('stage')) is not int or queue.get('stage') not in (1, 2):
        errors.append('observation queue.stage 唔符')
    targets = _norm_list(queue.get('models'), 'queue.models', errors, allow_empty=False)
    canonical = _norm_list(queue.get('canonicalModels'), 'queue.canonicalModels', errors,
                           allow_empty=False)
    if not (isinstance(queue.get('hash'), str) and SHA_RE.match(queue.get('hash'))):
        errors.append('observation queue.hash 唔係 full sha256')
    if targets is None or canonical is None:
        return errors
    if [norm_model(m) for m in targets] != [norm_model(m) for m in canonical]:
        errors.append('queue.models 同 canonicalModels 唔一致')
    if queue.get('stage') != obs.get('stage'):
        errors.append('observation queue.stage 同 stage 唔一致')
    routes = obs.get('routes')
    if not isinstance(routes, dict) or set(routes) != set(targets):
        errors.append('observation routes 同 target set 唔一致')
        routes = {}
    rob = None if routes_override is not None else _runtime()
    for model in targets:
        route = routes.get(model)
        if not isinstance(route, dict):
            errors.append(f'route 缺失：{model}')
            continue
        if set(route) != {'brand', 'stage', 'source', 'reason', 'supported'}:
            errors.append(f'route 欄位唔符：{model}')
        if type(route.get('supported')) is not bool:
            errors.append(f'route.supported 唔係 bool：{model}')
        expected_route = (routes_override or {}).get(model) if routes_override is not None else rob.route_for_model(model)
        if route != expected_route:
            errors.append(f'route 同實際 code rule 唔一致：{model}')
    scripts = obs.get('scripts')
    if not isinstance(scripts, list) or not scripts:
        return errors + ['observation scripts 缺失']
    expected = expected_plan or _expected_plan(obs['stage'], targets, obs.get('targetScoped'))
    if [s.get('script') for s in scripts if isinstance(s, dict)] != expected['scripts']:
        errors.append('observation scripts 同可信 route/plan 唔一致')
    if [s.get('output') for s in scripts if isinstance(s, dict)] != expected['outputs']:
        errors.append('observation outputs 同可信 route/plan 唔一致')
    outputs = obs.get('outputs')
    if not isinstance(outputs, list) or [o.get('output') for o in outputs if isinstance(o, dict)] != expected['outputs']:
        errors.append('observation live outputs 同 plan 唔一致')
        outputs = []
    live_models = {}
    for item in outputs:
        if not isinstance(item, dict):
            errors.append('observation output entry 無效')
            continue
        name = item.get('output')
        if not (isinstance(item.get('sha256'), str) and SHA_RE.match(item.get('sha256'))):
            errors.append(f'observation {name} live hash 唔係 full sha256')
        if type(item.get('size')) is not int or item.get('size') <= 0:
            errors.append(f'observation {name} live size 無效')
        if item.get('validJson') is not True:
            errors.append(f'observation {name} live JSON 無效')
        evidence = _norm_list(item.get('evidenceModels') or [], f'{name}.evidenceModels', errors)
        if evidence is not None:
            live_models[name] = {norm_model(m) for m in evidence}
    for entry in scripts:
        if not isinstance(entry, dict):
            errors.append('observation script entry 無效')
            continue
        name = entry.get('script')
        out = entry.get('output')
        if type(entry.get('returncode')) is not int or entry.get('returncode') != 0:
            errors.append(f'{name} returncode 唔係 0')
        marker = entry.get('marker')
        staged = entry.get('staged')
        staged_models = set()
        if not isinstance(staged, dict):
            errors.append(f'{name} staged facts 缺失')
        else:
            if not (isinstance(staged.get('sha256'), str) and SHA_RE.match(staged.get('sha256'))):
                errors.append(f'{name} staged hash 唔係 full sha256')
            if type(staged.get('size')) is not int or staged.get('size') <= 0:
                errors.append(f'{name} staged size 無效')
            if staged.get('validJson') is not True:
                errors.append(f'{name} staged JSON 無效')
            evidence = _norm_list(staged.get('evidenceModels') or [],
                                  f'{name}.staged.evidenceModels', errors)
            if evidence is not None:
                staged_models = {norm_model(m) for m in evidence}
        errors.extend(f'{name}: {e}' for e in _marker_errors(
            marker, name, live_models.get(out, set()), staged_models))
    covers, missing, pending, reasons = _recompute_coverage(
        targets, routes, scripts)
    if set(queue.get('missingModels') or []) != set(missing):
        errors.append('queue.missingModels 同 markers 重算唔一致')
    if set(queue.get('missingCanonicalModels') or []) != {norm_model(m) for m in missing}:
        errors.append('queue.missingCanonicalModels 同 markers 重算唔一致')
    if list(queue.get('scriptCoveragePending') or []) != list(pending):
        errors.append('queue.scriptCoveragePending 同 markers 重算唔一致')
    if not missing and not pending:
        errors.append('pending observation 冇實際 coverage gap')
    if set(queue.get('reasons') or {}) != set(missing) | set(pending):
        errors.append('queue.reasons 同 missing/pending set 唔一致')
    for model in set(missing) | set(pending):
        if (queue.get('reasons') or {}).get(model) != reasons.get(model):
            errors.append(f'coverage reason 同 markers 重算唔一致：{model}')
    previous = obs.get('previousSnapshot')
    if previous is not None:
        if not isinstance(previous, dict):
            errors.append('previousSnapshot 唔係 object/null')
        else:
            manifest = previous.get('outputManifest')
            if not (isinstance(previous.get('snapshotHash'), str)
                    and SHA_RE.match(previous.get('snapshotHash'))):
                errors.append('previousSnapshot hash 唔係 full sha256')
            if not (isinstance(previous.get('snapshotId'), str)
                    and previous.get('snapshotId').strip()):
                errors.append('previousSnapshot id 無效')
            if not _valid_utc(previous.get('lastSuccessAt')):
                errors.append('previousSnapshot lastSuccessAt 無效')
            if not isinstance(manifest, dict):
                errors.append('previousSnapshot manifest 缺失')
            else:
                body = {'schemaVersion': 1, 'stage': manifest.get('stage'),
                        'files': manifest.get('files')}
                if manifest.get('schemaVersion') != 1 \
                        or manifest.get('stage') != obs.get('stage') \
                        or manifest.get('manifestHash') != sha256_id(canonical_json_bytes(body)) \
                        or manifest.get('manifestHash') != previous.get('snapshotHash'):
                    errors.append('previousSnapshot manifest 身份唔一致')
    body = {k: v for k, v in obs.items() if k != 'identityHash'}
    if obs.get('identityHash') != sha256_id(canonical_json_bytes(body)):
        errors.append('observation identityHash 唔一致')
    text = json.dumps(obs, ensure_ascii=False)
    for marker in ('/home/', '/tmp/', '/srv/', '/var/', 'file://', 'Authorization',
                   'Bearer ', 'token='):
        if marker in text:
            errors.append('observation 含非公開來源字串')
            break
    return errors


def validate_observation_structure(obs, *, expected_plan=None, routes_override=None):
    """Validate the portable observation without reading live repo files."""
    return _base_structure_errors(obs, expected_plan=expected_plan,
                                  routes_override=routes_override)


def build_observation(receipt, *, repo, staging_repo=None, previous_status=None,
                      observed_at=None, replay_observation=None, stages=None,
                      route_fn=None, plan_fn=None):
    """Create a portable observation from a real pending batch receipt.

    The caller must invoke this while the staging directory still exists; only
    small hashes/counts/evidence lists are copied, never the staged JSON/HTML.
    """
    if not isinstance(receipt, dict):
        raise ObservationError('receipt 唔係 object')
    if receipt.get('decision') not in (DECISION, 'pending') \
            or receipt.get('advanced') is not False:
        raise ObservationError('只可以為 queue-kept-pending-coverage 建立 observation')
    if receipt.get('failures') not in ([], None):
        raise ObservationError('receipt 仍有 failures')
    if receipt.get('failureClass') != 'none' or receipt.get('safeToContinue') is not True:
        raise ObservationError('receipt failureClass/safeToContinue 唔符')
    if receipt.get('coveragePending') is not True:
        raise ObservationError('receipt 冇 coveragePending')
    stage = receipt.get('stage')
    if type(stage) is not int or stage not in (1, 2):
        raise ObservationError('receipt stage 唔符')
    targets = receipt.get('targetModels')
    if not isinstance(targets, list) or not targets:
        raise ObservationError('receipt targetModels 缺失')
    queue_path = os.path.join(repo, 'update_queue.json')
    try:
        queue = _load_json(queue_path)
    except (OSError, ValueError) as exc:
        raise ObservationError(f'queue 讀取失敗：{type(exc).__name__}') from exc
    if not isinstance(queue, dict) or queue.get('stage') != stage \
            or queue.get('models') != targets:
        raise ObservationError('receipt targetModels/stage 同實際 queue 唔一致')
    queue_hash = _sha256_file(queue_path)
    if _norm_sha(receipt.get('queueHashBefore')) != queue_hash \
            or _norm_sha(receipt.get('queueHashAfter')) != queue_hash:
        raise ObservationError('receipt queue before/after 同實際 queue hash 唔一致')
    if receipt.get('staging') and (receipt['staging'].get('used') is not True
                                   or receipt['staging'].get('promoted') is not False):
        raise ObservationError('pending receipt staging 狀態唔符')
    target_scoped = receipt.get('targetScoped') is True
    route_fn = route_fn or _runtime().route_for_model
    expected = _expected_plan(stage, targets, target_scoped, stages=stages,
                              plan_fn=plan_fn)
    if receipt.get('selectedScripts') != expected['scripts'] \
            or receipt.get('selectedOutputs') != expected['outputs']:
        raise ObservationError('receipt selected scripts/outputs 同可信 plan 唔一致')
    routes = {}
    for model in targets:
        routes[model] = route_fn(model)
    if receipt.get('sourceRouting') not in (None, {}, routes):
        raise ObservationError('receipt sourceRouting 同重算 route 唔一致')
    receipt_scripts = receipt.get('scripts')
    if not isinstance(receipt_scripts, list) or len(receipt_scripts) != len(expected['scripts']):
        raise ObservationError('receipt scripts 數量唔符')
    scripts = []
    for idx, script in enumerate(expected['scripts']):
        entry = receipt_scripts[idx]
        out = expected['outputs'][idx]
        if not isinstance(entry, dict) or entry.get('script') != script \
                or entry.get('output') != out:
            raise ObservationError('receipt script/output 次序唔符')
        if type(entry.get('returncode')) is not int or entry.get('returncode') != 0:
            raise ObservationError(f'{script} returncode 唔係 0')
        marker = _safe_marker(entry.get('receipt'))
        if marker is None:
            raise ObservationError(f'{script} marker 缺失')
        live_path = os.path.join(repo, out)
        staged_path = os.path.join(staging_repo or repo, out)
        live = _facts_with_models(live_path)
        staged = _facts_with_models(staged_path)
        if not live['validJson'] or not staged['validJson']:
            raise ObservationError(f'{out} live/staged JSON 無效')
        live_models = {norm_model(m) for m in live['evidenceModels']}
        staged_models = {norm_model(m) for m in staged['evidenceModels']}
        marker_errors = _marker_errors(marker, script, live_models, staged_models)
        if marker_errors:
            raise ObservationError('; '.join(marker_errors))
        if entry.get('stagedOutputHash') not in (None, staged['sha256'], staged['sha256'][7:]):
            raise ObservationError(f'{script} staged hash 同實際 staged bytes 唔一致')
        scripts.append({
            'script': script, 'output': out, 'returncode': 0,
            'marker': marker,
            'staged': {'sha256': staged['sha256'], 'size': staged['size'],
                       'validJson': True,
                       'evidenceModels': staged['evidenceModels']},
        })
    outputs = []
    for out in expected['outputs']:
        facts = _facts_with_models(os.path.join(repo, out))
        if not facts['validJson']:
            raise ObservationError(f'{out} 保留 output 無效')
        outputs.append({'output': out, 'sha256': facts['sha256'],
                        'size': facts['size'], 'validJson': True,
                        'evidenceModels': facts['evidenceModels']})
    covers, missing, pending, reasons = _recompute_coverage(targets, routes, scripts)
    if not missing and not pending:
        raise ObservationError('冇實際 coverage gap，唔應該建立 pending observation')
    # Missing reasons from the receipt are not trusted; only route/marker facts
    # above are used.  This keeps a hand-written list from becoming proof.
    emsd_path = os.path.join(repo, 'emsd_空調能源標籤.csv')
    emsd = None
    if os.path.isfile(emsd_path):
        emsd = {'input': 'emsd_空調能源標籤.csv',
                'snapshotHash': _sha256_file(emsd_path)}
    previous = _previous_snapshot(previous_status, stage, repo)
    now = _utc_now()
    stamp = observed_at or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if not _valid_utc(stamp, now=now):
        raise ObservationError('observedAt 無效或未來時間')
    canonical = [norm_model(m) for m in targets]
    observation = {
        'schemaVersion': SCHEMA_VERSION,
        'kind': KIND,
        'decision': DECISION,
        'observedAt': stamp,
        'observedAtKind': 'batch-attempt',
        'stage': stage,
        'targetScoped': target_scoped,
        'queue': {
            'stage': stage,
            'models': list(targets),
            'canonicalModels': canonical,
            'hash': queue_hash,
            'missingModels': missing,
            'missingCanonicalModels': [norm_model(m) for m in missing],
            'scriptCoveragePending': pending,
            'reasons': dict(sorted(reasons.items())),
        },
        'emsd': emsd,
        'routes': routes,
        'scripts': scripts,
        'outputs': outputs,
        'previousSnapshot': previous,
    }
    if replay_observation is not None:
        replay_errors = validate_observation(replay_observation, repo=repo,
                                             previous_status=previous_status,
                                             require_emsd=emsd is not None,
                                             enforce_age=False,
                                             expected_plan=expected,
                                             routes_override=routes)
        if replay_errors:
            raise ObservationError('replay observation 無效：' + '; '.join(replay_errors[:5]))
        old_core = {k: v for k, v in replay_observation.items()
                    if k not in ('observedAt', 'observedAtKind', 'identityHash')}
        new_core = {k: v for k, v in observation.items()
                    if k not in ('observedAt', 'observedAtKind', 'identityHash')}
        if old_core != new_core:
            raise ObservationError('replay observation 證據已改變，唔可以保留原觀察時間')
        observation['observedAt'] = replay_observation['observedAt']
        observation['observedAtKind'] = 'cache-replay'
    body = {k: v for k, v in observation.items() if k != 'identityHash'}
    observation['identityHash'] = sha256_id(canonical_json_bytes(body))
    errors = validate_observation(observation, repo=repo,
                                  previous_status=previous_status,
                                  require_emsd=emsd is not None,
                                  enforce_age=False,
                                  expected_plan=expected, routes_override=routes)
    if errors:
        raise ObservationError('; '.join(errors[:8]))
    return observation


def validate_observation(obs, *, repo=None, receipt=None, previous_status=None,
                         require_emsd=True, enforce_age=False, max_age_hours=36,
                         now=None, expected_plan=None, routes_override=None):
    """Recompute/verify a portable observation against actual local inputs."""
    errors = _base_structure_errors(obs, expected_plan=expected_plan,
                                    routes_override=routes_override)
    if not isinstance(obs, dict):
        return errors
    now_dt = _utc_now(now)
    if _valid_utc(obs.get('observedAt')):
        observed = _dt.datetime.fromisoformat(obs['observedAt'].replace('Z', '+00:00'))
        if observed > now_dt + _dt.timedelta(minutes=5):
            errors.append('observation observedAt 係未來')
        elif enforce_age and (now_dt - observed).total_seconds() > max_age_hours * 3600:
            errors.append('observation observedAt 已過期')
    queue = obs.get('queue') or {}
    stage = obs.get('stage')
    targets = queue.get('models') if isinstance(queue.get('models'), list) else []
    previous = obs.get('previousSnapshot')
    if repo is not None:
        queue_path = os.path.join(repo, 'update_queue.json')
        try:
            actual_queue = _load_json(queue_path)
        except (OSError, ValueError):
            errors.append('actual queue 缺失或無效')
            actual_queue = None
        if isinstance(actual_queue, dict):
            if actual_queue.get('stage') != stage:
                errors.append('actual queue stage 唔一致')
            if actual_queue.get('models') != targets:
                errors.append('actual queue models 唔一致')
            try:
                if _sha256_file(queue_path) != queue.get('hash'):
                    errors.append('actual queue hash 唔一致')
            except OSError:
                errors.append('actual queue hash 無法讀取')
        csv_path = os.path.join(repo, 'emsd_空調能源標籤.csv')
        emsd = obs.get('emsd')
        if require_emsd:
            if not isinstance(emsd, dict) or not (
                    isinstance(emsd.get('snapshotHash'), str)
                    and SHA_RE.match(emsd.get('snapshotHash'))):
                errors.append('observation 缺 EMSD input hash')
            elif not os.path.isfile(csv_path):
                errors.append('actual EMSD CSV 缺失')
            elif _sha256_file(csv_path) != emsd['snapshotHash']:
                errors.append('actual EMSD CSV hash 唔一致')
        elif isinstance(emsd, dict) and emsd.get('snapshotHash'):
            if not os.path.isfile(csv_path) or _sha256_file(csv_path) != emsd['snapshotHash']:
                errors.append('actual EMSD CSV hash 唔一致')
        for item in obs.get('outputs') or []:
            if not isinstance(item, dict) or not isinstance(item.get('output'), str):
                continue
            path = os.path.join(repo, item['output'])
            facts = _facts_with_models(path)
            if facts['sha256'] != item.get('sha256'):
                errors.append(f"actual output hash 唔一致：{item['output']}")
            if facts['size'] != item.get('size'):
                errors.append(f"actual output size 唔一致：{item['output']}")
            if facts['validJson'] is not True:
                errors.append(f"actual output JSON 無效：{item['output']}")
            if facts['evidenceModels'] != item.get('evidenceModels'):
                errors.append(f"actual output evidence 唔一致：{item['output']}")
        if isinstance(previous, dict):
            errors.extend(_manifest_errors(previous.get('outputManifest'), stage, repo))
            if previous.get('snapshotHash') != (
                    previous.get('outputManifest') or {}).get('manifestHash'):
                errors.append('previous snapshot hash 同 manifest 唔一致')
        if previous_status is not None:
            try:
                derived = _previous_snapshot(previous_status, stage, repo)
            except ObservationError as exc:
                errors.append(str(exc))
            else:
                if derived != previous:
                    errors.append('previousSnapshot 同 previous status 唔一致')
    if receipt is not None:
        if not isinstance(receipt, dict):
            errors.append('receipt 唔係 object')
        else:
            if receipt.get('decision') != DECISION:
                errors.append('receipt decision 唔符')
            if receipt.get('stage') != stage:
                errors.append('receipt stage 唔符')
            if receipt.get('targetModels') != targets:
                errors.append('receipt targetModels 唔符')
            if _norm_sha(receipt.get('queueHashBefore')) != queue.get('hash') \
                    or _norm_sha(receipt.get('queueHashAfter')) != queue.get('hash'):
                errors.append('receipt queue hash 唔符')
            if receipt.get('failures') not in ([], None) \
                    or receipt.get('failureClass') != 'none' \
                    or receipt.get('safeToContinue') is not True:
                errors.append('receipt 唔係安全成功狀態')
            reported = receipt.get('scripts')
            if isinstance(reported, list) and len(reported) == len(obs.get('scripts') or []):
                for got, want in zip(reported, obs.get('scripts') or []):
                    if not isinstance(got, dict):
                        errors.append('receipt script entry 無效')
                        continue
                    if got.get('script') != want.get('script') \
                            or got.get('output') != want.get('output') \
                            or got.get('returncode') != 0:
                        errors.append('receipt script 同 observation 唔一致')
                    marker = _safe_marker(got.get('receipt'))
                    if marker != want.get('marker'):
                        errors.append('receipt marker 同 observation 唔一致')
            else:
                errors.append('receipt scripts 同 observation 數量唔一致')
    return errors


def expected_pending(official_doc, *, repo=None, previous_status=None,
                     metadata=None, now=None, enforce_age=False,
                     max_age_hours=36, require_emsd=True):
    """Return a small decision object for a pending official status document.

    ``trusted`` means the versioned observation passed all available checks.
    A legacy pending status (no observation) is never trusted.
    """
    if not isinstance(official_doc, dict):
        return {'pending': False, 'trusted': False, 'errors': ['status 唔係 object']}
    if official_doc.get('decision') != DECISION:
        return {'pending': False, 'trusted': False, 'errors': ['decision 唔係 pending coverage']}
    obs = official_doc.get('coverageObservation')
    if not isinstance(obs, dict):
        return {'pending': True, 'trusted': False, 'errors': ['legacy pending 冇 observation']}
    errors = validate_observation(
        obs, repo=repo, previous_status=previous_status,
        require_emsd=require_emsd, enforce_age=enforce_age,
        max_age_hours=max_age_hours, now=now)
    queue = obs.get('queue') or {}
    previous = obs.get('previousSnapshot')
    if official_doc.get('pendingCoverage') is not True:
        errors.append('status pendingCoverage 唔係 true')
    if official_doc.get('stage') != obs.get('stage'):
        errors.append('status stage 同 observation 唔一致')
    if official_doc.get('generatedAt') != obs.get('observedAt'):
        errors.append('status generatedAt 同 observation observedAt 唔一致')
    if official_doc.get('missingModels') != queue.get('missingModels'):
        errors.append('status missingModels 同 observation 唔一致')
    if official_doc.get('missingCanonicalModels') != queue.get('missingCanonicalModels'):
        errors.append('status missingCanonicalModels 同 observation 唔一致')
    if official_doc.get('scriptCoveragePending') != queue.get('scriptCoveragePending'):
        errors.append('status scriptCoveragePending 同 observation 唔一致')
    if official_doc.get('coveragePendingReasons') != queue.get('reasons'):
        errors.append('status coverage reasons 同 observation 唔一致')
    has_previous = isinstance(previous, dict)
    if has_previous:
        if official_doc.get('snapshotHash') != previous.get('snapshotHash') \
                or official_doc.get('snapshotId') != previous.get('snapshotId') \
                or official_doc.get('outputManifest') != previous.get('outputManifest'):
            errors.append('status previous snapshot 同 observation 唔一致')
    else:
        if official_doc.get('snapshotHash') is not None \
                or official_doc.get('snapshotId') is not None \
                or official_doc.get('outputManifest') is not None:
            errors.append('status 冇完整 snapshot 但宣稱有 snapshot')
    return {
        'pending': True,
        'trusted': not errors,
        'errors': errors[:20],
        'observation': obs,
        'observationHash': obs.get('identityHash'),
        'observedAt': obs.get('observedAt'),
        'missingModels': list(queue.get('missingModels') or []),
        'missingCanonicalModels': list(queue.get('missingCanonicalModels') or []),
        'missingCount': len(queue.get('missingModels') or []),
        'scriptCoveragePending': list(queue.get('scriptCoveragePending') or []),
        'stage': obs.get('stage'),
        'targetCount': len(queue.get('models') or []),
        'queueHash': queue.get('hash'),
        'emsdSnapshotHash': (obs.get('emsd') or {}).get('snapshotHash'),
        'previousSnapshot': previous,
        'officialSnapshotHash': (previous or {}).get('snapshotHash'),
        'officialManifestHash': ((previous or {}).get('outputManifest') or {}).get('manifestHash'),
    }


def coverage_fingerprint(decision):
    if not isinstance(decision, dict) or not decision.get('trusted'):
        return 'coverage-pending-untrusted'
    return 'coverage-pending:' + str(decision.get('missingCount', 0))
