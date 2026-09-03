#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成部署 metadata.json（文檔 §7.2：只能在受信任部署作業生成）

- deployTime 由本腳本以 UTC 生成，不接受手填
- releasePayloadHash 對不含 metadata.json 嘅負載計算 SHA-256（非自引用）
- dataset 欄位由數據校驗報告傳入
- 本地試跑需要 --force（預設要求 CI 環境）

用法：
  python scripts/gen-metadata.py --version 1.2.7 --build B20260826.1 \
    --commit <full-sha> --workflow-run-id 123456 \
    --dataset-date 2026-08-25 --dataset-date-basis retrieval-date-fallback \
    --dataset-source-url "https://www.emsd.gov.hk/..." \
    --dataset-snapshot-id "emsd-20260825" --dataset-hash "sha256:..." \
    --record-count 1927 --payload-dir <DIR> [--deployment-type release] [--force]
"""
import argparse
import hashlib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, 'metadata.json')


def hash_payload(directory):
    """對目錄內所有檔案（除 metadata.json）按路徑排序計 SHA-256"""
    h = hashlib.sha256()
    entries = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', '.venv', 'node_modules')]
        for f in files:
            if f == 'metadata.json':
                continue
            p = os.path.join(root, f)
            rel = os.path.relpath(p, directory).replace('\\', '/')
            entries.append((rel, p))
    for rel, p in sorted(entries):
        h.update(rel.encode('utf-8'))
        h.update(b'\x00')
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                h.update(chunk)
        h.update(b'\x00')
    return 'sha256:' + h.hexdigest()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--version', required=True)
    ap.add_argument('--build', required=True)
    ap.add_argument('--commit', required=True)
    ap.add_argument('--workflow-run-id', required=True)
    ap.add_argument('--deployment-type', choices=['release', 'hotfix', 'rollback'], default='release')
    ap.add_argument('--rollback-of-build', default=None)
    ap.add_argument('--dataset-date', required=True)
    ap.add_argument('--dataset-date-basis', required=True,
                    choices=['official-published-date', 'official-effective-date', 'retrieval-date-fallback'])
    ap.add_argument('--dataset-retrieved-at', default=None)
    ap.add_argument('--dataset-source-url', required=True)
    ap.add_argument('--dataset-snapshot-id', required=True)
    ap.add_argument('--dataset-hash', required=True)
    ap.add_argument('--record-count', type=int, required=True)
    ap.add_argument('--raw-record-count', type=int, default=None)
    ap.add_argument('--registration-count', type=int, default=None)
    ap.add_argument('--model-count', type=int, default=None)
    ap.add_argument('--payload-dir', default=None)
    ap.add_argument('--release-payload-hash', default=None)
    ap.add_argument('--out', default=OUT)
    ap.add_argument('--force', action='store_true',
                    help='本地試跑（預設要求喺受信任 CI 環境先可以生成）')
    args = ap.parse_args()

    # 受信任環境閘門：GitHub Actions + 唔係 fork PR
    if not args.force:
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            print('❌ metadata.json 只能喺受信任部署作業生成（本地試跑加 --force）', file=sys.stderr)
            return 1

    if args.payload_dir and not args.release_payload_hash:
        args.release_payload_hash = hash_payload(args.payload_dir)
    if not args.release_payload_hash:
        print('❌ 需要 --payload-dir 或 --release-payload-hash', file=sys.stderr)
        return 1

    meta = {
        'schemaVersion': '1.0.0',
        'version': args.version,
        'build': args.build,
        'commit': args.commit,
        'deployTime': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'workflowRunId': args.workflow_run_id,
        'deploymentType': args.deployment_type,
        'releasePayloadHash': args.release_payload_hash,
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
            print('❌ rollback 部署必須提供 --rollback-of-build', file=sys.stderr)
            return 1
        meta['rollbackOfBuild'] = args.rollback_of_build

    # optional 計數欄位（D12）：CI 未傳就唔寫，保持向後兼容
    for arg_name, field in (('raw_record_count', 'rawRecordCount'),
                            ('registration_count', 'registrationCount'),
                            ('model_count', 'modelCount')):
        value = getattr(args, arg_name)
        if value is not None:
            meta[field] = value

    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f'✅ metadata.json 已生成：{args.out}（deployTime={meta["deployTime"]}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
