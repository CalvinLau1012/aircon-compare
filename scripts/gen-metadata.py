#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成／完成部署 metadata.json（文檔 §7.2：只能在受信任部署作業生成）

兩階段封裝（DECISIONS.md D14；解決「PDF 先於 metadata ⇒ PDF 讀到上一 run metadata」嘅次序問題）：
  1. --stage core     ：生成同一次部署嘅核心事實（deployTime／datasetRetrievedAt 由腳本 UTC 生成，
                        不接受人手時間）。輸出係未完成件：唔可以叫 metadata.json，
                        亦過唔到正式 Schema（缺 releasePayloadHash）。
  2. --stage finalize ：用核心事實 + 明確 payload manifest 計算 releasePayloadHash，寫出正式
                        metadata.json。除新增 hash 欄位外唔會改動核心事實；寫入前按治理內嵌
                        Schema 自我驗證，失敗唔會寫出檔案。

單階段（省略 --stage）：維持舊行為，一次過生成 + hash（--payload-dir 或 --release-payload-hash）。

用法：
  兩階段（CI）：
    python scripts/gen-metadata.py --stage core --out "$RUNNER_TEMP/metadata.core.json" ...
    python generate_pdf.py --metadata "$RUNNER_TEMP/metadata.core.json"
    python scripts/gen-metadata.py --stage finalize \
        --core "$RUNNER_TEMP/metadata.core.json" --payload-manifest deploy_payload.json \
        --out metadata.json
  單階段（本地試跑）：
    python scripts/gen-metadata.py --version 1.2.7 --build B20260826.1 \
      --commit <full-sha> --workflow-run-id 123456 \
      --dataset-date 2026-08-25 --dataset-date-basis retrieval-date-fallback \
      --dataset-source-url "https://www.emsd.gov.hk/..." \
      --dataset-snapshot-id "emsd-20260825" --dataset-hash "sha256:..." \
      --record-count 1814 --payload-dir <DIR> [--force]
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, 'metadata.json')
CORE_OUT = os.path.join(BASE, 'metadata.core.json')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


def _frame(h, rel, data):
    """長度前綴 framing：消除「路徑／內容含分隔符」造成嘅歧義，令 hash 可審計可重現"""
    rb = rel.encode('utf-8')
    h.update(len(rb).to_bytes(8, 'big'))
    h.update(rb)
    h.update(len(data).to_bytes(8, 'big'))
    h.update(data)


def hash_files(rel_paths, base=None):
    """對明確列出嘅相對路徑清單計算 SHA-256（D14：hash 範圍收斂，唔掃描整個工作區）

    - 只接受相對路徑；拒絕絕對路徑、`..`、重複項；
    - 拒絕包含最終 `metadata.json`（避免 metadata 自引用）；
    - 排序後逐一寫入「長度 + 路徑 + 長度 + 內容」，同一組檔案內容 → 同一 hash。
    """
    base = base or BASE
    norm = []
    for rel in rel_paths:
        rel = str(rel).replace('\\', '/').strip()
        if not rel or rel.startswith('/') or re.match(r'^[A-Za-z]:', rel):
            raise ValueError(f'payload 路徑必須係相對路徑：{rel!r}')
        parts = [p for p in rel.split('/') if p not in ('', '.')]
        if not parts or '..' in parts:
            raise ValueError(f'payload 路徑唔可以有 .／..：{rel!r}')
        rel = '/'.join(parts)
        if rel == 'metadata.json':
            raise ValueError('payload 唔可以包含最終 metadata.json（避免自引用）')
        norm.append(rel)
    if len(set(norm)) != len(norm):
        raise ValueError('payload 清單有重複路徑')
    h = hashlib.sha256()
    for rel in sorted(norm):
        p = os.path.join(base, rel)
        if not os.path.isfile(p):
            raise FileNotFoundError(f'payload 檔案唔存在：{rel}')
        with open(p, 'rb') as fh:
            _frame(h, rel, fh.read())
    return 'sha256:' + h.hexdigest()


def hash_payload(directory):
    """舊接口（兼容）：對目錄內所有檔案（排除 metadata.json 及 VCS／cache）計 hash。

    正式流水線用 --payload-manifest 明確清單；`--payload-dir .` 範圍過寬，只保留兼容。
    """
    entries = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.venv', 'node_modules')]
        for f in files:
            if f == 'metadata.json':
                continue
            p = os.path.join(root, f)
            entries.append(os.path.relpath(p, directory).replace('\\', '/'))
    return hash_files(entries, base=directory)


def hash_manifest(path):
    """讀 payload manifest（deploy_payload.json）並計算 releasePayloadHash（相對 repo 根）"""
    with open(path, encoding='utf-8') as f:
        spec = json.load(f)
    if not isinstance(spec, dict) or not isinstance(spec.get('files'), list) or not spec['files']:
        raise ValueError('payload manifest 必須係 object 且有非空 files 陣列')
    if not all(isinstance(x, str) for x in spec['files']):
        raise ValueError('payload manifest files 必須全部係字串')
    return hash_files(spec['files'], base=BASE)


def build_core_meta(args):
    """生成核心事實（不含 releasePayloadHash）：deployTime 由腳本 UTC 生成，不接受手填"""
    meta = {
        'schemaVersion': '1.0.0',
        'version': args.version,
        'build': args.build,
        'commit': args.commit,
        'deployTime': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'workflowRunId': args.workflow_run_id,
        'deploymentType': args.deployment_type,
        'datasetDate': args.dataset_date,
        'datasetDateBasis': args.dataset_date_basis,
        'datasetRetrievedAt': args.dataset_retrieved_at
                              or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'datasetSourceUrl': args.dataset_source_url,
        'datasetSnapshotId': args.dataset_snapshot_id,
        'datasetHash': args.dataset_hash,
        'recordCount': args.record_count,
    }
    if args.deployment_type == 'rollback':
        if not args.rollback_of_build:
            raise ValueError('rollback 部署必須提供 --rollback-of-build')
        meta['rollbackOfBuild'] = args.rollback_of_build

    # optional 計數欄位（D12）：CI 未傳就唔寫，保持向後兼容
    for arg_name, field in (('raw_record_count', 'rawRecordCount'),
                            ('registration_count', 'registrationCount'),
                            ('model_count', 'modelCount')):
        value = getattr(args, arg_name)
        if value is not None:
            meta[field] = value
    return meta


def write_json(path, data):
    """寫 UTF-8 JSON（LF，2 空格縮排）；拒絕覆寫 core／metadata 互換路徑由 caller 負責"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def schema_errors(meta):
    """用治理內嵌 METADATA_SCHEMA_V1 驗證（同 scripts/validate_metadata.py 同一份 Schema）"""
    from validate_metadata import validate
    from extract_governance import extract_blocks, GOV_FILE
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    return validate(meta, schema)


def _build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('--stage', choices=['core', 'finalize'], default=None,
                    help='兩階段封裝：core=核心事實（未完成件）；finalize=加 releasePayloadHash 寫正式 metadata')
    ap.add_argument('--core', default=None, help='finalize：core 階段輸出檔案')
    ap.add_argument('--payload-manifest', default=None, help='finalize：明確 payload 清單（如 deploy_payload.json）')
    ap.add_argument('--version', default=None)
    ap.add_argument('--build', default=None)
    ap.add_argument('--commit', default=None)
    ap.add_argument('--workflow-run-id', default=None)
    ap.add_argument('--deployment-type', choices=['release', 'hotfix', 'rollback'], default='release')
    ap.add_argument('--rollback-of-build', default=None)
    ap.add_argument('--dataset-date', default=None)
    ap.add_argument('--dataset-date-basis', default=None,
                    choices=['official-published-date', 'official-effective-date', 'retrieval-date-fallback'])
    ap.add_argument('--dataset-retrieved-at', default=None)
    ap.add_argument('--dataset-source-url', default=None)
    ap.add_argument('--dataset-snapshot-id', default=None)
    ap.add_argument('--dataset-hash', default=None)
    ap.add_argument('--record-count', type=int, default=None)
    ap.add_argument('--raw-record-count', type=int, default=None)
    ap.add_argument('--registration-count', type=int, default=None)
    ap.add_argument('--model-count', type=int, default=None)
    ap.add_argument('--payload-dir', default=None)
    ap.add_argument('--release-payload-hash', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--force', action='store_true',
                    help='本地試跑（預設要求喺受信任 CI 環境先可以生成）')
    return ap


def _require_core_args(ap, args):
    missing = []
    for name, label in (('version', '--version'), ('build', '--build'), ('commit', '--commit'),
                        ('workflow_run_id', '--workflow-run-id'), ('dataset_date', '--dataset-date'),
                        ('dataset_date_basis', '--dataset-date-basis'),
                        ('dataset_source_url', '--dataset-source-url'),
                        ('dataset_snapshot_id', '--dataset-snapshot-id'),
                        ('dataset_hash', '--dataset-hash')):
        if not getattr(args, name):
            missing.append(label)
    if args.record_count is None:
        missing.append('--record-count')
    if missing:
        ap.error('缺少必要參數：' + '、'.join(missing))


def _finalize(args):
    if not args.core:
        print('❌ finalize 階段需要 --core', file=sys.stderr)
        return 1
    core_path = os.path.abspath(args.core)
    if os.path.basename(core_path) == 'metadata.json':
        print('❌ --core 唔可以係 metadata.json（未完成件唔可以冒充部署 metadata）', file=sys.stderr)
        return 1
    out_path = os.path.abspath(args.out or OUT)
    if os.path.basename(out_path) != 'metadata.json':
        print('❌ finalize 輸出必須命名為 metadata.json（正式部署事實）', file=sys.stderr)
        return 1
    if out_path == core_path:
        print('❌ finalize 輸出唔可以覆寫 --core', file=sys.stderr)
        return 1
    try:
        with open(core_path, encoding='utf-8') as f:
            core = json.load(f)
    except Exception as e:
        print(f'❌ 讀唔到 core metadata：{e}', file=sys.stderr)
        return 1
    if not isinstance(core, dict):
        print('❌ core metadata 必須係 JSON object', file=sys.stderr)
        return 1
    if 'releasePayloadHash' in core:
        print('❌ core 已經有 releasePayloadHash（唔可以重複 finalize）', file=sys.stderr)
        return 1
    try:
        if args.payload_manifest:
            release_hash = hash_manifest(args.payload_manifest)
        elif args.payload_dir:
            release_hash = hash_payload(args.payload_dir)
        elif args.release_payload_hash:
            release_hash = args.release_payload_hash
        else:
            raise ValueError('需要 --payload-manifest（建議）或 --payload-dir／--release-payload-hash')
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f'❌ payload hash 失敗：{e}', file=sys.stderr)
        return 1

    # 只新增 hash 欄位；核心事實（version／build／commit／deployTime／dataset*）一律不變
    final = dict(core)
    final['releasePayloadHash'] = release_hash
    errors = schema_errors(final)
    if errors:
        print('❌ finalize 出嘅 metadata 唔過正式 Schema（唔會寫出檔案）：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1
    write_json(out_path, final)
    print(f'✅ metadata.json 已完成（finalize）：{out_path}（releasePayloadHash={release_hash}）')
    return 0


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = _build_parser()
    args = ap.parse_args()

    # 受信任環境閘門：GitHub Actions + 唔係 fork PR（core／finalize／單階段一律適用）
    if not args.force:
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            print('❌ metadata.json 只能喺受信任部署作業生成（本地試跑加 --force）', file=sys.stderr)
            return 1

    if args.stage == 'finalize':
        return _finalize(args)

    _require_core_args(ap, args)

    if args.stage == 'core':
        if args.payload_dir or args.release_payload_hash or args.payload_manifest:
            print('❌ core 階段唔接受 payload hash 參數（由 finalize 階段計算）', file=sys.stderr)
            return 1
        out_path = os.path.abspath(args.out or CORE_OUT)
        if os.path.basename(out_path) == 'metadata.json':
            print('❌ core 階段輸出唔可以叫 metadata.json（未 finalize，唔可以冒充部署 metadata）',
                  file=sys.stderr)
            return 1
        try:
            meta = build_core_meta(args)
        except ValueError as e:
            print(f'❌ {e}', file=sys.stderr)
            return 1
        write_json(out_path, meta)
        print(f'✅ metadata core 已生成（未 finalize）：{out_path}（deployTime={meta["deployTime"]}）')
        return 0

    # 單階段（兼容舊行為）：一次過生成 + hash
    try:
        meta = build_core_meta(args)
    except ValueError as e:
        print(f'❌ {e}', file=sys.stderr)
        return 1
    try:
        if args.payload_manifest:
            meta['releasePayloadHash'] = hash_manifest(args.payload_manifest)
        elif args.payload_dir and not args.release_payload_hash:
            meta['releasePayloadHash'] = hash_payload(args.payload_dir)
        elif args.release_payload_hash:
            meta['releasePayloadHash'] = args.release_payload_hash
        else:
            raise ValueError('需要 --payload-manifest／--payload-dir 或 --release-payload-hash')
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f'❌ payload hash 失敗：{e}', file=sys.stderr)
        return 1
    errors = schema_errors(meta)
    if errors:
        print('❌ 生成嘅 metadata 唔過正式 Schema：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1
    out_path = args.out or OUT
    write_json(out_path, meta)
    print(f'✅ metadata.json 已生成：{out_path}（deployTime={meta["deployTime"]}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
