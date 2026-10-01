#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified snapshot rebuild preflight / preservation guard（離線；零 provider）。

用途：`.github/workflows/daily-update.yml` 新增 `rebuild_verified_snapshot` 手動
RELEASE REBUILD 模式：**重用**現有、72 小時內、hash-bound 嘅 EMSD 快照（CSV＋
receipt＋raw receipt），只重新生成 index／PDF／metadata；不抓取任何來源。

本工具兩個子命令：
  - `preflight`：只讀驗證現有 snapshot 可以安全重用，並把要保留嘅來源檔 hash
    寫入 baseline JSON（repo 外）；
  - `guard`：重建之後核對所有來源／canonical 檔 byte 不變（只准 index.html／
    PDF／metadata.json 三個 generated artifacts 改變）。

嚴格性：
  - 重用完整 governance Metadata Schema、payload hash、CSV hash、counts、receipt
    hash-bound facts、raw receipt binding、72h 時效、metadata.commit 本地祖先、
    乾淨 checkout、price stage inactive／force=false；
  - 任何缺失／不符即非零退出，唔會 fallback、唔會偽造新抓取日期／hash；
  - 只讀本地檔案同 git 物件；無網絡、無 provider 呼叫。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
for _p in (BASE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCHEMA_VERSION = 1
AGE_LIMIT = timedelta(hours=72)
HEX40_RE = re.compile(r'^[0-9a-f]{40}$')
SHA256_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z$')
ALLOWED_GENERATED = ('index.html', '空調對比報告.pdf', 'metadata.json')
PRESERVED_SOURCES = (
    'emsd_空調能源標籤.csv', 'emsd_receipt.json', 'emsd_raw_receipt.json',
    'biggo_prices.json', 'prices_meta.json', 'model_blacklist.json', 'model_status.json',
    'new_models.json', 'update_queue.json', 'official_batch_status.json',
    'official_specs.json', 'shew_official.json', 'rasonic_official.json',
    'carrier_official.json', 'general_official.json', 'specs.json',
    'specs_emsd.json', 'gemini_prices.json', 'prices.json',
)


class CheckError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path, label):
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise CheckError(f'{label} 讀取失敗：{type(e).__name__}')
    if not isinstance(data, dict):
        raise CheckError(f'{label} 必須係 JSON object')
    return data


def _parse_utc(text, label):
    if not isinstance(text, str):
        raise CheckError(f'{label} 必須係字串')
    m = TS_RE.match(text)
    if not m:
        raise CheckError(f'{label} 必須係嚴格 UTC Z timestamp（got {text!r}）')
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as e:
        raise CheckError(f'{label} 唔係有效 timestamp：{e}')
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise CheckError(f'{label} 必須係 UTC')
    return dt


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    try:
        return subprocess.run(['git', '-C', repo, *args], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_ok(repo, *args):
    proc = _git(repo, *args)
    return bool(proc is not None and proc.returncode == 0)


def _git_text(repo, *args):
    proc = _git(repo, *args)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout.decode('utf-8', 'replace').strip()


def _resolve_out(path, root, allow_repo_path):
    root_real = os.path.realpath(os.path.abspath(root))
    out_real = os.path.realpath(os.path.abspath(path))
    inside = out_real == root_real or out_real.startswith(root_real + os.sep)
    if inside and not allow_repo_path:
        raise CheckError(f'輸出唔可以喺 repo 內（避免污染工作樹）：{path}')
    return out_real


def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _payload_files_unsafe(files_root, rel):
    path = os.path.join(files_root, rel)
    if not os.path.isfile(path):
        return f'{rel} 唔係 regular file'
    if os.path.islink(path):
        return f'{rel} 係 symlink'
    root_real = os.path.realpath(files_root)
    real = os.path.realpath(path)
    if not (real == root_real or real.startswith(root_real + os.sep)):
        return f'{rel} realpath 逃出 files-root'
    return None


# ---------------------------------------------------------------- core checks


def _check_git(args, expected_head):
    errors = []
    head = _git_text(args.repo, 'rev-parse', 'HEAD')
    if not head or not HEX40_RE.match(head):
        errors.append('git HEAD 唔係完整 40-hex')
    status = _git(args.repo, 'status', '--porcelain')
    if status is None or status.returncode != 0:
        errors.append('git status 讀取失敗')
    elif status.stdout.strip():
        errors.append('checkout 唔乾淨（preflight 需要 clean worktree）')
    if expected_head:
        if not HEX40_RE.match(expected_head):
            errors.append('expected-head 唔係完整 40-hex')
        elif head != expected_head:
            errors.append(f'HEAD {head} != expected-head {expected_head}')
    return errors, head


def _check_commit_ancestry(args, metadata):
    errors = []
    commit = str(metadata.get('commit') or '')
    if not HEX40_RE.match(commit):
        errors.append('metadata.commit 唔係完整 40-hex')
        return errors
    if not _git_ok(args.repo, 'cat-file', '-e', commit + '^{commit}'):
        errors.append(f'metadata.commit 唔存在本地：{commit[:12]}')
        return errors
    if not _git_ok(args.repo, 'merge-base', '--is-ancestor', commit, 'HEAD') \
            and not _git_ok(args.repo, 'merge-base', '--is-ancestor', commit,
                            'origin/master'):
        errors.append(f'metadata.commit 唔係 HEAD／origin/master 祖先：{commit[:12]}')
    return errors


def _check_payload(args, metadata):
    errors = []
    bpa = _load_module('aircon_bpa_rebuild', os.path.join(SCRIPTS, 'build_pages_artifact.py'))
    try:
        payload = bpa.load_payload_manifest(args.manifest)
    except Exception as e:  # noqa: BLE001
        return [f'payload manifest 唔合格：{type(e).__name__}: {e}'], None
    for rel in payload:
        bad = _payload_files_unsafe(args.files_root, rel)
        if bad:
            errors.append(bad)
    if args.csv_rel not in payload:
        errors.append(f'payload 缺少預期 CSV：{args.csv_rel}')
    if errors:
        return errors, None
    _, csv_rel, verify_errors = bpa.verify_metadata(args.files_root, payload, args.metadata)
    errors.extend(verify_errors)
    return errors, csv_rel


def _check_receipts(args, metadata, csv_rel):
    errors = []
    try:
        receipt = _load_json(args.receipt, 'emsd_receipt.json')
        raw = _load_json(args.raw_receipt, 'emsd_raw_receipt.json')
    except CheckError as e:
        return [str(e)]
    if receipt.get('success') is not True or receipt.get('aborted') is not False \
            or receipt.get('error'):
        errors.append('receipt 唔係成功（success/aborted/error）')
    if receipt.get('datasetHash') != metadata.get('datasetHash'):
        errors.append('receipt.datasetHash 同 metadata.datasetHash 唔一致')
    raw_hash = 'sha256:' + _sha256_file(args.raw_receipt)
    if receipt.get('rawReceiptHash') != raw_hash:
        errors.append('receipt.rawReceiptHash 同 raw receipt bytes 唔一致')
    if raw.get('success') is not True:
        errors.append('raw receipt success 唔係 true')
    if raw.get('schemaVersion') != 1:
        errors.append('raw receipt schemaVersion 唔係 1')
    pages = raw.get('pages')
    if not isinstance(pages, list) or not pages:
        errors.append('raw receipt pages 缺失或空')
    elif raw.get('pageCount') != len(pages):
        errors.append('raw receipt pageCount != len(pages)')
    per_page = raw.get('perPageRows')
    if not isinstance(per_page, list) or not per_page \
            or any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in per_page):
        errors.append('raw receipt perPageRows 唔合格')
    elif raw.get('totalRows') != sum(per_page):
        errors.append('raw receipt totalRows != sum(perPageRows)')
    if not (isinstance(raw.get('archiveHash'), str)
            and SHA256_RE.match(raw['archiveHash'])):
        errors.append('raw receipt archiveHash 唔合格')
    if raw.get('datasetHash') != metadata.get('datasetHash'):
        errors.append('raw receipt datasetHash 同 metadata.datasetHash 唔一致')
    if receipt.get('retrievedAt') != raw.get('retrievedAt'):
        errors.append('receipt.retrievedAt 同 raw receipt retrievedAt 唔一致')
    # 頁數／列數三向一致
    if isinstance(pages, list) and isinstance(per_page, list) and len(pages) == len(per_page):
        if receipt.get('pagesExpected') != len(pages) \
                or receipt.get('pagesFetched') != len(pages):
            errors.append('receipt pagesExpected/pagesFetched 同 raw receipt 頁數唔一致')
        if receipt.get('totalRows') != raw.get('totalRows'):
            errors.append('receipt.totalRows 同 raw receipt totalRows 唔一致')
    return errors


def _check_receipt_facts(args, metadata, now_dt):
    errors = []
    facts = None
    try:
        gen = _load_module('aircon_gen_meta_rebuild', os.path.join(SCRIPTS, 'gen-metadata.py'))
        facts = gen.receipt_facts(args.receipt, args.csv, now=now_dt)
    except Exception as e:  # noqa: BLE001
        return [f'receipt_facts 驗證失敗：{type(e).__name__}: {e}'], None
    for field in ('datasetDate', 'datasetDateBasis', 'datasetRetrievedAt',
                  'datasetSourceUrl', 'datasetSnapshotId', 'datasetHash',
                  'rawRecordCount', 'registrationCount'):
        if metadata.get(field) != facts.get(field):
            errors.append(f'metadata.{field} 同 receipt facts 唔一致')
    try:
        from crawl_utils import load_models, load_registrations
        models = len(load_models(args.csv))
        regs = len(load_registrations(args.csv))
        if metadata.get('recordCount') != models:
            errors.append(f'metadata.recordCount {metadata.get("recordCount")} != load_models {models}')
        if metadata.get('modelCount') is not None and metadata.get('modelCount') != models:
            errors.append('metadata.modelCount != load_models')
        if metadata.get('registrationCount') is not None \
                and metadata.get('registrationCount') != regs:
            errors.append('metadata.registrationCount != load_registrations')
    except Exception as e:  # noqa: BLE001
        errors.append(f'CSV counts 計算失敗：{type(e).__name__}')
    return errors, facts


def _check_age(facts, now_dt):
    errors = []
    try:
        retrieved = _parse_utc(facts.get('datasetRetrievedAt'), 'datasetRetrievedAt')
    except CheckError as e:
        return [str(e)]
    if retrieved > now_dt:
        errors.append('datasetRetrievedAt 係未來時間')
    age = now_dt - retrieved
    if age > AGE_LIMIT:
        errors.append(f'snapshot 已超過 72h（age={age.total_seconds() / 3600:.1f}h）')
    return errors


def _check_price_stage(args):
    errors = []
    force = str(args.force_price_batch or '').strip().lower()
    if force not in ('', 'false'):
        errors.append(f'force_price_batch 必須 false（got {args.force_price_batch!r}）')
    try:
        meta = _load_json(args.price_meta, 'prices_meta.json')
    except CheckError as e:
        return [str(e)]
    try:
        batch = _load_module('aircon_batch_rebuild', os.path.join(BASE, 'batch_utils.py'))
        batch._validate_meta(meta, os.path.basename(args.price_meta))
    except Exception as e:  # noqa: BLE001
        return [f'prices_meta.json 契約錯誤：{type(e).__name__}: {e}']
    start = meta.get('price_batch_start')
    idx = meta.get('price_batch_idx', 0)
    if start and isinstance(idx, int) and idx < batch.PRICE_BATCH_DAYS:
        errors.append('price stage 仍然 active：rebuild 模式只可喺 inactive 時執行')
    return errors


def _hash_preserved(args):
    files = {}
    for rel in PRESERVED_SOURCES:
        path = os.path.join(args.files_root, rel)
        if os.path.islink(path):
            raise CheckError(f'preserved source 係 symlink：{rel}')
        if os.path.isfile(path):
            files[rel] = 'sha256:' + _sha256_file(path)
        else:
            files[rel] = None
    return files


def run_preflight(args):
    now_dt = _parse_utc(args.now, '--now') if args.now else datetime.now(timezone.utc)
    checks = []

    def rec(name, errors, detail=None):
        checks.append({'check': name, 'pass': not errors,
                       'errors': errors, 'detail': detail})

    errors, head = _check_git(args, args.expected_head)
    rec('git-clean-and-head', errors, head)

    metadata = None
    try:
        metadata = _load_json(args.metadata, 'metadata.json')
    except CheckError as e:
        rec('metadata-load', [str(e)])
    else:
        rec('metadata-load', [])
        errors = _check_commit_ancestry(args, metadata)
        rec('metadata-commit-ancestry', errors,
            {'commit': metadata.get('commit')})
        errors, csv_rel = _check_payload(args, metadata)
        rec('payload-and-metadata-contract', errors, {'csvRel': csv_rel})
        if csv_rel and csv_rel != args.csv_rel:
            rec('payload-csv-path', [f'payload CSV {csv_rel} != 預期 {args.csv_rel}'])
        errors = _check_receipts(args, metadata, args.csv_rel)
        rec('receipt-raw-binding', errors)
        errors, facts = _check_receipt_facts(args, metadata, now_dt)
        rec('receipt-facts', errors,
            None if not facts else {'datasetDate': facts.get('datasetDate'),
                                    'datasetRetrievedAt': facts.get('datasetRetrievedAt'),
                                    'datasetHash': facts.get('datasetHash')})
        if facts:
            errors = _check_age(facts, now_dt)
            rec('age-within-72h', errors,
                {'now': now_dt.strftime('%Y-%m-%dT%H:%M:%SZ')})
    errors = _check_price_stage(args)
    rec('price-stage-inactive-force-false', errors)

    baseline = None
    try:
        files = _hash_preserved(args)
        baseline = {'schemaVersion': SCHEMA_VERSION, 'createdAt':
                    datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'head': head, 'metadataCommit': (metadata or {}).get('commit'),
                    'files': files}
        baseline_path = _resolve_out(args.baseline_out, args.repo, args.allow_repo_path)
        _atomic_write_json(baseline_path, baseline)
        rec('baseline-written', [], {'path': os.path.basename(baseline_path),
                                     'files': len(files)})
    except CheckError as e:
        rec('baseline-written', [str(e)])

    ok = all(c['pass'] for c in checks)
    report = {'schemaVersion': SCHEMA_VERSION, 'mode': 'preflight', 'ok': ok,
              'checks': checks,
              'snapshot': (None if not metadata else {
                  'metadataCommit': metadata.get('commit'),
                  'datasetDate': metadata.get('datasetDate'),
                  'datasetRetrievedAt': metadata.get('datasetRetrievedAt'),
                  'datasetHash': metadata.get('datasetHash'),
                  'releasePayloadHash': metadata.get('releasePayloadHash'),
              })}
    _write_report(args, report)
    return 0 if ok else 1


def run_guard(args):
    checks = []

    def rec(name, errors, detail=None):
        checks.append({'check': name, 'pass': not errors,
                       'errors': errors, 'detail': detail})

    try:
        baseline = _load_json(args.baseline, 'baseline')
    except CheckError as e:
        rec('baseline-load', [str(e)])
        baseline = None
    if baseline is not None:
        rec('baseline-load', [])
        head = _git_text(args.repo, 'rev-parse', 'HEAD')
        if not head or head != baseline.get('head'):
            rec('head-unchanged', [f'HEAD {head} != baseline {baseline.get("head")}'])
        else:
            rec('head-unchanged', [])
        changed = []
        for rel, want in (baseline.get('files') or {}).items():
            path = os.path.join(args.files_root, rel)
            got = None
            if os.path.islink(path):
                got = 'symlink'
            elif os.path.isfile(path):
                got = 'sha256:' + _sha256_file(path)
            if got != want:
                changed.append(rel)
        disallowed = [rel for rel in changed if rel not in ALLOWED_GENERATED]
        rec('preserved-sources-unchanged', disallowed, {'changedAllowed':
                                                        [r for r in changed
                                                         if r in ALLOWED_GENERATED]})
        status = _git(args.repo, 'status', '--porcelain', '-z', '--',
                      *PRESERVED_SOURCES)
        dirty = []
        if status is not None and status.returncode == 0:
            dirty = [p.decode('utf-8', 'replace') for p in status.stdout.split(b'\0') if p]
        rec('git-preserved-clean', dirty, {'paths': dirty[:5]})
        missing = []
        for rel in ALLOWED_GENERATED:
            path = os.path.join(args.files_root, rel)
            if not os.path.isfile(path) or os.path.islink(path):
                missing.append(rel)
        rec('generated-present', missing)
        try:
            meta = _load_json(os.path.join(args.files_root, 'metadata.json'),
                              'metadata.json')
            rec('generated-metadata-parse', [],
                {'version': meta.get('version'), 'datasetDate': meta.get('datasetDate')})
        except CheckError as e:
            rec('generated-metadata-parse', [str(e)])

    ok = all(c['pass'] for c in checks)
    report = {'schemaVersion': SCHEMA_VERSION, 'mode': 'guard', 'ok': ok,
              'checks': checks}
    _write_report(args, report)
    return 0 if ok else 1


def _write_report(args, report):
    try:
        path = _resolve_out(args.report, args.repo, args.allow_repo_path)
        _atomic_write_json(path, report)
        print(f"{'✅' if report['ok'] else '❌'} rebuild {report['mode']}："
              f"{sum(1 for c in report['checks'] if not c['pass'])} 項失敗 → {args.report}")
    except CheckError as e:
        print(f'❌ 報告寫入失敗：{e}', file=sys.stderr)
        raise SystemExit(2)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='verified snapshot rebuild preflight/guard（離線）')
    sub = ap.add_subparsers(dest='command', required=True)
    parsers = {}
    for name in ('preflight', 'guard'):
        p = sub.add_parser(name)
        p.add_argument('--repo', default=BASE)
        p.add_argument('--files-root', default=None)
        p.add_argument('--report', required=True)
        p.add_argument('--allow-repo-path', action='store_true')
        if name == 'preflight':
            p.add_argument('--metadata', default=None)
            p.add_argument('--manifest', default=None)
            p.add_argument('--receipt', default=None)
            p.add_argument('--raw-receipt', default=None)
            p.add_argument('--csv', default='emsd_空調能源標籤.csv')
            p.add_argument('--price-meta', default=None)
            p.add_argument('--expected-head', default=None)
            p.add_argument('--force-price-batch', default='')
            p.add_argument('--now', default=None)
            p.add_argument('--baseline-out', required=True)
        else:
            p.add_argument('--baseline', required=True)
        parsers[name] = p
    args = ap.parse_args(argv)
    if args.command not in parsers:
        return 2
    args.files_root = args.files_root or args.repo
    if args.command == 'preflight':
        args.metadata = args.metadata or os.path.join(args.files_root, 'metadata.json')
        args.manifest = args.manifest or os.path.join(args.files_root, 'deploy_payload.json')
        args.receipt = args.receipt or os.path.join(args.files_root, 'emsd_receipt.json')
        args.raw_receipt = args.raw_receipt or os.path.join(args.files_root,
                                                            'emsd_raw_receipt.json')
        args.price_meta = args.price_meta or os.path.join(args.files_root, 'prices_meta.json')
        if os.path.isabs(args.csv):
            args.csv_rel = os.path.basename(args.csv)
        else:
            args.csv_rel = args.csv
            args.csv = os.path.join(args.files_root, args.csv)
        return run_preflight(args)
    return run_guard(args)


if __name__ == '__main__':
    sys.exit(main())
