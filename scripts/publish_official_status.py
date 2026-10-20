#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `run_official_batch.py` machine receipt 投影為公開 pending 狀態檔。

只寫入無絕對路徑／無 secrets 嘅欄位；decision 必須係已知值。寫入原子。
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from biggo_canonical import canonical_json_bytes, sha256_id

DECISIONS = {
    'advanced',
    'queue-kept-pending-coverage',
    'queue-kept-fail-closed',
}
EXPECTED_OUTPUTS = {
    1: ('official_specs.json', 'shew_official.json', 'rasonic_official.json'),
    2: ('carrier_official.json', 'general_official.json', 'specs.json'),
}
SHA_RE = __import__('re').compile(r'^sha256:[0-9a-f]{64}$')
ALL_OUTPUTS = frozenset(set().union(*EXPECTED_OUTPUTS.values()) | {'mecan_official.json', 'gilman_official.json'})
SOURCE_OUTPUTS = {'fetch_mecan.py': 'mecan_official.json',
                  'fetch_gilman.py': 'gilman_official.json',
                  'fetch_official.py': 'official_specs.json',
                  'fetch_shew.py': 'shew_official.json',
                  'fetch_rasonic.py': 'rasonic_official.json',
                  'fetch_carrier.py': 'carrier_official.json',
                  'fetch_general.py': 'general_official.json',
                  'fetch_specs.py': 'specs.json'}


def _sha(value):
    if not isinstance(value, str):
        return None
    v = value.strip()
    if SHA_RE.match(v):
        return v
    if len(v) == 64 and all(c in '0123456789abcdef' for c in v):
        return 'sha256:' + v
    return None


def _valid_time(value):
    if not isinstance(value, str) or not value.endswith('Z'):
        return False
    try:
        dt = _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() == _dt.timedelta(0)



def _selected_outputs_from_routes(receipt):
    # Recompute the route from actual targetModels; never trust a forged
    # sourceRouting dictionary in a receipt.
    targets = receipt.get('targetModels')
    if not isinstance(targets, list) or not targets \
            or len(targets) != len(set(targets)) \
            or any(not isinstance(x, str) or not x for x in targets):
        return None
    try:
        from run_official_batch import route_for_model
    except Exception:
        return None
    expected = set()
    recomputed = {}
    for model in targets:
        route = route_for_model(model)
        recomputed[model] = route
        if route.get('supported') is True and route.get('source') in SOURCE_OUTPUTS:
            expected.add(SOURCE_OUTPUTS[route['source']])
    reported = receipt.get('sourceRouting')
    if reported not in (None, {}) and reported != recomputed:
        return None
    return expected


def _manifest_errors(receipt):
    errors = []
    stage = receipt.get('stage')
    if isinstance(stage, bool) or stage not in EXPECTED_OUTPUTS:
        return ['stage 唔支援／缺失']
    manifest = receipt.get('outputManifest')
    if not isinstance(manifest, dict) or manifest.get('schemaVersion') != 1:
        return ['outputManifest schemaVersion 必須係 1']
    if manifest.get('stage') != stage:
        errors.append('outputManifest.stage 同 receipt.stage 唔一致')
    files = manifest.get('files')
    if receipt.get('targetScoped') is True:
        selected = receipt.get('selectedOutputs')
        if not isinstance(selected, list) or not selected \
                or len(selected) != len(set(selected)) \
                or any(x not in ALL_OUTPUTS for x in selected):
            return errors + ['target-scoped selectedOutputs 唔合法']
        route_expected = _selected_outputs_from_routes(receipt)
        if route_expected is None or set(selected) != route_expected:
            return errors + ['target-scoped selectedOutputs 同可信 route/profile 唔一致']
        expected = set(selected)
    else:
        expected = set(EXPECTED_OUTPUTS[stage])
    if not isinstance(files, dict) or set(files) != expected:
        return errors + ['outputManifest.files 必須恰好包含指定 outputs']
    for name, meta in files.items():
        if not isinstance(meta, dict):
            errors.append(f'outputManifest.files[{name}] 唔係 object')
            continue
        if not _sha(meta.get('sha256')):
            errors.append(f'outputManifest.files[{name}].sha256 唔係 full sha256')
        if isinstance(meta.get('size'), bool) or not isinstance(meta.get('size'), int)                 or meta.get('size') <= 0:
            errors.append(f'outputManifest.files[{name}].size 唔合格')
    body = {'schemaVersion': 1, 'stage': stage, 'files': files}
    want = sha256_id(canonical_json_bytes(body))
    if manifest.get('manifestHash') != want:
        errors.append('outputManifest.manifestHash 唔一致')
    outputs = receipt.get('outputs')
    if not isinstance(outputs, list) or not outputs:
        return errors + ['receipt.outputs 必須係非空 array']
    got = [o.get('output') for o in outputs if isinstance(o, dict)]
    if set(got) != expected or len(got) != len(expected):
        errors.append('receipt.outputs 名稱／數量同 stage 指定 outputs 唔一致')
    for o in outputs:
        if not isinstance(o, dict):
            errors.append('receipt.outputs entry 必須係 object')
            continue
        name = o.get('output')
        if not o.get('ok') is True or not o.get('promoted') is True:
            errors.append(f'{name} 未經成功 promote')
        h = _sha(o.get('hash'))
        if not h:
            errors.append(f'{name} output hash 唔係 full sha256')
        elif h != _sha((files.get(name) or {}).get('sha256')):
            errors.append(f'{name} output hash 同 manifest 唔一致')
    if not isinstance(receipt.get('staging'), dict) or receipt['staging'].get('promoted') is not True:
        errors.append('receipt.staging.promoted 唔係 true')
    if receipt.get('advanced') is not True:
        errors.append('receipt.advanced 唔係 true')
    if receipt.get('failureClass') != 'none' or receipt.get('safeToContinue') is not True:
        errors.append('receipt failureClass／safeToContinue 唔係成功狀態')
    if receipt.get('failures'):
        errors.append('receipt 有 failures')
    if not _valid_time(receipt.get('finishedAt')):
        errors.append('receipt.finishedAt 唔係可信 UTC 時間')
    return errors


def main(argv=None):
    ap = argparse.ArgumentParser(description='投影官網核實 pending 狀態')
    ap.add_argument('--receipt', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    try:
        with open(args.receipt, encoding='utf-8') as f:
            receipt = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f'❌ 讀唔到 official receipt：{e}', file=sys.stderr)
        return 1
    if not isinstance(receipt, dict):
        print('❌ official receipt 必須係 object', file=sys.stderr)
        return 1
    decision = receipt.get('decision')
    if decision not in DECISIONS:
        print(f'❌ receipt decision 未知／缺失：{decision!r}', file=sys.stderr)
        return 1
    missing = receipt.get('missingModels')
    if not isinstance(missing, list):
        missing = []
    canon = receipt.get('missingCanonicalModels')
    if not isinstance(canon, list):
        canon = []
    script_pending = receipt.get('coveragePendingFromScripts')
    if not isinstance(script_pending, list):
        script_pending = []
    pending_reasons = receipt.get('coveragePendingReasons')
    if not isinstance(pending_reasons, dict):
        pending_reasons = {}
    previous = {}
    try:
        with open(args.out, encoding='utf-8') as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = {}
    if decision == 'advanced':
        manifest_errors = _manifest_errors(receipt)
        if manifest_errors:
            print('❌ advanced receipt 冇可信 output manifest，拒絕投影：'
                  + '；'.join(manifest_errors), file=sys.stderr)
            return 1
        snapshot_hash = receipt['outputManifest']['manifestHash']
        if receipt.get('targetScoped') is True:
            # A cache replay must retain the real official acquisition time; the
            # batch finish time is not an acquisition witness.
            source_time = receipt.get('sourceAcquiredAt')
            last_success = source_time if _valid_time(source_time) else None
        else:
            last_success = receipt['finishedAt']
        snapshot_id = 'official-' + str(receipt['stage']) + '-' + snapshot_hash[-12:]
        output_manifest = receipt['outputManifest']
    else:
        # Preserve only a previously verified complete snapshot; the current
        # partial/staged outputs are NOT advertised as a new snapshot.
        previous_hash = _sha(previous.get('snapshotHash'))
        previous_time = previous.get('lastSuccessAt')
        snapshot_hash = previous_hash if previous_hash and _valid_time(previous_time) else None
        last_success = previous_time if snapshot_hash else None
        snapshot_id = previous.get('snapshotId') if snapshot_hash else None
        output_manifest = previous.get('outputManifest') if snapshot_hash else None
    status = {
        'schemaVersion': 1,
        'decision': decision,
        'lastSuccessAt': last_success,
        'snapshotId': snapshot_id,
        'snapshotHash': snapshot_hash,
        'outputManifest': output_manifest,
        'stage': receipt.get('stage'),
        # pendingCoverage 係 OR：queue model 未有覆蓋 或 script 確認 coverage pending
        'pendingCoverage': decision == 'queue-kept-pending-coverage',
        'missingQueueCoverage': receipt.get('missingQueueCoverage')
        if isinstance(receipt.get('missingQueueCoverage'), bool)
        else bool(missing),
        'scriptCoveragePending': [m for m in script_pending if isinstance(m, str)],
        'missingModels': [m for m in missing if isinstance(m, str)],
        'missingCanonicalModels': [m for m in canon if isinstance(m, str)],
        'coveragePendingReasons': {
            str(k): str(v) for k, v in pending_reasons.items()
            if isinstance(v, str)},
        'generatedAt': receipt.get('finishedAt') or receipt.get('startedAt'),
        'source': 'run_official_batch.py',
    }
    if decision == 'advanced' and receipt.get('targetScoped') is True:
        status['selectedOutputs'] = list(receipt.get('selectedOutputs') or [])
        status['targetModels'] = list(receipt.get('targetModels') or [])
        status['sourceRouting'] = dict(receipt.get('sourceRouting') or {})
    out = os.path.abspath(args.out)
    tmp = out + '.tmp'
    try:
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        print(f'❌ 寫入 official status 失敗：{e}', file=sys.stderr)
        return 1
    print(f'✅ official pending status：decision={decision} pending={status["pendingCoverage"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
