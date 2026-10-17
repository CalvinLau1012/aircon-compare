#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官網核實批次執行器（stage 1／2）：有可審計、可綁定嘅完成證據先推進 queue。

Fail-closed 不變式：
- 每個腳本必須有 machine receipt（schemaVersion／script／counts／
  succeededModels／failedModels／alreadyVerified／covers）；
- `failed==0` 才可能成功；counts 同 lists 數量一致；covers == canonical union
  (succeededModels, alreadyVerified)；
- 所有 listed models 必須喺該腳本輸出有 `_entry_has_evidence`；
- queue bytes 執行期間不得改變；所有 queue model 必須被 covers 覆蓋；
- 推進前先原子寫 `decision=ready-to-advance` receipt（寫唔到就唔會 advance）；
- advance 後更新 `advanced`；若更新失敗會如實報「queue 已推進但 final receipt
  更新失敗」，唔會聲稱 queue 保留。

用法：
  python scripts/run_official_batch.py --stage 1|2 [--timeout 1500] [--receipt <path>]
退出碼：0 = 已驗證並推進；1 = 未全部完成（queue 保留）或 receipt 寫入失敗。
"""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from queue_utils import load_queue, QueueError  # noqa: E402
from crawl_utils import norm_model, COVERAGE_PENDING_REASONS  # noqa: E402
from biggo_canonical import canonical_json_bytes, sha256_id  # noqa: E402

STAGES = {
    1: {
        'scripts': ['fetch_official.py', 'fetch_shew.py', 'fetch_rasonic.py'],
        'outputs': ['official_specs.json', 'shew_official.json', 'rasonic_official.json'],
    },
    2: {
        'scripts': ['fetch_carrier.py', 'fetch_general.py', 'fetch_specs.py'],
        'outputs': ['carrier_official.json', 'general_official.json', 'specs.json'],
    },
}
RECEIPT_MARKER = 'AIRCON_FETCH_RECEIPT '

# Machine-readable routing for queue entries that have no matching stage/source
# target.  This is diagnostic only: it never removes a queue entry and never
# turns missing coverage into a cover.  Keep the evidence tied to the actual
# source scripts rather than asserting that a model does not exist.
ROUTE_RULES = (
    ('MITSUBISHIHEAVY', r'^(?:WRK07MIZH1|WRK09MIZH1|WRK18MIZH1)$',
     None, None, 'brand-source-not-configured'),
    ('MECAN', r'^(?:MC07WVRQW|MC09WVRQW|MC07VXH|MC09VXH|MC12VXH|MC18VXH|MC24VXH)$',
     None, None, 'brand-source-not-configured'),
    ('GREE', r'^(?:GEH63ZE/GEH63ZC|GWT07ES|GWT09ES|GWT12ES)$',
     None, None, 'brand-source-not-configured'),
    ('COMFEE', r'^CWV-24CRFN9-AD5$', 1, 'fetch_official.py',
     'target-not-in-configured-comfee-list'),
)


def route_for_model(model):
    """Return the configured official-source route, or an explicit gap reason."""
    import re
    text = str(model)
    for brand, pattern, stage, source, reason in ROUTE_RULES:
        if re.match(pattern, text):
            return {'brand': brand, 'stage': stage, 'source': source,
                    'reason': reason,
                    'supported': bool(source and reason is None)}
    return {'brand': None, 'stage': None, 'source': None,
            'reason': 'source-route-not-configured', 'supported': False}


def queue_stage():
    """讀實際 queue stage（共用 queue_utils 契約；錯誤即 raise）。"""
    return load_queue(os.path.join(BASE, 'update_queue.json'))['stage']


def _entry_has_evidence(entry):
    if not isinstance(entry, dict) or not entry:
        return False
    for k, v in entry.items():
        if k.endswith('_error') or k in ('url', 'model'):
            continue
        if isinstance(v, str) and v.strip():
            return True
        if isinstance(v, bool) and v:
            return True
        if isinstance(v, (list, dict)) and v:
            return True
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            return True
    return False


def validate_output(path):
    if not os.path.exists(path):
        return False, '輸出檔唔存在'
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return False, f'輸出檔唔係有效 JSON：{e}'
    if not isinstance(data, (dict, list)):
        return False, f'輸出必須係 JSON object／array（got {type(data).__name__}）'
    if not data:
        return False, '輸出檔係空（全部目標失敗冇數據）'
    entries = list(data.values()) if isinstance(data, dict) else data
    bad = [i for i, e in enumerate(entries) if not _entry_has_evidence(e)]
    if bad:
        return False, f'有 {len(bad)} 個 entry 冇實質 evidence（索引 {bad[:5]}）'
    return True, ''


def output_models(path):
    """回傳 {canonical model: has_evidence}。"""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return {norm_model(k): _entry_has_evidence(v) for k, v in data.items()}
    out = {}
    for v in data:
        if isinstance(v, dict):
            out[norm_model(v.get('model', ''))] = _entry_has_evidence(v)
    return out


def _sha256_file(path):
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _sha256_file_direct(path):
    """Hash bytes without the injectable receipt helper.

    Receipts store this value; verification deliberately uses `_sha256_file`
    so a late mutation (or an injected race in the receipt path) is detected.
    """
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _normalize_sha256(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value.startswith('sha256:') and len(value) == 71:
        return value if all(c in '0123456789abcdef' for c in value[7:]) else None
    if len(value) == 64 and all(c in '0123456789abcdef' for c in value):
        return 'sha256:' + value
    return None


def _output_manifest(root, stage, outputs):
    """Versioned manifest binding exact output names and complete bytes hashes."""
    files = {}
    for rel in outputs:
        path = os.path.join(root, rel)
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(f'output 唔係 regular file：{rel}')
        files[rel] = {'sha256': 'sha256:' + _sha256_file_direct(path),
                      'size': os.path.getsize(path)}
    body = {'schemaVersion': 1, 'stage': stage, 'files': files}
    return {**body, 'manifestHash': sha256_id(canonical_json_bytes(body))}


def _atomic_write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


STAGING_IGNORE = shutil.ignore_patterns(
    '.git', '__pycache__', '.pytest_cache', '.venv', 'node_modules', 'reports',
    '.mypy_cache', '.ruff_cache', '*.pyc')


def _make_staging_copy(root):
    """Create an isolated candidate copy; scripts write only inside it."""
    tmp = tempfile.mkdtemp(prefix='aircon-official-staging-')
    dest = os.path.join(tmp, 'repo')
    shutil.copytree(root, dest, ignore=STAGING_IGNORE, symlinks=True)
    return tmp, dest


def _cleanup_staging(tmp):
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_file_bytes(path):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return f.read()


def _atomic_write_bytes(path, data, mode=0o644):
    directory = os.path.dirname(os.path.abspath(path)) or '.'
    tmp = path + f'.promote.{os.getpid()}.{time.time_ns()}'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(directory, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _promote_outputs(repo, staging_repo, outputs):
    """Promote verified staged outputs atomically; rollback on any failure.

    Returns (promoted, before_after) where before_after maps rel -> (before, after).
    The queue is never promoted from staging.
    """
    before = {rel: _read_file_bytes(os.path.join(repo, rel)) for rel in outputs}
    promoted = []
    try:
        for rel in outputs:
            src = os.path.join(staging_repo, rel)
            if not os.path.isfile(src):
                raise OSError(f'staged output 唔存在：{rel}')
            with open(src, 'rb') as f:
                data = f.read()
            dst = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
            mode = 0o644
            try:
                mode = os.stat(src).st_mode & 0o777
            except OSError:
                pass
            _atomic_write_bytes(dst, data, mode or 0o644)
            promoted.append(rel)
    except Exception:
        # Restore every path touched so a promotion failure leaves the old set.
        rollback_errors = []
        for rel in promoted:
            dst = os.path.join(repo, rel)
            try:
                if before[rel] is None:
                    if os.path.exists(dst):
                        os.unlink(dst)
                else:
                    _atomic_write_bytes(dst, before[rel])
            except OSError as e:
                rollback_errors.append(f'{rel}:{e}')
        if rollback_errors:
            raise OSError('promotion 失敗而且 rollback 失敗：' + '; '.join(rollback_errors))
        raise
    after = {rel: _read_file_bytes(os.path.join(repo, rel)) for rel in outputs}
    return promoted, before, after


def parse_receipt(stdout):
    marker = None
    for line in stdout.splitlines():
        if line.startswith(RECEIPT_MARKER):
            marker = line[len(RECEIPT_MARKER):]
    if marker is None:
        return None
    try:
        return json.loads(marker)
    except ValueError:
        return None


def _canon_list_ok(values, label, errors):
    """非空字串 list、canonical unique；回傳 canonical set。"""
    if not isinstance(values, list):
        errors.append(f'{label} 必須係 array')
        return set()
    out = set()
    for v in values:
        if not isinstance(v, str) or not v.strip():
            errors.append(f'{label} 有非字串／空白 entry：{v!r}')
            return set()
        key = norm_model(v)
        if key in out:
            errors.append(f'{label} 有 canonical 重複：{v!r}')
            return set()
        out.add(key)
    return out


def _parse_failure_reasons(marker, failed_norm, errors):
    """驗證 failureReasons；每個 failed target 都必須有 reason code。

    reason code 係分類 coverage pending（allowlist）同硬失敗嘅唯一機器依據；
    缺 reasons 而又有 failed → 一律當硬失敗（uncertain）。
    """
    fr = marker.get('failureReasons')
    if fr is None:
        if failed_norm:
            errors.append('receipt 有 failed 但冇 failureReasons，無法區分 coverage pending 同硬失敗')
        return {}
    if not isinstance(fr, dict):
        errors.append('failureReasons 必須係 object')
        return {}
    reasons = {}
    for k, v in fr.items():
        if not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip():
            errors.append(f'failureReasons 有無效 entry：{k!r}:{v!r}')
            return {}
        reasons[norm_model(k)] = v.strip()
    if set(reasons) != failed_norm:
        errors.append('failureReasons 同 failedModels 唔一致')
    return reasons


def _parse_coverage_pending(marker, failed_norm, errors):
    """驗證 coveragePendingModels／Reasons；回傳 (pending_norm, reasons)。"""
    models = marker.get('coveragePendingModels') or []
    reasons = marker.get('coveragePendingReasons') or {}
    if not isinstance(models, list):
        errors.append('coveragePendingModels 必須係 array')
        return set(), {}
    if not isinstance(reasons, dict):
        errors.append('coveragePendingReasons 必須係 object')
        return set(), {}
    pending = set()
    for m in models:
        if not isinstance(m, str) or not m.strip():
            errors.append(f'coveragePendingModels 有無效 entry：{m!r}')
            return set(), {}
        key = norm_model(m)
        if key in pending:
            errors.append(f'coveragePendingModels canonical 重覆：{m!r}')
            return set(), {}
        pending.add(key)
    norm_reasons = {}
    for k, v in reasons.items():
        if not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip():
            errors.append(f'coveragePendingReasons 有無效 entry：{k!r}:{v!r}')
            return set(), {}
        norm_reasons[norm_model(k)] = v.strip()
    if pending != set(norm_reasons):
        errors.append('coveragePendingModels 同 coveragePendingReasons 唔一致')
    if not pending <= failed_norm:
        errors.append('coveragePendingModels 必須係 failedModels subset')
    unknown = {v for v in norm_reasons.values() if v not in COVERAGE_PENDING_REASONS}
    if unknown:
        errors.append(f'coveragePendingReasons 有未知 reason：{sorted(unknown)}')
    return pending, norm_reasons


def coverage_pending_from_marker(marker):
    """已通過 validate_marker 嘅 receipt → canonical coverage pending model set。"""
    out = set()
    if not isinstance(marker, dict):
        return out
    for m in marker.get('coveragePendingModels') or []:
        if isinstance(m, str) and m.strip():
            out.add(norm_model(m))
    return out


def validate_marker(marker, expected_script, models_with_evidence):
    """嚴格驗證 mark receipt 同 output evidence 綁定；回傳 errors。"""
    errors = []
    if not isinstance(marker, dict):
        return ['machine receipt 唔係 object']
    if marker.get('schemaVersion') != 1 or isinstance(marker.get('schemaVersion'), bool):
        errors.append('receipt schemaVersion 必須係 1')
    if marker.get('script') != expected_script:
        errors.append(f"receipt script 唔符：{marker.get('script')!r} != {expected_script!r}")
    counts = {}
    for key in ('attempted', 'succeeded', 'failed'):
        v = marker.get(key)
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            errors.append(f'receipt {key} 必須係非負整數（got {v!r}）')
        counts[key] = v if isinstance(v, int) and not isinstance(v, bool) else None
    if all(v is not None for v in counts.values()):
        if counts['attempted'] != counts['succeeded'] + counts['failed']:
            errors.append(f"receipt counts 唔一致：{counts['attempted']} != "
                          f"{counts['succeeded']}+{counts['failed']}")
    succeeded = _canon_list_ok(marker.get('succeededModels'), 'succeededModels', errors)
    failed = _canon_list_ok(marker.get('failedModels'), 'failedModels', errors)
    already = _canon_list_ok(marker.get('alreadyVerified'), 'alreadyVerified', errors)
    covers = _canon_list_ok(marker.get('covers'), 'covers', errors)
    if counts['succeeded'] is not None and len(succeeded) != counts['succeeded']:
        errors.append(f"len(succeededModels) {len(succeeded)} != succeeded {counts['succeeded']}")
    if counts['failed'] is not None and len(failed) != counts['failed']:
        errors.append(f"len(failedModels) {len(failed)} != failed {counts['failed']}")
    skipped = marker.get('skipped')
    if skipped is not None:
        if not isinstance(skipped, int) or isinstance(skipped, bool) or skipped < 0:
            errors.append(f'receipt skipped 必須係非負整數（got {skipped!r}）')
        elif skipped != len(already):
            errors.append(f'len(alreadyVerified) {len(already)} != skipped {skipped}')
    if covers != (succeeded | already):
        errors.append('covers 唔等於 canonical union(succeededModels, alreadyVerified)')
    if succeeded & failed:
        errors.append('同一個 model 唔可以同時 succeeded 同 failed')
    reasons = _parse_failure_reasons(marker, failed, errors)
    pending, pending_reasons = _parse_coverage_pending(marker, failed, errors)
    if pending & (succeeded | already):
        errors.append('coveragePendingModels 唔可以同時係 succeeded／alreadyVerified')
    if reasons and pending_reasons:
        mismatch = {k for k, v in pending_reasons.items() if reasons.get(k) != v}
        if mismatch:
            errors.append(f'coveragePendingReasons 同 failureReasons 唔一致：'
                          f'{sorted(mismatch)[:5]}')
    if reasons:
        hard = failed - pending
        if hard:
            errors.append(f"receipt failed={counts.get('failed')} > 0（未確認 coverage pending："
                          f"{sorted(hard)[:5]}）")
    elif counts['failed']:
        errors.append(f"receipt failed={counts.get('failed')} > 0（冇 coverage pending 證明）")
    # 所有 listed models 必須喺輸出有 evidence（phantom 阻斷）
    for key in (succeeded | already):
        if not models_with_evidence.get(key):
            errors.append(f'receipt 列出但輸出冇有效 evidence：{key}')
    return errors


def verify_receipt_outputs(receipt):
    errors = []
    for s in receipt.get('scripts', []):
        out = s.get('output')
        if not out:
            errors.append(f"script {s.get('script')} receipt 冇 output 記錄")
            continue
        want = s.get('outputHashAfter')
        got = _sha256_file(os.path.join(BASE, out))
        if want != got:
            errors.append(f'{out} output hash 唔一致（receipt={want} actual={got}）')
    return errors


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='官網核實批次（有可審計證據先推進隊列）')
    ap.add_argument('--stage', type=int, choices=[1, 2], default=None)
    ap.add_argument('--from-queue', action='store_true')
    ap.add_argument('--timeout', type=int, default=1500)
    ap.add_argument('--receipt', default=None)
    ap.add_argument('--degraded-exit-zero', action='store_true',
                    help='safe source failure (old outputs/queue intact) returns 0 with degraded receipt; '
                         'integrity/transaction failure still returns 1')
    args = ap.parse_args(argv)

    queue_path = os.path.join(BASE, 'update_queue.json')
    try:
        actual_stage = queue_stage()
    except (QueueError, RuntimeError) as e:
        print(f'❌ {e}', file=sys.stderr)
        return 1
    stage = args.stage if args.stage is not None else (actual_stage if args.from_queue else None)
    if stage not in STAGES:
        print(f'❌ 冇有效 stage（--stage 1/2 或 --from-queue；queue stage={actual_stage}）', file=sys.stderr)
        return 1
    if args.stage is not None and actual_stage != args.stage:
        print(f'❌ --stage {args.stage} 同實際 queue stage {actual_stage} 唔一致，拒絕推進',
              file=sys.stderr)
        return 1

    try:
        q = load_queue(queue_path)
    except (QueueError, RuntimeError) as e:
        print(f'❌ {e}', file=sys.stderr)
        return 1
    target_models = list(q['models'])
    queue_hash_before = _sha256_file(queue_path)
    receipt_path = args.receipt or os.environ.get(
        'AIRCON_OFFICIAL_RECEIPT',
        os.path.join(tempfile.gettempdir(), 'aircon-official-receipt.json'))

    plan = STAGES[stage]
    failures = []
    integrity_failure = False
    receipt = {
        'schemaVersion': 1,
        'stage': stage,
        'queuePath': 'update_queue.json',
        'queueHashBefore': queue_hash_before,
        'targetModels': target_models,
        'startedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'scripts': [],
        'outputs': [],
        'failures': failures,
        'advanced': False,
        'decision': 'pending',
    }

    staging_tmp = None
    staging_repo = None
    try:
        staging_tmp, staging_repo = _make_staging_copy(BASE)
        staging_queue = os.path.join(staging_repo, 'update_queue.json')
        staging_queue_before = _sha256_file(staging_queue)
        for idx, script in enumerate(plan['scripts']):
            path = os.path.join(staging_repo, script)
            output_rel = plan['outputs'][idx]
            original_output = os.path.join(BASE, output_rel)
            staged_output = os.path.join(staging_repo, output_rel)
            print(f'▶️ [{stage}] {script}（isolated staging）', flush=True)
            t0 = time.time()
            entry = {'script': script, 'argv': ['python', script], 'output': output_rel,
                     'outputHashBefore': _sha256_file(original_output)}
            try:
                child_env = dict(os.environ)
                child_env['AIRCON_OFFICIAL_STAGING_REPO'] = staging_repo
                proc = subprocess.run([sys.executable, path], cwd=staging_repo,
                                      timeout=args.timeout, capture_output=True,
                                      text=True, encoding='utf-8', errors='replace',
                                      env=child_env)
                if proc.stdout:
                    print(proc.stdout, end='')
                if proc.stderr:
                    print(proc.stderr, end='', file=sys.stderr)
                rc = proc.returncode
                marker = parse_receipt(proc.stdout or '')
            except subprocess.TimeoutExpired:
                rc = -1
                marker = None
                failures.append(f'{script} 逾時（>{args.timeout}s）')
            entry['returncode'] = rc
            entry['seconds'] = round(time.time() - t0, 1)
            entry['receipt'] = marker
            entry['stagedOutputHash'] = _sha256_file_direct(staged_output)
            entry['outputHashAfter'] = entry['stagedOutputHash']
            if rc != 0:
                failures.append(f'{script} exit {rc}')
            if marker is None:
                failures.append(f'{script} 冇 machine receipt（未證明本輪完成證據）')
                entry['markerValid'] = False
            else:
                models = output_models(staged_output)
                marker_errors = validate_marker(marker, script, models)
                failures.extend(f'{script}: {e}' for e in marker_errors)
                entry['markerValid'] = not marker_errors
            receipt['scripts'].append(entry)

        staging_queue_after = _sha256_file(staging_queue)
        if staging_queue_after != staging_queue_before:
            failures.append('queue 喺 staging 批次執行期間被改動；拒絕推進')
            integrity_failure = True

        covers_norm = set()
        coverage_pending_norm = set()
        coverage_pending_reasons = {}
        for entry in receipt['scripts']:
            marker = entry.get('receipt')
            # 只有通過 validate_marker 嘅 receipt 才可貢獻 covers／coverage pending；
            # 任何 invalid marker 只可以係 hard failure（見上面 failures）。
            if isinstance(marker, dict) and entry.get('returncode') == 0 \
                    and entry.get('markerValid') is True:
                covers_norm.update(norm_model(m) for m in (marker.get('covers') or []))
                pending = coverage_pending_from_marker(marker)
                coverage_pending_norm.update(pending)
                for m, reason in (marker.get('coveragePendingReasons') or {}).items():
                    coverage_pending_reasons[norm_model(m)] = reason

        staged_outputs = []
        for rel in plan['outputs']:
            p = os.path.join(staging_repo, rel)
            ok, reason = validate_output(p)
            staged_outputs.append({'output': rel, 'ok': ok, 'reason': reason,
                                   'stagedHash': _sha256_file(p),
                                   'originalHash': _sha256_file(os.path.join(BASE, rel))})
            if not ok:
                failures.append(f'{rel}：{reason}')

        missing_coverage = [m for m in target_models if norm_model(m) not in covers_norm]
        script_pending_norm = set(coverage_pending_norm)
        coverage_pending = bool(missing_coverage or script_pending_norm)
        # Coverage pending cannot safely advertise a new official snapshot:
        # preserve the previous complete output set, even if staged outputs are
        # individually valid.  This keeps snapshotHash and actual bytes aligned.
        promoted = False
        if not failures and not coverage_pending:
            try:
                _promoted, before, after = _promote_outputs(
                    BASE, staging_repo, plan['outputs'])
                promoted = True
                for entry in staged_outputs:
                    rel = entry['output']
                    entry['hash'] = _sha256_file(os.path.join(BASE, rel))
                    entry['promoted'] = True
                for entry in receipt['scripts']:
                    rel = entry['output']
                    entry['outputHashAfter'] = _sha256_file_direct(os.path.join(BASE, rel))
            except OSError as e:
                failures.append(f'output promotion 失敗（已 rollback）：{e}')
                integrity_failure = True
        if not promoted:
            for entry in staged_outputs:
                entry['hash'] = entry.get('originalHash')
                entry['promoted'] = False
                # Verification below must compare the actual current bytes.
                for se in receipt['scripts']:
                    if se.get('output') == entry['output']:
                        se['outputHashAfter'] = entry['hash']
        receipt['outputs'] = staged_outputs
        receipt['staging'] = {'used': True, 'promoted': promoted}
        if promoted:
            try:
                manifest = _output_manifest(BASE, stage, plan['outputs'])
            except (OSError, ValueError) as e:
                failures.append(f'output manifest 失敗：{e}')
                integrity_failure = True
            else:
                receipt['outputManifest'] = manifest
                # Re-read every file and prove the manifest identity after promotion.
                recheck = _output_manifest(BASE, stage, plan['outputs'])
                if recheck != manifest:
                    failures.append('output manifest 寫入後 bytes 不一致')
                    integrity_failure = True

        queue_hash_after = _sha256_file(queue_path)
        receipt['queueHashAfter'] = queue_hash_after
        if queue_hash_after != queue_hash_before:
            failures.append('queue 喺批次執行期間被改動；拒絕推進')
            integrity_failure = True
        # 兩個獨立事實：queue model 冇任何覆蓋；script 確認嘅 target coverage pending。
        # 任何一項非空都必須保留 queue（queue-kept-pending-coverage），包括 script pending
        # target 已經被另一個 script／舊快照覆蓋嘅情況（D1-B／D27）。
        receipt['missingQueueCoverage'] = bool(missing_coverage)
        receipt['scriptCoveragePending'] = bool(script_pending_norm)
        receipt['coveragePending'] = bool(missing_coverage or script_pending_norm)
        receipt['missingModels'] = list(missing_coverage)
        receipt['missingCanonicalModels'] = sorted({norm_model(m) for m in missing_coverage})
        receipt['coveragePendingFromScripts'] = sorted(script_pending_norm)
        missing_reasons = {}
        route_map = {}
        for m in missing_coverage:
            route = route_for_model(m)
            route_map[m] = route
            # A script's explicit failure/coverage reason wins; routing only
            # explains otherwise-unexplained queue gaps.
            coverage_pending_reasons.setdefault(
                m, route['reason'] or 'stage-source-did-not-cover-target')
        receipt['sourceRouting'] = route_map
        receipt['coveragePendingReasons'] = dict(sorted(coverage_pending_reasons.items()))
        if not failures:
            verify_errors = verify_receipt_outputs(receipt)
            if verify_errors:
                failures.extend(verify_errors)
                integrity_failure = True
    finally:
        _cleanup_staging(staging_tmp)

    receipt['failureClass'] = 'integrity' if integrity_failure else (
        'source' if failures else 'none')
    receipt['safeToContinue'] = not integrity_failure

    def _finalize(decision, advanced):
        receipt['decision'] = decision
        receipt['advanced'] = advanced
        receipt['failures'] = failures
        receipt['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        try:
            _atomic_write_json(receipt_path, receipt)
            return None
        except OSError as e:
            return e

    if failures:
        err = _finalize('queue-kept-fail-closed', False)
        if err:
            print(f'❌ receipt 寫入失敗：{err}', file=sys.stderr)
            return 1
        print(f'📄 official batch receipt：{receipt_path}（decision=queue-kept-fail-closed）')
        print('❌ 官網核實批次未全部完成，queue 保留：', file=sys.stderr)
        for f in failures:
            print('  -', f, file=sys.stderr)
        if args.degraded_exit_zero and not integrity_failure:
            print('⚠️ 來源失敗但已驗證舊 outputs／queue 保持完整；'
                  '以 degraded 狀態繼續發布，獨立告警保留。', file=sys.stderr)
            return 0
        return 1

    # D1-B：只有「所有腳本／receipt／輸出本身成功」而出現 coverage gap（queue model
    # 冇覆蓋 或 script 確認嘅 coverage pending）才可走 queue-kept-pending-coverage；
    # queue stage／models 原樣保留，唔會 advance。Script coverage pending 即使已經被
    # 另一 script／舊快照覆蓋，仍然必須保留 queue（唔可以藉 missing_coverage 空而推進）。
    if missing_coverage or script_pending_norm:
        err = _finalize('queue-kept-pending-coverage', False)
        if err:
            print(f'❌ pending coverage receipt 寫入失敗：{err}', file=sys.stderr)
            return 1
        print(f'📄 official batch receipt：{receipt_path}'
              '（decision=queue-kept-pending-coverage）')
        print('ℹ️ 官網核實本身全部成功，但有以下 coverage gap；'
              'queue stage／models 原樣保留，發布可繼續並標示規格待核：',
              file=sys.stderr)
        if missing_coverage:
            print(f'  - queue model 未有覆蓋：{len(missing_coverage)} 個', file=sys.stderr)
        if script_pending_norm:
            print(f'  - script 確認 coverage pending：{len(script_pending_norm)} 個',
                  file=sys.stderr)
        for m in missing_coverage[:20]:
            print('    *', m, file=sys.stderr)
        if len(missing_coverage) > 20:
            print(f'    … 其餘 {len(missing_coverage) - 20} 項見 receipt', file=sys.stderr)
        return 0

    # 推進前先寫 ready receipt；寫唔到就絕不 advance
    err = _finalize('ready-to-advance', False)
    if err:
        print(f'❌ ready receipt 寫入失敗，唔會 advance：{err}', file=sys.stderr)
        return 1
    print(f'📄 official batch receipt（ready）：{receipt_path}')

    adv = subprocess.run([sys.executable, os.path.join(BASE, 'advance_queue.py')], cwd=BASE)
    if adv.returncode != 0:
        failures.append(f'advance_queue.py exit {adv.returncode}')
        err = _finalize('ready-to-advance', False)
        if err:
            print(f'❌ advance 失敗而且 receipt 更新失敗：{err}', file=sys.stderr)
            return 1
        print('❌ advance_queue 失敗，queue 未推進（ready receipt 保留）', file=sys.stderr)
        return 1
    err = _finalize('advanced', True)
    if err:
        print(f'⚠️ queue 已推進但 final receipt 更新失敗：{err}；ready receipt 仍在 '
              f'{receipt_path}', file=sys.stderr)
        return 1
    print(f'✅ stage {stage} 官網核實全部完成，隊列已推進（receipt：{receipt_path}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
