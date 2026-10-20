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
import datetime as _dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

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


EXPECTED_INPUTS = (
    'biggo_prices.json',
    'prices_meta.json',
    'model_status.json',
    'model_blacklist.json',
    'emsd_空調能源標籤.csv',
)
EXPECTED_RUN_ID = 39384334727
EXPECTED_SOURCE_HEAD = '743a929a284adf7ae3f4321d4683b01d40e99981'
EXPECTED_ARTIFACT = 'artifact-12247731259.zip'
EXPECTED_ZIP_MEMBERS = ('biggo-stage-status.json', 'biggo-review/stage-snapshot.json')
EXPECTED_REVIVED_KEY = 'CARRIER|CHK18FBVX'
EXPECTED_REVIVED_MODEL = 'CHK18FBVX'
SOURCE_COMPLETION_BOUND = '2026-10-20T07:19:53Z'
SOURCE_LOG_WINDOW_START = '2026-10-20T07:13:40Z'
OUTPUT_FILES = (
    ('base-biggo_prices.json', 'baseSnapshot'),
    ('biggo_prices.json', 'newSnapshot'),
    ('stage-result.json', 'stageResult'),
    ('manifest.json', 'manifest'),
    ('prices_meta-target.json', 'pricesMetaTarget'),
    ('review-evidence.json', 'reviewEvidence'),
)
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def _load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise RecoveryError(f'cannot read {Path(path).name}: {type(e).__name__}') from e


def _load_regular(path):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise RecoveryError(f'not a regular file: {p.name}')
    return _load(p)


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _hash_value(value, label):
    text = str(value or '')
    if text.startswith('sha256:'):
        text = text[7:]
    if not SHA_RE.fullmatch(text):
        raise RecoveryError(f'{label} sha256 missing/invalid')
    return 'sha256:' + text


def _verify_original_inputs(evidence):
    manifest = _load_regular(evidence / 'original-inputs-manifest.json')
    if manifest.get('sourceHead') != EXPECTED_SOURCE_HEAD:
        raise RecoveryError('original input sourceHead mismatch')
    files = manifest.get('files')
    if not isinstance(files, dict) or set(files) != set(EXPECTED_INPUTS):
        raise RecoveryError('original input manifest must contain exactly the five required files')
    for name in EXPECTED_INPUTS:
        expected = files.get(name)
        if not isinstance(expected, dict):
            raise RecoveryError(f'original input entry invalid: {name}')
        expected_hash = _hash_value(expected.get('sha256'), f'original input {name}')
        if not isinstance(expected.get('bytes'), int) or isinstance(expected.get('bytes'), bool) \
                or expected['bytes'] < 0:
            raise RecoveryError(f'original input size missing/invalid: {name}')
        path = evidence / 'original-inputs' / name
        if path.is_symlink() or not path.is_file():
            raise RecoveryError(f'missing original input: {name}')
        if path.stat().st_size != expected['bytes'] or _sha_file(path) != expected_hash:
            raise RecoveryError(f'original input hash/size mismatch: {name}')
    return manifest


def _zip_member_bytes(archive):
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            if set(names) != set(EXPECTED_ZIP_MEMBERS) or len(names) != len(set(names)):
                raise RecoveryError('artifact zip member set is ambiguous')
            for info in infos:
                name = info.filename
                parts = PurePosixPath(name).parts
                if not name or name.startswith('/') or '\\' in name or '..' in parts or '.' in parts:
                    raise RecoveryError('artifact zip member path is unsafe')
                if info.is_dir():
                    raise RecoveryError('artifact zip contains a directory')
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RecoveryError('artifact zip contains a symlink')
            out = {}
            for name in EXPECTED_ZIP_MEMBERS:
                try:
                    out[name] = zf.read(name)
                except KeyError as e:
                    raise RecoveryError(f'artifact zip member missing: {name}') from e
            return out
    except RecoveryError:
        raise
    except (OSError, zipfile.BadZipFile, UnicodeError) as e:
        raise RecoveryError(f'cannot read artifact zip: {type(e).__name__}') from e


def _verify_artifact(evidence, input_manifest):
    run_dir = evidence / f'run-{EXPECTED_RUN_ID}'
    run = _load_regular(run_dir / 'run.json')
    if run.get('id') != EXPECTED_RUN_ID or run.get('head_sha') != EXPECTED_SOURCE_HEAD:
        raise RecoveryError('run identity/head does not match source evidence')
    if run.get('head_sha') != input_manifest.get('sourceHead'):
        raise RecoveryError('run head and original-input sourceHead disagree')
    receipts = _load_regular(run_dir / 'download-receipts.json')
    if not isinstance(receipts, list):
        raise RecoveryError('artifact receipts are invalid')
    matches = [r for r in receipts if isinstance(r, dict)
               and Path(str(r.get('archive', ''))).name == EXPECTED_ARTIFACT]
    if len(matches) != 1:
        raise RecoveryError('artifact receipt must be unique')
    artifact = matches[0]
    archive = run_dir / EXPECTED_ARTIFACT
    if archive.is_symlink() or not archive.is_file():
        raise RecoveryError('artifact zip missing or symlink')
    expected_hash = _hash_value(artifact.get('sha256'), 'artifact')
    if not isinstance(artifact.get('bytes'), int) or isinstance(artifact.get('bytes'), bool) \
            or artifact['bytes'] < 0 or archive.stat().st_size != artifact['bytes'] \
            or _sha_file(archive) != expected_hash:
        raise RecoveryError('artifact zip hash/size mismatch')
    members = _zip_member_bytes(archive)
    try:
        status = json.loads(members['biggo-stage-status.json'].decode('utf-8'))
        review = json.loads(members['biggo-review/stage-snapshot.json'].decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as e:
        raise RecoveryError('artifact zip JSON is invalid') from e
    if status.get('cycle') != '2026-10-10:5/7' or status.get('at') != SOURCE_COMPLETION_BOUND \
            or status.get('stage') != 'publish' or status.get('mode') != 'price-batch':
        raise RecoveryError('artifact status identity mismatch')
    if status.get('requests') != {'token': 1, 'search': 149}:
        raise RecoveryError('artifact status request counts mismatch')
    if review.get('status') != 'completed' or not isinstance(review.get('snapshot'), dict) \
            or not isinstance(review.get('outcomes'), list) \
            or review.get('counters') != {'got': 102, 'cleanMiss': 7, 'netErrors': 0}:
        raise RecoveryError('artifact review shape/counters mismatch')
    return artifact, status, review


def _verify_preconditions(evidence):
    cond = _load_regular(evidence / 'recovery-preconditions.json')
    state_path = evidence / 'recovery-before-state.json'
    diag_path = evidence / 'recovery-before-diagnostic.json'
    state_expected = _hash_value((cond.get('state') or {}).get('sha256'), 'recovery state')
    diag_expected = _hash_value((cond.get('diagnostic') or {}).get('sha256'), 'recovery diagnostic')
    if state_path.is_symlink() or not state_path.is_file() \
            or _sha_file(state_path) != state_expected:
        raise RecoveryError('recovery state hash mismatch')
    if diag_path.is_symlink() or not diag_path.is_file() \
            or _sha_file(diag_path) != diag_expected:
        raise RecoveryError('recovery diagnostic hash mismatch')
    state = _load_regular(state_path)
    if state.get('status') != 'needs_review' or state.get('cycleId') != '2026-10-10:5/7' \
            or state.get('stage') != 5:
        raise RecoveryError('pre-state is not the expected needs_review stage')
    if state.get('leaseOwner') is not None or int(state.get('leaseExpiresAt') or 0) != 0:
        raise RecoveryError('foreign active lease present')
    if state.get('requestAttempts') != {'token': 1, 'search': 149}:
        raise RecoveryError('request attempts do not match source evidence')
    return state, _load_regular(diag_path)


def _brand_lookup(csv_path):
    out = {}
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 2 or not row[1].strip() or row[1].strip() == '型號':
                continue
            out.setdefault(norm_model(row[1]), row[0].strip())
    return out


def _validate_publication_time(value, source_completion):
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise RecoveryError('publication time must be exact YYYY-MM-DDTHH:MM:SSZ')
    try:
        actual = _dt.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=_dt.timezone.utc)
        source = _dt.datetime.strptime(source_completion, '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=_dt.timezone.utc)
    except ValueError as e:
        raise RecoveryError('publication time is not a valid UTC instant') from e
    if actual <= source:
        raise RecoveryError('publication time must be later than the source completion bound')
    return value


def _finalize_publication(packet, publication_time):
    publication_time = _validate_publication_time(
        publication_time, packet['publication']['sourceCompletionBound'])
    out = copy.deepcopy(packet)
    manifest = out['manifest']
    manifest['publishedAt'] = publication_time
    manifest['publicationStatus'] = 'finalized'
    out['bundleHash'] = sha256_json(manifest)
    target = out['pricesMetaTarget']
    target['appliedBundleHash'] = out['bundleHash']
    target['appliedSnapshotHash'] = sha256_json(out['newSnapshot'])
    target.pop('appliedAt', None)
    target['_recovery'] = dict(target.get('_recovery') or {})
    target['_recovery']['publicationStatus'] = 'finalized'
    target['_recovery']['publishedAt'] = publication_time
    target['_recovery']['publicationIntent'] = publication_time
    out['publication'] = {
        'status': 'finalized',
        'publishedAt': publication_time,
        'sourceCompletionBound': out['publication']['sourceCompletionBound'],
        'policy': 'explicit Root publication intent; retries reuse the same timestamp',
    }
    return out


def build_recovery(evidence, publication_time=None):
    evidence = Path(evidence)
    input_manifest = _verify_original_inputs(evidence)
    artifact_receipt, status, review = _verify_artifact(evidence, input_manifest)
    pre_state, diagnostic = _verify_preconditions(evidence)
    base = _load_regular(evidence / 'original-inputs/biggo_prices.json')
    new = review.get('snapshot')
    outcomes = review.get('outcomes')
    meta = _load_regular(evidence / 'original-inputs/prices_meta.json')
    pre_tracking = _load_regular(evidence / 'original-inputs/model_status.json')
    pre_wrapper = _load_regular(evidence / 'original-inputs/model_blacklist.json')
    pre_black = pre_wrapper.get('models') if isinstance(pre_wrapper, dict) else None
    if not isinstance(base, dict) or not isinstance(new, dict) or not isinstance(outcomes, list) \
            or not isinstance(pre_tracking, dict) or not isinstance(pre_black, dict):
        raise RecoveryError('original/review data shape invalid')
    if len(base) != 762 or len(new) != 763:
        raise RecoveryError('source snapshot record counts are not the expected shape')
    priced = [o for o in outcomes if isinstance(o, dict) and o.get('outcome') == 'priced']
    clean = [o for o in outcomes if isinstance(o, dict) and o.get('outcome') == 'clean_miss']
    if len(priced) != 101 or len(clean) != 7 or len(outcomes) != 108:
        raise RecoveryError('source artifact counters/outcomes are not the expected shape')
    extra = sorted(k for k in new if k not in base)
    if extra != [EXPECTED_REVIVED_MODEL]:
        raise RecoveryError(f'unexpected snapshot delta: {extra!r}')
    model = extra[0]
    revived_matches = [k for k in pre_black if isinstance(k, str) and k.endswith('|' + model)]
    if revived_matches != [EXPECTED_REVIVED_KEY] or EXPECTED_REVIVED_KEY not in pre_black:
        raise RecoveryError('revived canonical key is not uniquely proven')
    revived_key = EXPECTED_REVIVED_KEY
    price = new[model]
    if not isinstance(price, dict) or price.get('price') != '$4,499起' \
            or price.get('updated') != '2026-10-20':
        raise RecoveryError('revived price does not match source artifact')
    if any(o.get('model') == model for o in outcomes):
        raise RecoveryError('revived model already appears in original outcomes')
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
    time_evidence = {
        'schemaVersion': 1,
        'kind': 'biggo-recovery-source-time-v1',
        'sourceRunId': EXPECTED_RUN_ID,
        'sourceHead': input_manifest['sourceHead'],
        'artifactSha256': _hash_value(artifact_receipt.get('sha256'), 'artifact'),
        'sourceCompletionBound': status['at'],
        'logWindowStart': SOURCE_LOG_WINDOW_START,
        'sourceCycleId': stage['cycleId'],
        'sourceStage': stage['stage'],
        'newSnapshotHash': sha256_json(new),
        'stageResultHash': sha256_json(stage),
        'acquiredAt': None,
        'timeProvenance': 'unknown',
        'reason': 'exact-acquisition-time-not-recorded',
    }
    time_evidence_hash = sha256_json(time_evidence)
    manifest_doc = {
        'cycleId': stage['cycleId'], 'stage': 5, 'mode': 'price-batch',
        'writer': 'root-recovery', 'writerKind': 'self-host',
        'commit': input_manifest.get('sourceHead'),
        'publishedAt': None,
        'publicationStatus': 'pending',
        'recovery': {
            'schemaVersion': 1, 'kind': 'biggo-recovery-bundle-v1',
            'timeEvidenceHash': time_evidence_hash,
        },
        'recoveryTimeEvidence': time_evidence,
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
        'publicationStatus': 'pending',
        'sourceCompletionBound': status['at'],
        'originalGeneratedAt': status['at'],
        'originalAcquisitionEvidence': 'price.updated=2026-10-20; log window 07:13:40..07:19:53Z',
    }
    packet = {
        'schemaVersion': 1, 'kind': 'aircon-biggo-recovery-candidate',
        'sourceHead': input_manifest.get('sourceHead'),
        'sourceRunId': EXPECTED_RUN_ID,
        'artifactSha256': _hash_value(artifact_receipt.get('sha256'), 'artifact'),
        'originalInputs': {k: v for k, v in input_manifest['files'].items()},
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
        'publication': {
            'status': 'pending',
            'publishedAt': None,
            'sourceCompletionBound': status['at'],
            'policy': 'Root supplies an explicit fixed publication intent before manifest/bundle finalization',
        },
        'derived': {
            'revivedMapping': 'unique CARRIER|CHK18FBVX blacklist key + sole snapshot delta + exact artifact price',
            'postStateEffects': 'deterministic lifecycle planners using original inputs and candidate code',
            'stageGeneratedAt': 'artifact status at 2026-10-20T07:19:53Z; not recovery time',
            'acquisition': 'price.updated=2026-10-20 and log window; no exact acquisition time claimed',
        },
        'notProven': ['39 confirmed review names', 'exact acquisition sub-second time',
                      'coordinator commit/apply after recovery'],
        'providerRequests': 0, 'coordinatorWrites': 0,
    }
    if publication_time is not None:
        packet = _finalize_publication(packet, publication_time)
    return packet


def finalize_recovery(evidence, publication_time):
    """Pure Root-facing finalize entry; no write/apply is performed."""
    return build_recovery(evidence, publication_time=publication_time)


def _atomic_exclusive(path, data):
    if os.path.lexists(path):
        raise RecoveryError(f'output exists: {Path(path).name}')
    tmp = str(path) + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_packet(out, packet):
    _atomic_exclusive(out / 'recovery-packet.json', packet)
    for name, key in OUTPUT_FILES:
        _atomic_exclusive(out / name, packet[key])


def _verify_output(out, evidence):
    if out.is_symlink() or not out.is_dir():
        raise RecoveryError('output directory missing/symlink')
    packet_path = out / 'recovery-packet.json'
    if packet_path.is_symlink() or not packet_path.is_file():
        raise RecoveryError('recovery packet missing/symlink')
    packet = _load(packet_path)
    pub = packet.get('publication')
    if not isinstance(pub, dict):
        raise RecoveryError('publication metadata missing')
    if pub.get('status') == 'finalized':
        publication_time = pub.get('publishedAt')
    elif pub.get('status') == 'pending':
        if pub.get('publishedAt') is not None or packet.get('manifest', {}).get('publishedAt') is not None:
            raise RecoveryError('pending publication has a publication timestamp')
        publication_time = None
    else:
        raise RecoveryError('publication status invalid')
    rebuilt = build_recovery(evidence, publication_time=publication_time)
    if packet != rebuilt:
        raise RecoveryError('recovery packet does not exactly reproduce from trusted evidence')
    for name, key in OUTPUT_FILES:
        p = out / name
        if p.is_symlink() or not p.is_file():
            raise RecoveryError(f'emitted artifact missing/symlink: {name}')
        if _load(p) != packet[key]:
            raise RecoveryError(f'emitted artifact differs from packet: {name}')
    return packet


def main(argv=None):
    ap = argparse.ArgumentParser(description='prepare read-only BigGo recovery packet')
    ap.add_argument('--evidence', required=True)
    ap.add_argument('--out-dir', required=True)
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--publication-time',
                    help='explicit Root publication intent (YYYY-MM-DDTHH:MM:SSZ)')
    args = ap.parse_args(argv)
    out = Path(args.out_dir)
    if args.verify:
        try:
            _verify_output(out, Path(args.evidence))
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
        packet = build_recovery(Path(args.evidence), publication_time=args.publication_time)
    except Exception as e:  # noqa: BLE001
        print(f'❌ recovery preparation failed: {type(e).__name__}: {str(e)[:160]}',
              file=sys.stderr)
        return 2
    try:
        out.mkdir(parents=True)
        _write_packet(out, packet)
    except (OSError, RecoveryError) as e:
        print(f'❌ recovery packet write failed: {type(e).__name__}', file=sys.stderr)
        return 2
    print(json.dumps({'ok': True, 'packet': str(out / 'recovery-packet.json'),
                      'bundleHash': packet['bundleHash'],
                      'stageResultHash': sha256_json(packet['stageResult']),
                      'publicationStatus': packet['publication']['status'],
                      'providerRequests': 0}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
