#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建立隔離嘅 PR fixture release 目錄（唔係正式部署產物）。

PR gate 唔可以用 repo 內 production metadata.json 去驗候選 payload（版本／hash
可能未同步），所以喺 repo 外 temp dir 用「明示 fixture」重建一次可信管線：
  - 複製候選 payload 檔案（index / CSV）；
  - 由 fixture core metadata 真係重新生成 PDF（同正式兩階段封裝次序一致）；
  - 以 fixture payload bytes 計算 releasePayloadHash，寫出完整 metadata.json；
  - 複製 deploy_payload.json / deploy_envelope.json，之後由
    build_pages_artifact.py 喺同一 fixture dir 做公開 artifact 驗證。

fixture 身分會清楚寫入 `build`（例如 `B20260923.PR-FIXTURE`）同 snapshot id，
永遠只存在 runner temp，唔會 commit、唔會部署。任何步驟失敗即非零退出。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def _hkt_date(retrieved_at):
    dt = datetime.strptime(retrieved_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    return (dt + timedelta(hours=8)).strftime('%Y-%m-%d')


def build_fixture(repo, out_dir, now=None, build=None, commit='a' * 40):
    repo = os.path.abspath(repo)
    out_dir = os.path.abspath(out_dir)
    if os.path.lexists(out_dir):
        raise ValueError(f'fixture 輸出目錄已經存在：{out_dir}')
    try:
        inside_repo = os.path.commonpath([out_dir, os.path.realpath(repo)]) == os.path.realpath(repo)
    except ValueError:
        inside_repo = False  # 唔同 drive 一定喺 repo 外
    if inside_repo:
        raise ValueError('fixture 輸出目錄唔可以喺 repo 內')
    bpa = _load('aircon_bpa_fixture', os.path.join(BASE, 'scripts', 'build_pages_artifact.py'))
    gen = _load('aircon_gen_fixture', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
    payload = bpa.load_payload_manifest(os.path.join(repo, 'deploy_payload.json'))
    csvs = [f for f in payload if f.lower().endswith('.csv')]
    if len(csvs) != 1:
        raise ValueError(f'payload 必須有恰好一個 CSV：{csvs}')
    csv_rel = csvs[0]
    now = now or datetime.now(timezone.utc)
    now = now.replace(microsecond=0)
    deploy_time = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    retrieved_at = deploy_time
    src_url = 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php'
    receipt_path = os.path.join(repo, 'emsd_receipt.json')
    if os.path.isfile(receipt_path):
        try:
            receipt = json.load(open(receipt_path, encoding='utf-8'))
            if receipt.get('success') is True and isinstance(receipt.get('retrievedAt'), str) \
                    and receipt['retrievedAt'].endswith('Z'):
                retrieved_at = receipt['retrievedAt']
                if receipt.get('sourceUrl'):
                    src_url = receipt['sourceUrl'].split('?')[0]
        except (OSError, ValueError):
            pass
    dataset_date = _hkt_date(retrieved_at)
    import csv as _csv
    with open(os.path.join(repo, *csv_rel.split('/')), encoding='utf-8-sig') as f:
        rows = sum(1 for _ in _csv.reader(f)) - 1
    import models_data
    if build is None:
        build = 'B' + dataset_date.replace('-', '') + '.PR-FIXTURE'
    core = {
        'schemaVersion': '1.0.0',
        'version': models_data.VERSION,
        'build': build,
        'commit': commit,
        'deployTime': deploy_time,
        'workflowRunId': 'pr-fixture',
        'deploymentType': 'release',
        'datasetDate': dataset_date,
        'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': retrieved_at,
        'datasetSourceUrl': src_url,
        'datasetSnapshotId': f'emsd-{dataset_date}-pr-fixture',
        'datasetHash': _sha256(os.path.join(repo, *csv_rel.split('/'))),
        'recordCount': rows,
        'rawRecordCount': rows,
        'registrationCount': rows,
        'modelCount': rows,
    }
    os.makedirs(out_dir)
    try:
        for rel in payload:
            dst = os.path.join(out_dir, *rel.split('/'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(repo, *rel.split('/')), dst)
        core_path = os.path.join(out_dir, 'metadata.core.json')
        with open(core_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(core, f, ensure_ascii=False, indent=2)
        pdf_rel = next((f for f in payload if f.lower().endswith('.pdf')), None)
        if pdf_rel:
            proc = subprocess.run(
                [sys.executable, os.path.join(BASE, 'generate_pdf.py'),
                 '--metadata', core_path, '--out', os.path.join(out_dir, *pdf_rel.split('/'))],
                cwd=BASE, capture_output=True, text=True, encoding='utf-8', errors='replace')
            if proc.returncode != 0:
                raise RuntimeError(f'fixture PDF 生成失敗（rc={proc.returncode}）：'
                                   f'{(proc.stdout or "")[-500:]} {(proc.stderr or "")[-500:]}')
        payload_hash = gen.hash_files(payload, base=out_dir)
        core['releasePayloadHash'] = payload_hash
        from validate_metadata import validate
        from extract_governance import extract_blocks, GOV_FILE
        schema = extract_blocks(open(GOV_FILE, encoding='utf-8').read())['AIRCON_METADATA_SCHEMA_V1']
        errors = validate(core, schema)
        if errors:
            raise RuntimeError('fixture metadata 唔過 Schema：' + '；'.join(errors))
        meta_path = os.path.join(out_dir, 'metadata.json')
        with open(meta_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(core, f, ensure_ascii=False, indent=2)
        os.remove(core_path)
        for name in ('deploy_payload.json', 'deploy_envelope.json'):
            shutil.copyfile(os.path.join(repo, name), os.path.join(out_dir, name))
        return {'out': out_dir, 'files': payload + ['metadata.json'],
                'releasePayloadHash': payload_hash, 'datasetHash': core['datasetHash'],
                'version': core['version'], 'build': core['build'], 'commit': core['commit']}
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='建立隔離 PR fixture release 目錄')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--out', required=True)
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)
    try:
        report = build_fixture(args.repo, args.out)
    except Exception as e:  # noqa: BLE001
        print(f'❌ fixture release 建立失敗：{e}', file=sys.stderr)
        return 1
    if args.report:
        with open(args.report, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'✅ fixture release：{report["out"]}（build={report["build"]}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
