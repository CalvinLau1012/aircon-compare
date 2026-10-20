#!/usr/bin/env python3
"""Read-only BigGo recovery preparation for the 2026-10-20 publish-uncertain run.

This tool reconstructs a candidate stage result/effects from immutable evidence
and candidate code.  It performs no network, coordinator, provider or local
data writes.  A separate Root-operated apply/CAS step remains required.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / 'scripts'
for p in (str(BASE), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import biggo_apply as apply_mod  # noqa: E402
import biggo_coordinator as coord  # noqa: E402
import fetch_biggo as fetch  # noqa: E402
import model_lifecycle as lifecycle  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id, sha256_json  # noqa: E402
from crawl_utils import canonical_model_key, norm_model  # noqa: E402


class RecoveryError(ValueError):
    pass


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise RecoveryError(f'cannot read {Path(path).name}: {type(e).__name__}') from e


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _verify_original_inputs(evidence):
    manifest = _load(evidence / 'original-inputs-manifest.json')
    files = manifest.get('files')
    if not isinstance(files, dict):
        raise RecoveryError('original input manifest missing files')
    for name, expected in files.items():
        path = evidence / 'original-inputs' / name
        if not path.is_file() or path.is_symlink():
            raise RecoveryError(f'missing original input: {name}')
        actual_hash = _sha_file(path)
        expected_hash = expected.get('sha256')
        if expected_hash and not str(expected_hash).startswith('sha256:'):
            expected_hash = 'sha256:' + str(expected_hash)
        if path.stat().st_size != expected.get('bytes') or actual_hash != expected_hash:
            raise RecoveryError(f'original input hash mismatch: {name}')
    return manifest


def _verify_artifact(evidence):
    receipts = _load(evidence / 'run-39384334727/download-receipts.json')
    artifact = None
    for item in receipts:
        if str(item.get('archive', '')).endswith('artifact-12247731259.zip'):
            artifact = item
            break
    if artifact is None:
        raise RecoveryError('missing biggo stage artifact receipt')
    archive = evidence / 'run-39384334727' / 'artifact-12247731259.zip'
    artifact_hash = artifact.get('sha256')
    if artifact_hash and not str(artifact_hash).startswith('sha256:'):
        artifact_hash = 'sha256:' + str(artifact_hash)
    if not archive.is_file() or _sha_file(archive) != artifact_hash \
            or archive.stat().st_size != artifact.get('bytes'):
        raise RecoveryError('artifact zip hash/size mismatch')
    return artifact


def _verify_preconditions(evidence):
    cond = _load(evidence / 'recovery-preconditions.json')
    state_path = evidence / 'recovery-before-state.json'
    diag_path = evidence / 'recovery-before-diagnostic.json'
    state_expected = (cond.get('state') or {}).get('sha256')
    if state_expected and not str(state_expected).startswith('sha256:'):
        state_expected = 'sha256:' + str(state_expected)
    if _sha_file(state_path) != state_expected:
        raise RecoveryError('recovery state hash mismatch')
    diag_expected = (cond.get('diagnostic') or {}).get('sha256')
    if diag_expected and not str(diag_expected).startswith('sha256:'):
        diag_expected = 'sha256:' + str(diag_expected)
    if _sha_file(diag_path) != diag_expected:
        raise RecoveryError('recovery diagnostic hash mismatch')
    state = _load(state_path)
    if state.get('status') != 'needs_review' or state.get('cycleId') != '2026-10-10:5/7' \
            or state.get('stage') != 5:
        raise RecoveryError('pre-state is not the expected needs_review stage')
    if state.get('leaseOwner') is not None or int(state.get('leaseExpiresAt') or 0) != 0:
        raise RecoveryError('foreign active lease present')
    if state.get('requestAttempts') != {'token': 1, 'search': 149}:
        raise RecoveryError('request attempts do not match source evidence')
    return state, _load(diag_path)


def _brand_lookup(csv_path):
    out = {}
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[1].strip() or row[1].strip() == '型號':
                continue
            out.setdefault(norm_model(row[1]), row[0].strip())
    return out


def build_recovery(evidence):
    evidence = Path(evidence)
    manifest = _verify_original_inputs(evidence)
    artifact_receipt = _verify_artifact(evidence)
    pre_state, diagnostic = _verify_preconditions(evidence)
    artifact = evidence / 'run-39384334727/artifact-12247731259'
    review = _load(artifact / 'biggo-review/stage-snapshot.json')
    status = _load(artifact / 'biggo-stage-status.json')
    base = _load(evidence / 'original-inputs/biggo_prices.json')
    new = review.get('snapshot')
    outcomes = review.get('outcomes')
    meta = _load(evidence / 'original-inputs/prices_meta.json')
    pre_tracking = _load(evidence / 'original-inputs/model_status.json')
    pre_wrapper = _load(evidence / 'original-inputs/model_blacklist.json')
    pre_black = pre_wrapper.get('models') if isinstance(pre_wrapper, dict) else None
    if not isinstance(base, dict) or not isinstance(new, dict) or not isinstance(outcomes, list) \
            or not isinstance(pre_tracking, dict) or not isinstance(pre_black, dict):
        raise RecoveryError('original/review data shape invalid')
    priced = [o for o in outcomes if isinstance(o, dict) and o.get('outcome') == 'priced']
    clean = [o for o in outcomes if isinstance(o, dict) and o.get('outcome') == 'clean_miss']
    if len(priced) != 101 or len(clean) != 7 or review.get('counters') != {
            'got': 102, 'cleanMiss': 7, 'netErrors': 0}:
        raise RecoveryError('source artifact counters/outcomes are not the expected shape')
    extra = sorted(k for k in new if k not in base)
    if extra != ['CHK18FBVX']:
        raise RecoveryError(f'unexpected snapshot delta: {extra!r}')
    model = extra[0]
    revived_key = next((k for k in pre_black if str(k).endswith('|' + model)), None)
    if revived_key is None:
        raise RecoveryError('revived canonical key cannot be proven from original blacklist')
    price = new[model]
    if not isinstance(price, dict) or not str(price.get('price', '')).startswith('$'):
        raise RecoveryError('revived price missing/invalid')
    brand = _brand_lookup(evidence / 'original-inputs/emsd_空調能源標籤.csv')
    brand_of = lambda m: brand.get(norm_model(m)) or 'UNKNOWN'
    idx = 4
    batch_id = f"{meta.get('price_batch_start')}:{idx + 1}/{fetch.PRICE_BATCH_DAYS}"
    got_models = [o['model'] for o in priced]
    clean_models = [o['model'] for o in clean]
    post_tracking, post_black = lifecycle.plan_record_results(
        [(m, True) for m in got_models] + [(m, False) for m in clean_models],
        protected=fetch.protected_models(), batch_id=batch_id,
        brand_of=brand_of, tracking=pre_tracking, blacklist=pre_black,
        today='2026-10-20')
    post_tracking, post_black, was = lifecycle.plan_revive(
        revived_key, tracking=post_tracking, blacklist=post_black)
    if not was:
        raise RecoveryError('revived key was not in the original blacklist')
    effects = lifecycle.compute_effects(pre_tracking, pre_black, post_tracking, post_black)
    new_outcomes = copy.deepcopy(outcomes) + [{
        'model': model, 'canonicalKey': revived_key,
        'outcome': 'priced', 'price': copy.deepcopy(price)}]
    counters = {'got': sum(1 for o in new_outcomes if o['outcome'] == 'priced'),
                'cleanMiss': sum(1 for o in new_outcomes if o['outcome'] == 'clean_miss'),
                'netErrors': sum(1 for o in new_outcomes if o['outcome'] == 'net_error')}
    stage = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': status['cycle'], 'stage': 5, 'mode': 'price-batch',
        'generatedAt': status['at'], 'counters': counters,
        'requestAttempts': {'token': 1, 'search': 149},
        'responseStatusCounts': copy.deepcopy(pre_state.get('responseStatusCounts') or {'200': 1}),
        'outcomes': new_outcomes,
        'blacklistReview': {
            'quotaIndex': idx,
            'reviewed': [{'key': revived_key, 'model': model,
                          'result': 'revived', 'price': copy.deepcopy(price)}],
            'confirmedCount': 39,
            'confirmedDetailsNotProven': True,
        },
        'effects': effects,
        'preState': {'trackingHash': sha256_json(pre_tracking),
                     'blacklistHash': sha256_json(pre_black)},
        'postState': {'trackingHash': sha256_json(post_tracking),
                      'blacklistHash': sha256_json(post_black)},
    }
    stage['effectsHash'] = sha256_json(effects)
    coord.validate_stage_result(stage, base, new, cycle_id=stage['cycleId'],
                                stage=5, mode='price-batch')
    # Re-apply the effects and verify both post images independently.
    if sha256_json(apply_mod._apply_tracking(pre_tracking, effects)) != stage['postState']['trackingHash']:
        raise RecoveryError('tracking effects do not reproduce postState')
    if sha256_json(apply_mod._apply_blacklist(pre_black, effects)) != stage['postState']['blacklistHash']:
        raise RecoveryError('blacklist effects do not reproduce postState')
    cyc = coord.cycle_slug(stage['cycleId'])
    manifest_doc = {
        'cycleId': stage['cycleId'], 'stage': 5, 'mode': 'price-batch',
        'writer': 'root-recovery', 'writerKind': 'self-host',
        'commit': manifest.get('sourceHead'), 'publishedAt': status['at'],
        'schemaVersion': coord.BUNDLE_MANIFEST_SCHEMA_VERSION,
        'snapshotSchemaVersion': coord.SNAPSHOT_SCHEMA_VERSION,
        'stageResultSchemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'recordCount': len(new),
        'baseSnapshotHash': sha256_json(base),
        'newSnapshotHash': sha256_json(new),
        'snapshotHash': sha256_json(new),
        'stageResultHash': sha256_json(stage),
        'files': {
            'base': f'{coord.SNAPSHOT_DIR_DEFAULT}/{cyc}/base-biggo_prices.json',
            'snapshot': f'{coord.SNAPSHOT_DIR_DEFAULT}/{cyc}/biggo_prices.json',
            'stageResult': f'{coord.SNAPSHOT_DIR_DEFAULT}/{cyc}/stage-result.json',
        },
        'authority': 'coordinator-stage-result-v1',
    }
    bundle_hash = sha256_json(manifest_doc)
    target = apply_mod._target_meta(meta, stage, manifest_doc, 0, applied_at=None)
    target['appliedSnapshotHash'] = sha256_json(new)
    target['appliedBundleHash'] = bundle_hash
    target.pop('appliedAt', None)
    target['_recovery'] = {
        'appliedAtPolicy': 'set by Root apply from actual apply time; not recovered',
        'originalGeneratedAt': status['at'],
        'originalAcquisitionEvidence': 'price.updated=2026-10-20; log window 07:13:40..07:19:53Z',
    }
    return {
        'schemaVersion': 1, 'kind': 'aircon-biggo-recovery-candidate',
        'sourceHead': manifest.get('sourceHead'),
        'sourceRunId': 39384334727,
        'artifactSha256': ('sha256:' + artifact_receipt['sha256']
                            if not str(artifact_receipt.get('sha256', '')).startswith('sha256:')
                            else artifact_receipt.get('sha256')),
        'originalInputs': {k: v for k, v in _load(evidence / 'original-inputs-manifest.json')['files'].items()},
        'baseSnapshot': base, 'newSnapshot': new, 'stageResult': stage,
        'manifest': manifest_doc, 'bundleHash': bundle_hash,
        'pricesMetaTarget': target,
        'preState': pre_state, 'diagnostic': diagnostic,
        'reviewEvidence': {
            'revived': [{'key': revived_key, 'model': model,
                         'result': 'revived', 'price': copy.deepcopy(price)}],
            'confirmedCount': 39,
            'confirmedDetailsNotProven': True,
            'reason': 'original stage-snapshot omitted review details; confirmed names are not fabricated',
        },
        'derived': {
            'revivedMapping': 'cross-checked original blacklist key + sole snapshot delta + status/log evidence',
            'postStateEffects': 'deterministic lifecycle planners using original inputs and candidate code',
            'stageGeneratedAt': 'artifact status at 2026-10-20T07:19:53Z; not recovery time',
            'acquisition': 'price.updated=2026-10-20 and log window; no exact acquisition time claimed',
        },
        'notProven': ['39 confirmed review names', 'exact acquisition sub-second time',
                      'coordinator commit/apply after recovery'],
        'providerRequests': 0, 'coordinatorWrites': 0,
    }


def _atomic_exclusive(path, data):
    if os.path.lexists(path):
        raise RecoveryError(f'output exists: {Path(path).name}')
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description='prepare read-only BigGo recovery packet')
    ap.add_argument('--evidence', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    if args.verify:
        try:
            packet = _load(out / 'recovery-packet.json')
            rebuilt = build_recovery(Path(args.evidence))
            if packet.get('bundleHash') != rebuilt.get('bundleHash') or \
                    sha256_json(packet.get('stageResult')) != sha256_json(rebuilt.get('stageResult')):
                raise RecoveryError('recovery packet does not reproduce')
        except Exception as e:  # noqa: BLE001
            print(f'❌ recovery verify failed: {type(e).__name__}: {str(e)[:160]}',
                  file=sys.stderr)
            return 2
        print(json.dumps({'ok': True, 'verified': str(out / 'recovery-packet.json')},
                         ensure_ascii=False))
        return 0
    if out.exists():
        print('❌ out-dir must not exist (no-clobber)', file=sys.stderr)
        return 2
    try:
        packet = build_recovery(Path(args.evidence))
    except Exception as e:  # noqa: BLE001
        print(f'❌ recovery preparation failed: {type(e).__name__}: {str(e)[:160]}',
              file=sys.stderr)
        return 2
    try:
        out.mkdir(parents=True)
        _atomic_exclusive(out / 'recovery-packet.json', packet)
        _atomic_exclusive(out / 'base-biggo_prices.json', packet['baseSnapshot'])
        _atomic_exclusive(out / 'biggo_prices.json', packet['newSnapshot'])
        _atomic_exclusive(out / 'stage-result.json', packet['stageResult'])
        _atomic_exclusive(out / 'manifest.json', packet['manifest'])
        _atomic_exclusive(out / 'prices_meta-target.json', packet['pricesMetaTarget'])
        _atomic_exclusive(out / 'review-evidence.json', packet['reviewEvidence'])
    except OSError as e:
        print(f'❌ recovery packet write failed: {type(e).__name__}', file=sys.stderr)
        return 2
    print(json.dumps({'ok': True, 'packet': str(out / 'recovery-packet.json'),
                      'bundleHash': packet['bundleHash'],
                      'stageResultHash': sha256_json(packet['stageResult']),
                      'providerRequests': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
