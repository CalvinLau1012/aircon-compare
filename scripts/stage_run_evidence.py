#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本 run 證據 provenance staging（baseline hash＋timestamp＋run id）。

背景（2026-09-29 第二次返修）：舊版只靠 timestamp >= runStart − 300s，前一次手動 run
嘅 tracked receipt／status 只要喺 run start 前 5 分鐘內，就會被當成本 run 證據。
Timestamp 單獨證明唔到 provenance。本工具改為兩段式：

1. `--write-baseline <path> [files...]`（source step **之前**執行）：
   記錄每個候選檔嘅 presence 同 SHA-256（缺席都記錄 `present: false`）。
2. staging（source step 之後執行）必須傳 `--baseline <path>`：
   只有同時滿足以下全部條件才 stage：
   - 存在、regular file（唔跟 symlink）、JSON object；
   - 有 UTC timestamp（`retrievedAt`／`startedAt`／`finishedAt`／`generatedAt`）；
   - timestamp >= runStartedAt（**唔准 pre-run；冇 skew 容忍**）而且唔係未來；
   - timestamp 唔會單獨被接受：檔案 content SHA-256 必須同 baseline 不同
     （baseline 話 absent，或者 hash 有變）；hash 相同即 `baseline-unchanged` 排除；
   - 如 receipt 有 run-specific id（`runId`／`githubRunId`）而 `--run-id` 有提供，
     必須相等，否則 `run-id-mismatch` 排除。

baseline 檔唔會 stage、亦唔會入 artifact（workflow 寫喺 evidence dir 以外）。全部檔
唔合格時 `run-status.json` 會有明確 error；早退（source rc 非零）亦有 error record。
本工具只讀來源，唔會刪除／改寫生產 receipt。

用法（兩段）：
  python scripts/stage_run_evidence.py --write-baseline /tmp/baseline/emsd.json \
      emsd_receipt.json emsd_raw_receipt.json
  python scripts/stage_run_evidence.py --out-dir "$RUNNER_TEMP/emsd-evidence" \
      --baseline /tmp/baseline/emsd.json --phase emsd --run-id "$GITHUB_RUN_ID" \
      --commit "$GITHUB_SHA" --source-rc "$rc" --run-started-at "..." \
      emsd_receipt.json emsd_raw_receipt.json

退出碼：0 = staging 完成（即使零檔 staged）；2 = 自身失敗／baseline 缺失或損毀。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMESTAMP_FIELDS = ('retrievedAt', 'startedAt', 'finishedAt', 'generatedAt')
RUN_ID_FIELDS = ('runId', 'githubRunId')


def utcnow():
    return datetime.now(timezone.utc)


def parse_utc(value):
    """UTC Z 字串 → datetime；唔合格回 None。"""
    if not isinstance(value, str) or not value.endswith('Z'):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        return None
    return dt


def file_timestamp(data):
    """回傳 (field, datetime) 或 (None, None)。"""
    if not isinstance(data, dict):
        return None, None
    for field in TIMESTAMP_FIELDS:
        dt = parse_utc(data.get(field))
        if dt is not None:
            return field, dt
    return None, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def entry_for(name, staged, field, ts, reason, sha256=None, baseline_hash=None):
    rec = {'name': name, 'staged': bool(staged), 'timestampField': field,
           'timestamp': ts, 'reason': reason}
    if sha256 is not None:
        rec['sha256'] = sha256
    if baseline_hash is not None:
        rec['baselineSha256'] = baseline_hash
    return rec


def classify(path, baseline_entry, run_started, now, run_id):
    """回傳 (staged, field, ts, reason, sha256, baseline_hash)；唔會讀取敏感內容。"""
    name = os.path.basename(path)
    baseline_present = bool((baseline_entry or {}).get('present'))
    baseline_hash = (baseline_entry or {}).get('sha256') if baseline_present else None
    if not os.path.exists(path):
        return False, None, None, 'missing', None, baseline_hash
    if not os.path.isfile(path) or os.path.islink(path):
        return False, None, None, 'not-a-regular-file', None, baseline_hash
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False, None, None, 'not-valid-json', None, baseline_hash
    field, ts = file_timestamp(data)
    if ts is None:
        return False, None, None, 'missing-utc-timestamp', None, baseline_hash
    ts_text = ts.strftime('%Y-%m-%dT%H:%M:%SZ')
    if ts > now + timedelta(seconds=60):
        return False, field, ts_text, 'future-timestamp', None, baseline_hash
    if ts < run_started:
        # 冇任何 skew 容忍：run start 之前嘅 timestamp 一律排除（即使只差 1 秒）
        return False, field, ts_text, 'before-run-start', None, baseline_hash
    if run_id:
        embedded = None
        for key in RUN_ID_FIELDS:
            value = data.get(key)
            if isinstance(value, (str, int)) and str(value):
                embedded = str(value)
                break
        if embedded is not None and embedded != str(run_id):
            return False, field, ts_text, 'run-id-mismatch', None, baseline_hash
    digest = sha256_file(path)
    if baseline_present and baseline_hash == digest:
        return False, field, ts_text, 'baseline-unchanged', digest, baseline_hash
    return True, field, ts_text, 'current-run', digest, baseline_hash


def write_baseline(path, files):
    payload = {'schemaVersion': 1,
               'createdAt': utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
               'files': {}}
    for src in files:
        name = os.path.basename(src)
        if not os.path.exists(src):
            payload['files'][name] = {'present': False, 'sha256': None}
        elif not os.path.isfile(src) or os.path.islink(src):
            payload['files'][name] = {'present': False, 'sha256': None,
                                      'note': 'not-a-regular-file'}
        else:
            payload['files'][name] = {'present': True, 'sha256': sha256_file(src)}
    out = os.path.abspath(path)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    tmp = out + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out)
    return payload


def load_baseline(path):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, 'baseline-invalid'
    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return None, 'baseline-invalid'
    return data, None


def _write_status(out_dir, payload):
    status_path = os.path.join(out_dir, 'run-status.json')
    tmp = status_path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, status_path)
    except OSError as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        print(f'❌ run-status.json 寫入失敗：{e}', file=sys.stderr)
        return 2
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='本 run 證據 baseline／staging')
    ap.add_argument('--write-baseline', default=None,
                    help='只寫 baseline 然後返回（source step 前執行）')
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--baseline', default=None)
    ap.add_argument('--phase', default='emsd')
    ap.add_argument('--run-id', default=None)
    ap.add_argument('--commit', default=None)
    ap.add_argument('--source-rc', type=int, default=0)
    ap.add_argument('--run-started-at', default=None)
    ap.add_argument('files', nargs='*')
    args = ap.parse_args(argv)

    if args.write_baseline:
        try:
            payload = write_baseline(args.write_baseline, args.files)
        except OSError as e:
            print(f'❌ baseline 寫入失敗：{e}', file=sys.stderr)
            return 2
        print(f"✅ baseline：{args.write_baseline}（{len(payload['files'])} 檔）")
        return 0

    if not args.out_dir or not args.baseline or not args.run_started_at:
        print('❌ staging 需要 --out-dir、--baseline、--run-started-at', file=sys.stderr)
        return 2
    run_started = parse_utc(args.run_started_at)
    if run_started is None:
        print('❌ --run-started-at 必須係 UTC Z timestamp', file=sys.stderr)
        return 2
    now = utcnow()
    out_dir = os.path.abspath(args.out_dir)
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as e:
        print(f'❌ 建立 staging dir 失敗：{e}', file=sys.stderr)
        return 2

    baseline, baseline_error = load_baseline(args.baseline)
    records = []
    staged_count = 0
    baseline_failed = baseline is None
    if baseline_failed:
        error = baseline_error
        for path in args.files:
            records.append(entry_for(os.path.basename(path), False, None, None, error))
    else:
        error = None
        baseline_files = baseline.get('files') or {}
        for path in args.files:
            name = os.path.basename(path)
            staged, field, ts, reason, digest, baseline_hash = classify(
                path, baseline_files.get(name), run_started, now, args.run_id)
            if staged:
                dest = os.path.join(out_dir, name)
                try:
                    shutil.copyfile(path, dest)
                except OSError as e:
                    staged, reason = False, f'copy-failed:{type(e).__name__}'
                else:
                    staged_count += 1
            records.append(entry_for(name, staged, field, ts, reason, digest, baseline_hash))
        if args.source_rc != 0 and staged_count == 0:
            error = f'source-rc-{args.source_rc}-and-no-current-run-evidence'

    status = {
        'schemaVersion': 1,
        'phase': str(args.phase),
        'runId': args.run_id,
        'commit': args.commit,
        'sourceRc': args.source_rc,
        'runStartedAt': args.run_started_at,
        'stagedAt': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'baselineUsed': baseline is not None,
        'baselineCreatedAt': (baseline or {}).get('createdAt'),
        'files': records,
        'stagedCount': staged_count,
        'error': error,
    }
    rc = _write_status(out_dir, status)
    if rc != 0:
        return rc
    if baseline_failed:
        print(f'❌ baseline 唔可用（{baseline_error}），拒絕 stage 任何證據', file=sys.stderr)
        return 2
    print(f"✅ evidence staging（{args.phase}）：staged={staged_count}/"
          f"{len(records)} error={error}")
    for rec in records:
        print(f"  - {rec['name']}: staged={rec['staged']} reason={rec['reason']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
