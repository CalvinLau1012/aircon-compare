#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成／完成部署 metadata.json（文檔 §7.2：只能在受信任部署作業生成）

兩階段封裝（DECISIONS.md D14；解決「PDF 先於 metadata ⇒ PDF 讀到上一 run metadata」嘅次序問題）：
  1. --stage core     ：生成同一次部署嘅核心事實（deployTime 由腳本 UTC 生成；datasetRetrievedAt 由成功收據提供，
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
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import csv
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


def normalize_manifest_paths(rel_paths):
    """只驗證 payload manifest 路徑語法（唔睇檔案存唔存在），回傳正規化清單。

    - 每個 entry 必須係非空字串；
    - 只接受相對路徑（拒絕絕對、磁碟機、`..`、`.`）；
    - 拒絕重複項同最終 `metadata.json`（避免自引用）。
    """
    norm = []
    for rel in rel_paths:
        if not isinstance(rel, str):
            raise ValueError(f'payload 路徑必須係字串：{rel!r}')
        rel = rel.replace('\\', '/').strip()
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
    return norm


def validate_manifest_files(rel_paths, base=None):
    """驗證 payload manifest 路徑契約兼檔案存在；回傳正規化清單。

    hash_files 同歸檔／index 檢查工具共用此驗證，避免 pack 用原路徑而 hash 用
    正規化路徑造成不一致；亦拒絕 symlink 跳出 base。
    """
    base = os.path.abspath(base or BASE)
    norm = normalize_manifest_paths(rel_paths)
    for rel in norm:
        p = os.path.join(base, rel)
        if not os.path.isfile(p):
            raise FileNotFoundError(f'payload 檔案唔存在：{rel}')
        real = os.path.normcase(os.path.realpath(p))
        if not real.startswith(os.path.normcase(base) + os.sep):
            raise ValueError(f'payload 路徑跳出 base（symlink／traversal）：{rel!r}')
    return norm


def hash_files(rel_paths, base=None):
    """對明確列出嘅相對路徑清單計算 SHA-256（D14：hash 範圍收斂，唔掃描整個工作區）

    - 路徑契約由 validate_manifest_files 共用驗證；
    - 排序後逐一寫入「長度 + 路徑 + 長度 + 內容」，同一組檔案內容 → 同一 hash。
    """
    base = os.path.abspath(base or BASE)
    norm = validate_manifest_files(rel_paths, base=base)
    h = hashlib.sha256()
    for rel in sorted(norm):
        p = os.path.join(base, rel)
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


def receipt_facts(path, csv_path, now=None):
    """Only a complete, hash-bound EMSD receipt can date the dataset.

    - 必須 success=True、aborted=False、無 error；
    - pagesExpected == pagesFetched == len(perPageRows)、每頁 > 0、總數加總一致；
    - sourceUrl 必須係批准嘅 EMSD 官方 https 路徑；
    - retrievedAt 必須係 UTC `Z`、唔可以係未來、唔可以係生成時間補出嚟；
    - receipt.datasetHash 必須同現時 CSV bytes SHA-256 一致（缺 hash 或唔符 → 失敗，
      舊收據冇 hash 唔可以補假時間／假 hash，必須重新成功抓取一次）。

    重建模式（用舊完整 hash-bound 收據 + 同一 CSV bytes）時，日期由收據實際
    retrievedAt 轉香港時區得出，因此會保留舊日期，唔會用今日生成時間改寫。
    """
    from pathlib import Path
    try:
        receipt = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f'EMSD receipt 讀取失敗：{e}')
    if not isinstance(receipt, dict):
        raise ValueError('EMSD receipt 必須係 JSON object')
    if receipt.get('success') is not True or receipt.get('aborted') is not False or receipt.get('error'):
        raise ValueError('EMSD receipt records an unsuccessful retrieval')
    counts = receipt.get('perPageRows', [])
    if (not isinstance(counts, list) or not counts
            or any(type(n) is not int or n <= 0 for n in counts)
            or receipt.get('pagesExpected') != len(counts)
            or receipt.get('pagesFetched') != len(counts)
            or receipt.get('totalRows') != sum(counts)):
        raise ValueError('EMSD receipt page counts are incomplete')
    source = urlparse(receipt.get('sourceUrl', ''))
    if source.scheme != 'https' or source.hostname != 'www.emsd.gov.hk' or source.path != '/energylabel/tc/households/rac/select_ac_result.php':
        raise ValueError('Unapproved EMSD receipt source')
    if source.username or source.password:
        raise ValueError('EMSD receipt source 唔可以有 userinfo')
    if source.port is not None:
        raise ValueError('EMSD receipt source 唔可以有非標準 port')
    timestamp = receipt.get('retrievedAt', '')
    if not isinstance(timestamp, str) or not timestamp.endswith('Z'):
        raise ValueError('Receipt retrievedAt must be UTC Z')
    try:
        retrieved = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('Receipt retrievedAt is not a valid timestamp')
    if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0):
        raise ValueError('Receipt retrievedAt must be UTC')
    if retrieved > (now or datetime.now(timezone.utc)) + timedelta(minutes=5):
        raise ValueError('Receipt is in the future')
    raw = Path(csv_path).read_bytes()
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    if not receipt.get('datasetHash'):
        raise ValueError('EMSD receipt has no datasetHash (legacy receipt)；先重新成功抓取一次'
                         '（python fetch_emsd.py）產生 hash-bound 收據再重建，舊收據唔可以補假時間')
    if receipt.get('datasetHash') != digest:
        raise ValueError('Receipt hash missing or not matching CSV; retrieve a new snapshot')
    try:
        rows = list(csv.reader(raw.decode('utf-8-sig').splitlines()))
    except UnicodeDecodeError as e:
        raise ValueError(f'EMSD CSV 唔係有效 UTF-8：{e}')
    if not rows or len(rows[0]) != 15 or len(rows[0]) < 2 or rows[0][1].strip() != '型號':
        raise ValueError('EMSD CSV header 唔符合預期（15 欄、第 2 欄型號）')
    if any(len(r) != 15 for r in rows):
        raise ValueError('EMSD CSV 有行唔係 15 欄')
    if len(rows) - 1 != sum(counts):
        raise ValueError('Receipt row count does not match CSV')
    day = retrieved.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    return {'datasetDate': day, 'datasetDateBasis': 'retrieval-date-fallback',
            'datasetRetrievedAt': timestamp, 'datasetSourceUrl': receipt['sourceUrl'],
            'datasetSnapshotId': f'emsd-{day}-{digest[-12:]}', 'datasetHash': digest,
            'rawRecordCount': len(rows) - 1, 'registrationCount': len(rows) - 1}


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
        'datasetRetrievedAt': args.dataset_retrieved_at,
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
    if getattr(args, 'emsd_receipt', None):
        facts = receipt_facts(args.emsd_receipt, args.dataset_csv)
        if args.dataset_hash != facts['datasetHash']:
            raise ValueError('Supplied dataset hash differs from receipt')
        for name, field in [('raw_record_count', 'rawRecordCount'), ('registration_count', 'registrationCount')]:
            if getattr(args, name) is not None and getattr(args, name) != facts[field]:
                raise ValueError(f'{field} differs from receipt')
        meta.update(facts)
    elif not args.dataset_retrieved_at:
        # 冇收據時唔可以用生成時間補 retrievedAt（測試 legacy 模式要明示 fixture）
        raise ValueError('冇 --emsd-receipt 時必須提供 --dataset-retrieved-at'
                         '（唔可以用生成時間冒充抓取時間）')
    if meta.get('datasetRetrievedAt'):
        if not meta['datasetRetrievedAt'].endswith('Z'):
            raise ValueError('datasetRetrievedAt 必須係 UTC Z')
    # 計數一致性契約（唔以真實 count 代替契約）
    record_count = meta.get('recordCount')
    if record_count is not None and record_count < 1:
        raise ValueError('recordCount 必須 >= 1')
    if meta.get('modelCount') is not None and record_count is not None \
            and meta['modelCount'] != record_count:
        raise ValueError(f"modelCount {meta['modelCount']} 唔等於 recordCount {record_count}")
    for field in ('rawRecordCount', 'registrationCount'):
        if meta.get(field) is not None and record_count is not None \
                and meta[field] < record_count:
            raise ValueError(f'{field} {meta[field]} 唔應該少過 recordCount {record_count}')
    return meta


def write_json(path, data):
    """原子寫 UTF-8 JSON（LF，2 空格縮排）；先寫 tmp 再 replace，失敗唔會留低半寫檔。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def schema_errors(meta):
    """用治理內嵌 METADATA_SCHEMA_V1 驗證（同 scripts/validate_metadata.py 同一份 Schema）"""
    from validate_metadata import validate
    from extract_governance import extract_blocks, GOV_FILE
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    return validate(meta, schema)


def core_schema_errors(meta):
    """核心事實 fail-closed：正式 Schema 仍拒絕無 releasePayloadHash 嘅 core，
    但呢度用 placeholder hash 驗證其餘所有約束（類型／pattern／enum／const／日期），
    確保唔會喺核心事實無效時照樣生成 PDF。"""
    probe = dict(meta)
    probe.setdefault('releasePayloadHash', 'sha256:' + '0' * 64)
    return schema_errors(probe)


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
    ap.add_argument('--emsd-receipt', default=None)
    ap.add_argument('--dataset-csv', default=os.path.join(BASE, 'emsd_空調能源標籤.csv'))
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
        if args.emsd_receipt and name in ('dataset_date', 'dataset_date_basis', 'dataset_source_url', 'dataset_snapshot_id'):
            continue
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
    # finalize 嚴格驗 core（placeholder hash）先，唔合格唔會繼續計算 hash
    core_errors = core_schema_errors(core)
    if core_errors:
        print('❌ finalize 前 core 唔過正式 Schema 約束（唔會寫出檔案）：', file=sys.stderr)
        for e in core_errors:
            print('  -', e, file=sys.stderr)
        return 1
    # CSV／hash 關係：datasetHash 必須對現行 CSV bytes（同一個資料快照）
    try:
        with open(args.dataset_csv, 'rb') as f:
            csv_digest = 'sha256:' + hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        print(f'❌ 讀唔到 dataset CSV（{args.dataset_csv}）：{e}', file=sys.stderr)
        return 1
    if core.get('datasetHash') != csv_digest:
        print('❌ core.datasetHash 同現行 CSV bytes 唔一致（唔可以 finalize 錯配快照）',
              file=sys.stderr)
        return 1
    if core.get('rawRecordCount') is not None:
        try:
            with open(args.dataset_csv, encoding='utf-8-sig') as f:
                n_rows = sum(1 for _ in csv.reader(f)) - 1
            if n_rows != core['rawRecordCount']:
                print(f"❌ core.rawRecordCount {core['rawRecordCount']} 同 CSV {n_rows} 行唔一致",
                      file=sys.stderr)
                return 1
        except (OSError, UnicodeDecodeError, csv.Error) as e:
            print(f'❌ CSV 行數檢查失敗：{e}', file=sys.stderr)
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
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = _build_parser()
    args = ap.parse_args()

    # 受信任環境閘門：GitHub Actions + 唔係 fork PR（core／finalize／單階段一律適用）
    if not args.force:
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            print('❌ metadata.json 只能喺受信任部署作業生成（本地試跑加 --force）', file=sys.stderr)
            return 1
        if os.environ.get('GITHUB_EVENT_NAME', '').startswith('pull_request'):
            print('❌ 唔可以喺 PR／fork 事件生成部署 metadata（只有受保護分支／tag 嘅受信任作業可以）',
                  file=sys.stderr)
            return 1
        if args.stage != 'finalize' and not args.emsd_receipt:
            print('❌ 生產 metadata 必須用成功、hash-bound EMSD 收據（--emsd-receipt）；'
                  '唔可以用手填日期／生成時間補資料事實', file=sys.stderr)
            return 1

    # 本地 --force 唔可以無意覆寫 repo 生產 metadata.json（容器部署須明示 AIRCON_ALLOW_REPO_METADATA=1）
    if args.force and not os.environ.get('GITHUB_ACTIONS') \
            and os.environ.get('AIRCON_ALLOW_REPO_METADATA') != '1':
        dest = os.path.abspath(args.out or (OUT if args.stage != 'core' else CORE_OUT))
        if args.stage != 'core' and dest == os.path.abspath(OUT):
            print('❌ 本地 --force 唔可以覆寫 repo 生產 metadata.json；請用 --out <temp>，'
                  '或容器部署設 AIRCON_ALLOW_REPO_METADATA=1', file=sys.stderr)
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
        core_errors = core_schema_errors(meta)
        if core_errors:
            print('❌ core 核心事實唔過正式 Schema 約束（唔會寫出檔案、後續唔可以生成 PDF）：',
                  file=sys.stderr)
            for e in core_errors:
                print('  -', e, file=sys.stderr)
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
