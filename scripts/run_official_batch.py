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
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from queue_utils import load_queue, QueueError  # noqa: E402
from crawl_utils import norm_model  # noqa: E402

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


def _atomic_write_json(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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
        if counts['failed'] != 0:
            errors.append(f"receipt failed={counts['failed']} > 0（唔可能成功）")
    succeeded = _canon_list_ok(marker.get('succeededModels'), 'succeededModels', errors)
    failed = _canon_list_ok(marker.get('failedModels'), 'failedModels', errors)
    already = _canon_list_ok(marker.get('alreadyVerified'), 'alreadyVerified', errors)
    covers = _canon_list_ok(marker.get('covers'), 'covers', errors)
    if counts['succeeded'] is not None and len(succeeded) != counts['succeeded']:
        errors.append(f"len(succeededModels) {len(succeeded)} != succeeded {counts['succeeded']}")
    if counts['failed'] is not None and len(failed) != counts['failed']:
        errors.append(f"len(failedModels) {len(failed)} != failed {counts['failed']}")
    if covers != (succeeded | already):
        errors.append('covers 唔等於 canonical union(succeededModels, alreadyVerified)')
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

    for idx, script in enumerate(plan['scripts']):
        path = os.path.join(BASE, script)
        output_rel = plan['outputs'][idx]
        print(f'▶️ [{stage}] {script}', flush=True)
        t0 = time.time()
        entry = {'script': script, 'argv': ['python', script], 'output': output_rel}
        try:
            proc = subprocess.run([sys.executable, path], cwd=BASE, timeout=args.timeout,
                                  capture_output=True, text=True, encoding='utf-8',
                                  errors='replace')
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
        entry['outputHashAfter'] = _sha256_file(os.path.join(BASE, output_rel))
        if rc != 0:
            failures.append(f'{script} exit {rc}')
        if marker is None:
            failures.append(f'{script} 冇 machine receipt（未證明本輪完成證據）')
        else:
            models = output_models(os.path.join(BASE, output_rel))
            failures.extend(f'{script}: {e}' for e in validate_marker(marker, script, models))
        receipt['scripts'].append(entry)

    covers_norm = set()
    for entry in receipt['scripts']:
        marker = entry.get('receipt')
        if isinstance(marker, dict) and entry.get('returncode') == 0:
            covers_norm.update(norm_model(m) for m in (marker.get('covers') or []))

    for rel in plan['outputs']:
        p = os.path.join(BASE, rel)
        ok, reason = validate_output(p)
        receipt['outputs'].append(
            {'output': rel, 'ok': ok, 'reason': reason, 'hash': _sha256_file(p)})
        if not ok:
            failures.append(f'{rel}：{reason}')

    queue_hash_after = _sha256_file(queue_path)
    receipt['queueHashAfter'] = queue_hash_after
    if queue_hash_after != queue_hash_before:
        failures.append('queue 喺批次執行期間被改動；拒絕推進')
    missing_coverage = [m for m in target_models if norm_model(m) not in covers_norm]
    receipt['coveragePending'] = bool(missing_coverage)
    receipt['missingModels'] = list(missing_coverage)
    receipt['missingCanonicalModels'] = sorted({norm_model(m) for m in missing_coverage})
    failures.extend(verify_receipt_outputs(receipt))

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
        return 1

    # D1-B：只有「所有腳本／receipt／輸出本身成功，純 coverage 不足」才可走
    # queue-kept-pending-coverage；queue stage／models 原樣保留，唔會 advance。
    if missing_coverage:
        err = _finalize('queue-kept-pending-coverage', False)
        if err:
            print(f'❌ pending coverage receipt 寫入失敗：{err}', file=sys.stderr)
            return 1
        print(f'📄 official batch receipt：{receipt_path}'
              '（decision=queue-kept-pending-coverage）')
        print('ℹ️ 官網核實本身全部成功，但以下 queue model 未有覆蓋；'
              'queue stage／models 原樣保留，發布可繼續並標示規格待核：',
              file=sys.stderr)
        for m in missing_coverage[:20]:
            print('  -', m, file=sys.stderr)
        if len(missing_coverage) > 20:
            print(f'  … 其餘 {len(missing_coverage) - 20} 項見 receipt', file=sys.stderr)
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
