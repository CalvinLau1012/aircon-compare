#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verified snapshot rebuild preflight / preservation guard（離線；零 provider）。

用途：`.github/workflows/daily-update.yml` 嘅 `rebuild_verified_snapshot` 手動
RELEASE REBUILD 模式：**重用**現有、72 小時內、hash-bound 嘅 EMSD 快照（CSV＋
receipt＋raw receipt），只重新生成 index／PDF／metadata；不抓取任何來源。

本工具兩個子命令：
  - `preflight`：只讀驗證現有 snapshot 可以安全重用，並把來源檔 hash 同
    acquisition facts 寫入 baseline JSON（repo 外）。**只有全部檢查通過先會寫出
    可用 baseline**；失敗會將同一路徑嘅舊 baseline 失效化（repo 外），確保唔會
    誤用上一次成功結果。
  - `guard`：重建之後核對 (1) baseline schema／內容嚴格有效、(2) git worktree／
    index 改動只限 index.html／空調對比報告.pdf／metadata.json 三個 generated
    outputs、(3) 所有來源輸入 byte 不變、(4) 新 metadata 通過完整治理 Schema 且
    acquisition facts 同 baseline 一致。

嚴格性：
  - 重用完整 governance Metadata Schema、payload hash、CSV hash、counts、receipt
    同 raw receipt 嘅 hash／頁數／durable source provenance 綁定、72h 時效、
    metadata.commit 本地祖先、乾淨非 shallow checkout、price stage inactive／
    force=false；
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
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')
for _p in (BASE, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SCHEMA_VERSION = 2
AGE_LIMIT = timedelta(hours=72)
HEX40_RE = re.compile(r'^[0-9a-f]{40}$')
SHA256_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?Z$')

# 只准呢三個 generated artifact 喺 rebuild 期間改變。
ALLOWED_GENERATED = ('index.html', '空調對比報告.pdf', 'metadata.json')
# 部署 payload manifest（D14）必須恰好包含呢三個檔。
EXPECTED_PAYLOAD = ('index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv')

# 重建期間必須 byte 不變嘅來源輸入（generate_html／generate_pdf／metadata 契約
# 讀到嘅檔，加 payload manifest）。baseline 必須恰好包含呢個集合，唔可以靜靜
# 漏咗任何一項。
REQUIRED_PRESERVED = (
    # EMSD 快照同 provenance
    'emsd_空調能源標籤.csv', 'emsd_receipt.json', 'emsd_raw_receipt.json',
    # canonical price／lifecycle／queue／status 輸入
    'biggo_prices.json', 'prices_meta.json', 'model_blacklist.json', 'model_status.json',
    'new_models.json', 'update_queue.json', 'official_batch_status.json',
    # generate_html.load_official() 會讀嘅品牌官網資料
    'official_specs.json', 'shew_official.json', 'rasonic_official.json',
    'carrier_official.json', 'general_official.json', 'pana_official.json',
    'midea_official.json',
    # 其餘規格／價格輸入
    'specs.json', 'specs_emsd.json', 'gemini_prices.json', 'prices.json',
    # 報告同生成器資產（generate_html／generate_pdf／extract_governance 讀取）
    '空調對比報告.md', 'whale_girl.webp', 'blue_fantasy_art.txt',
    'docs/AIRCON_COMPARE_GOVERNANCE.md',
    # 部署 payload manifest（releasePayloadHash 範圍）
    'deploy_payload.json',
)

# 重建後必須同 baseline 一致嘅 acquisition facts（commit／build／runId／
# deployTime／releasePayloadHash 可以合法改變，唔喺呢個集合內）。
FACT_FIELDS = ('datasetDate', 'datasetDateBasis', 'datasetRetrievedAt',
               'datasetSourceUrl', 'datasetSnapshotId', 'datasetHash',
               'recordCount', 'rawRecordCount', 'registrationCount', 'modelCount')
REQUIRED_FACT_FIELDS = ('datasetDate', 'datasetDateBasis', 'datasetRetrievedAt',
                        'datasetSourceUrl', 'datasetSnapshotId', 'datasetHash',
                        'recordCount')
DATE_BASIS_ENUM = ('official-published-date', 'official-effective-date',
                   'retrieval-date-fallback')
SOURCE_KIND_PAGINATED = 'emsd-energy-label-paginated'
SOURCE_KIND_CSV = 'emsd-open-data-csv'


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


def _utc_now_text():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_run(repo, *args, timeout=120):
    try:
        return subprocess.run(['git', '-C', repo, *args], capture_output=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def _git_bytes(repo, *args):
    proc = _git_run(repo, *args)
    if proc is None or proc.returncode != 0:
        return None
    return proc.stdout


def _git_text(repo, *args):
    raw = _git_bytes(repo, *args)
    if raw is None:
        return None
    return raw.decode('utf-8', 'replace').strip()


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


def _file_unsafe(files_root, rel):
    """檢查 rel 係 files_root 內嘅 regular file（拒 symlink／traversal／缺檔）。"""
    if os.path.isabs(rel) or '..' in rel.split('/') or not rel:
        return f'不安全路徑：{rel!r}'
    path = os.path.join(files_root, *rel.split('/'))
    if not os.path.isfile(path):
        return f'{rel} 唔係 regular file（缺檔或係目錄）'
    if os.path.islink(path):
        return f'{rel} 係 symlink'
    root_real = os.path.realpath(files_root)
    real = os.path.realpath(path)
    if not (real == root_real or real.startswith(root_real + os.sep)):
        return f'{rel} realpath 逃出 files-root'
    return None


def _is_sha256(value):
    return isinstance(value, str) and bool(SHA256_RE.match(value))


def _is_pos_int(value):
    return type(value) is int and value > 0


def _is_nonneg_int(value):
    return type(value) is int and value >= 0


def _parse_porcelain_z(raw):
    """嚴格解析 `git status --porcelain -z`；rename/copy 帶第二個 path token。"""
    tokens = raw.split(b'\0')
    entries = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if len(tok) < 4 or tok[2:3] != b' ':
            raise CheckError(f'git status porcelain 格式唔預期：{tok[:40]!r}')
        status = tok[:2].decode('ascii', 'replace')
        path = tok[3:].decode('utf-8', 'replace')
        src = None
        if 'R' in status or 'C' in status:
            if i < len(tokens) and tokens[i]:
                src = tokens[i].decode('utf-8', 'replace')
                i += 1
        entries.append((status, path, src))
    return entries


# ---------------------------------------------------------------- core checks


def _check_git(args, expected_head):
    """Preflight：要求有效 HEAD 同完全乾淨 checkout；git 命令失敗即 fail。"""
    errors = []
    raw = _git_bytes(args.repo, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}')
    head = None
    if raw is None:
        errors.append('git HEAD 讀取失敗（唔係有效 git worktree？）')
    else:
        head = raw.decode('ascii', 'replace').strip()
        if not HEX40_RE.match(head):
            errors.append(f'git HEAD 唔係完整 40-hex：{head!r}')
    status = _git_bytes(args.repo, 'status', '--porcelain', '-z')
    if status is None:
        errors.append('git status 讀取失敗')
    elif status.strip(b'\0'):
        errors.append('checkout 唔乾淨（preflight 需要 clean worktree）')
    if expected_head:
        if not HEX40_RE.match(expected_head):
            errors.append('expected-head 唔係完整 40-hex')
        elif head != expected_head:
            errors.append(f'HEAD {head} != expected-head {expected_head}')
    return errors, head


def _check_commit_ancestry(args, metadata):
    """metadata.commit 必須係本地完整物件＋HEAD（或 origin/master）祖先。

    同時拒絕 shallow checkout：fetch-depth 1 會令祖先驗證根本冇可能通過，
    preflight 要明確講出係歷史不足而唔係 metadata 壞。
    """
    errors = []
    shallow = _git_bytes(args.repo, 'rev-parse', '--is-shallow-repository')
    if shallow is None:
        errors.append('git shallow 狀態讀取失敗')
    elif shallow.decode('ascii', 'replace').strip() == 'true':
        errors.append('checkout 係 shallow（fetch-depth 不足）；重建需要完整歷史')
    commit = str(metadata.get('commit') or '')
    if not HEX40_RE.match(commit):
        errors.append('metadata.commit 唔係完整 40-hex')
        return errors
    if _git_bytes(args.repo, 'cat-file', '-e', commit + '^{commit}') is None:
        errors.append(f'metadata.commit 唔存在本地：{commit[:12]}')
        return errors
    if _git_bytes(args.repo, 'merge-base', '--is-ancestor', commit, 'HEAD') is None \
            and _git_bytes(args.repo, 'merge-base', '--is-ancestor', commit,
                           'origin/master') is None:
        errors.append(f'metadata.commit 唔係 HEAD／origin/master 祖先：{commit[:12]}')
    return errors


def _check_required_inputs(args):
    errors = []
    for rel in REQUIRED_PRESERVED:
        bad = _file_unsafe(args.files_root, rel)
        if bad:
            errors.append(bad)
    return errors


def _check_payload(args, metadata):
    errors = []
    bpa = _load_module('aircon_bpa_rebuild', os.path.join(SCRIPTS, 'build_pages_artifact.py'))
    try:
        payload = bpa.load_payload_manifest(args.manifest)
    except Exception as e:  # noqa: BLE001
        return [f'payload manifest 唔合格：{type(e).__name__}: {e}'], None
    if sorted(payload) != sorted(EXPECTED_PAYLOAD):
        errors.append(f'payload manifest 必須恰好係 {sorted(EXPECTED_PAYLOAD)}；'
                      f'got {sorted(payload)}')
    for rel in payload:
        bad = _file_unsafe(args.files_root, rel)
        if bad:
            errors.append(bad)
    if args.csv_rel not in payload:
        errors.append(f'payload 缺少預期 CSV：{args.csv_rel}')
    if errors:
        return errors, None
    _, csv_rel, verify_errors = bpa.verify_metadata(args.files_root, payload, args.metadata)
    errors.extend(verify_errors)
    return errors, csv_rel


def _check_receipts(args, metadata):
    """EMSD receipt／raw receipt 內部同互相 binding（唔假設欄位以外嘅嘢）。"""
    errors = []
    try:
        receipt = _load_json(args.receipt, 'emsd_receipt.json')
        raw = _load_json(args.raw_receipt, 'emsd_raw_receipt.json')
    except CheckError as e:
        return [str(e)]

    # ---- receipt 基本成功狀態 ----
    if receipt.get('success') is not True or receipt.get('aborted') is not False \
            or receipt.get('error'):
        errors.append('receipt 唔係成功（success/aborted/error）')
    raw_hash = 'sha256:' + _sha256_file(args.raw_receipt)
    if receipt.get('rawReceiptHash') != raw_hash:
        errors.append('receipt.rawReceiptHash 同 raw receipt bytes 唔一致')
    if receipt.get('retrievedAt') != raw.get('retrievedAt'):
        errors.append('receipt.retrievedAt 同 raw receipt retrievedAt 唔一致')

    # ---- raw receipt 基本形狀 ----
    if raw.get('success') is not True:
        errors.append('raw receipt success 唔係 true')
    if raw.get('schemaVersion') != 1:
        errors.append('raw receipt schemaVersion 唔係 1')
    if not _is_sha256(raw.get('archiveHash')):
        errors.append('raw receipt archiveHash 唔合格')

    # ---- 頁數／每頁列數／頁 identifiers（bool 唔算 int）----
    pages = raw.get('pages')
    per_page = raw.get('perPageRows')
    pages_ok = isinstance(pages, list) and bool(pages)
    if not pages_ok:
        errors.append('raw receipt pages 缺失或空')
    else:
        for idx, page in enumerate(pages):
            if not isinstance(page, dict):
                errors.append(f'raw receipt pages[{idx}] 唔係 object')
                continue
            if type(page.get('page')) is not int or page['page'] != idx + 1:
                errors.append(f'raw receipt pages[{idx}].page 唔係連續唯一：'
                              f'{page.get("page")!r}')
            if not _is_pos_int(page.get('byteLength')):
                errors.append(f'raw receipt pages[{idx}].byteLength 唔合格：'
                              f'{page.get("byteLength")!r}')
            if not _is_sha256(page.get('sha256')):
                errors.append(f'raw receipt pages[{idx}].sha256 形狀唔正確')
    per_page_ok = isinstance(per_page, list) and bool(per_page) \
        and all(_is_pos_int(n) for n in per_page)
    if not per_page_ok:
        errors.append('raw receipt perPageRows 唔合格')
    else:
        if raw.get('pageCount') != len(per_page):
            errors.append('raw receipt pageCount != len(perPageRows)')
        if pages_ok and len(pages) != len(per_page):
            errors.append('raw receipt pages 同 perPageRows 數量唔一致')
        if raw.get('totalRows') != sum(per_page):
            errors.append('raw receipt totalRows != sum(perPageRows)')
    if pages_ok and per_page_ok and len(pages) != len(per_page):
        errors.append('raw receipt pages／perPageRows cardinality mismatch')

    # ---- receipt ↔ raw 三向一致 ----
    if receipt.get('pagesExpected') != raw.get('pageCount') \
            or receipt.get('pagesFetched') != raw.get('pageCount'):
        errors.append('receipt pagesExpected/pagesFetched 同 raw receipt 頁數唔一致')
    if receipt.get('totalRows') != raw.get('totalRows'):
        errors.append('receipt.totalRows 同 raw receipt totalRows 唔一致')
    if receipt.get('perPageRows') != per_page:
        errors.append('receipt.perPageRows 同 raw receipt perPageRows 唔一致')
    if raw.get('datasetHash') != metadata.get('datasetHash') \
            or receipt.get('datasetHash') != metadata.get('datasetHash'):
        errors.append('receipt/raw datasetHash 同 metadata.datasetHash 唔一致')

    # ---- durable source provenance（paginated + CSV 兩條記錄）----
    by_kind = {}
    sources = raw.get('sources')
    if not isinstance(sources, list) or len(sources) != 2:
        errors.append('raw receipt sources 必須有兩條 durable source 記錄')
    else:
        for source in sources:
            if not isinstance(source, dict):
                errors.append('raw receipt source 記錄唔係 object')
                continue
            kind = source.get('sourceKind')
            if kind not in (SOURCE_KIND_PAGINATED, SOURCE_KIND_CSV) or kind in by_kind:
                errors.append(f'raw receipt sourceKind 唔符合／重複：{kind!r}')
                continue
            by_kind[kind] = source
            if source.get('persisted') is not True \
                    or source.get('verified') is not True \
                    or source.get('durableRemote') is not True:
                errors.append(f'raw receipt {kind} source 唔係 persisted/verified/durableRemote')
            if not (isinstance(source.get('adapter'), str) and source['adapter']):
                errors.append(f'raw receipt {kind} source adapter 缺失')
            if not _is_pos_int(source.get('retentionDays')):
                errors.append(f'raw receipt {kind} source retentionDays 唔合格')
            if not _is_sha256(source.get('archiveHash')):
                errors.append(f'raw receipt {kind} source archiveHash 唔合格')
        for kind in (SOURCE_KIND_PAGINATED, SOURCE_KIND_CSV):
            if kind not in by_kind:
                errors.append(f'raw receipt 缺少 {kind} source 記錄')

    paginated = by_kind.get(SOURCE_KIND_PAGINATED) or {}
    csv_source = by_kind.get(SOURCE_KIND_CSV) or {}
    if paginated:
        if paginated.get('sourceUrl') != raw.get('sourceUrl'):
            errors.append('paginated source.sourceUrl 同 raw receipt sourceUrl 唔一致')
        if paginated.get('pageCount') != raw.get('pageCount') \
                or paginated.get('totalRows') != raw.get('totalRows'):
            errors.append('paginated source 頁數／列數同 raw receipt 唔一致')
        if paginated.get('archiveHash') != raw.get('archiveHash'):
            errors.append('paginated source archiveHash 同 raw receipt archiveHash 唔一致')
    if csv_source:
        if csv_source.get('sourceUrl') != receipt.get('sourceUrl'):
            errors.append('CSV source.sourceUrl 同 receipt.sourceUrl 唔一致')
        if (csv_source.get('resolvedUrl') or csv_source.get('sourceUrl')) \
                != metadata.get('datasetSourceUrl'):
            errors.append('CSV source resolvedUrl 同 metadata.datasetSourceUrl 唔一致')
        if not _is_sha256(csv_source.get('sha256')):
            errors.append('CSV source sha256 形狀唔正確')
        if not _is_pos_int(csv_source.get('byteLength')):
            errors.append('CSV source byteLength 唔合格')

    # ---- privateArchive（paginated bytes 嘅持久保存記錄）----
    private = raw.get('privateArchive')
    if not isinstance(private, dict):
        errors.append('raw receipt privateArchive 缺失或唔係 object')
    else:
        if private.get('persisted') is not True or private.get('verified') is not True \
                or private.get('durableRemote') is not True:
            errors.append('privateArchive 唔係 persisted/verified/durableRemote')
        if private.get('archiveHash') != raw.get('archiveHash'):
            errors.append('privateArchive archiveHash 同 raw receipt archiveHash 唔一致')
        if not _is_pos_int(private.get('retentionDays')):
            errors.append('privateArchive retentionDays 唔合格')

    # ---- receipt.dualSource 同 raw sources 互相 binding ----
    dual = receipt.get('dualSource')
    if not isinstance(dual, dict):
        errors.append('receipt 缺少 dualSource（重建需要雙來源 provenance）')
    else:
        if dual.get('equal') is not True:
            errors.append('receipt.dualSource.equal 唔係 true')
        if dual.get('retrievedAt') != raw.get('retrievedAt'):
            errors.append('dualSource.retrievedAt 同 raw receipt retrievedAt 唔一致')
        dual_sources = dual.get('sources')
        if not isinstance(dual_sources, dict) \
                or SOURCE_KIND_PAGINATED not in dual_sources \
                or SOURCE_KIND_CSV not in dual_sources:
            errors.append('dualSource.sources 缺少兩個 sourceKind')
        else:
            for kind, record in ((SOURCE_KIND_PAGINATED, paginated),
                                 (SOURCE_KIND_CSV, csv_source)):
                archive = (dual_sources.get(kind) or {}).get('archive')
                if not isinstance(archive, dict):
                    errors.append(f'dualSource.{kind}.archive 缺失或唔係 object')
                    continue
                if archive.get('archiveHash') != record.get('archiveHash'):
                    errors.append(f'dualSource.{kind} archiveHash 同 raw source 唔一致')
                if archive.get('persisted') is not True \
                        or archive.get('verified') is not True \
                        or archive.get('durableRemote') is not True:
                    errors.append(f'dualSource.{kind} archive 唔係 persisted/verified/durableRemote')
                if not _is_pos_int(archive.get('retentionDays')):
                    errors.append(f'dualSource.{kind} archive retentionDays 唔合格')
            dual_csv = dual_sources.get(SOURCE_KIND_CSV) or {}
            dual_pag = dual_sources.get(SOURCE_KIND_PAGINATED) or {}
            if dual_csv.get('sha256') != csv_source.get('sha256') \
                    or dual_csv.get('byteLength') != csv_source.get('byteLength'):
                errors.append('dualSource CSV sha256／byteLength 同 raw CSV source 唔一致')
            if dual_pag.get('pageCount') != paginated.get('pageCount') \
                    or dual_pag.get('totalRows') != paginated.get('totalRows'):
                errors.append('dualSource paginated 頁數／列數同 raw source 唔一致')
            if dual_pag.get('sourceUrl') != paginated.get('sourceUrl'):
                errors.append('dualSource paginated sourceUrl 同 raw source 唔一致')
    return errors


def _collect_facts(metadata):
    return {field: metadata[field] for field in FACT_FIELDS if field in metadata}


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
    missing = [f for f in REQUIRED_FACT_FIELDS if f not in metadata]
    if missing:
        errors.append(f'metadata 缺少 acquisition facts：{missing}')
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
        if metadata.get('rawRecordCount') is not None \
                and metadata.get('rawRecordCount') != facts.get('rawRecordCount'):
            errors.append('metadata.rawRecordCount != receipt facts')
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
        return errors + [str(e)]
    try:
        batch = _load_module('aircon_batch_rebuild', os.path.join(BASE, 'batch_utils.py'))
        batch._validate_meta(meta, os.path.basename(args.price_meta))
    except Exception as e:  # noqa: BLE001
        return errors + [f'prices_meta.json 契約錯誤：{type(e).__name__}: {e}']
    start = meta.get('price_batch_start')
    idx = meta.get('price_batch_idx', 0)
    if start and isinstance(idx, int) and not isinstance(idx, bool) \
            and idx < batch.PRICE_BATCH_DAYS:
        errors.append('price stage 仍然 active：rebuild 模式只可喺 inactive 時執行')
    return errors


def _hash_required(args):
    files = {}
    for rel in REQUIRED_PRESERVED:
        bad = _file_unsafe(args.files_root, rel)
        if bad:
            raise CheckError(f'required input 唔合格：{bad}')
        files[rel] = 'sha256:' + _sha256_file(os.path.join(args.files_root, *rel.split('/')))
    return files


def _invalidate_baseline(args, reason):
    """把同一路徑嘅舊 baseline 失效化（只喺 repo 外）；回傳錯誤字串或 None。"""
    try:
        path = _resolve_out(args.baseline_out, args.repo, args.allow_repo_path)
        marker = {'schemaVersion': SCHEMA_VERSION, 'invalidated': True,
                  'reason': reason, 'createdAt': _utc_now_text()}
        _atomic_write_json(path, marker)
        return None
    except (CheckError, OSError) as e:  # noqa: BLE001
        return f'舊 baseline 無法失效化：{type(e).__name__}: {e}'


# ---------------------------------------------------------------- baseline schema


def _validate_baseline(baseline):
    """嚴格驗證 baseline；empty／incomplete／traversal／壞 hash 一律失敗。"""
    errors = []
    if not isinstance(baseline, dict):
        return ['baseline 必須係 JSON object']
    if baseline.get('schemaVersion') != SCHEMA_VERSION:
        errors.append(f'baseline.schemaVersion 必須係 {SCHEMA_VERSION}')
    if baseline.get('invalidated') is True:
        errors.append(f"baseline 已被失效化（reason={baseline.get('reason')!r}）")
    created = baseline.get('createdAt')
    if not isinstance(created, str):
        errors.append('baseline.createdAt 缺失或唔係字串')
    else:
        try:
            _parse_utc(created, 'baseline.createdAt')
        except CheckError as e:
            errors.append(str(e))
    head = baseline.get('head')
    if not (isinstance(head, str) and HEX40_RE.match(head)):
        errors.append('baseline.head 唔係完整 40-hex')
    commit = baseline.get('metadataCommit')
    if not (isinstance(commit, str) and HEX40_RE.match(commit)):
        errors.append('baseline.metadataCommit 唔係完整 40-hex')

    facts = baseline.get('facts')
    if not isinstance(facts, dict):
        errors.append('baseline.facts 缺失或唔係 object')
    else:
        for field in REQUIRED_FACT_FIELDS:
            if field not in facts:
                errors.append(f'baseline.facts 缺少 {field}')
        if 'datasetDate' in facts and not (isinstance(facts['datasetDate'], str)
                                           and DATE_RE.match(facts['datasetDate'])):
            errors.append('baseline.facts.datasetDate 形狀唔正確')
        if 'datasetDateBasis' in facts and facts['datasetDateBasis'] not in DATE_BASIS_ENUM:
            errors.append('baseline.facts.datasetDateBasis 唔係允許值')
        if 'datasetRetrievedAt' in facts:
            try:
                _parse_utc(facts['datasetRetrievedAt'], 'baseline.facts.datasetRetrievedAt')
            except CheckError as e:
                errors.append(str(e))
        if 'datasetHash' in facts and not _is_sha256(facts['datasetHash']):
            errors.append('baseline.facts.datasetHash 唔合格')
        if 'datasetSourceUrl' in facts:
            url = facts['datasetSourceUrl']
            if not (isinstance(url, str) and url.startswith('https://www.emsd.gov.hk/')):
                errors.append('baseline.facts.datasetSourceUrl 唔喺批准範圍')
        if 'datasetSnapshotId' in facts and not (isinstance(facts['datasetSnapshotId'], str)
                                                 and facts['datasetSnapshotId']):
            errors.append('baseline.facts.datasetSnapshotId 唔合格')
        if 'recordCount' in facts and not _is_pos_int(facts['recordCount']):
            errors.append('baseline.facts.recordCount 唔合格')
        for field in ('rawRecordCount', 'registrationCount', 'modelCount'):
            if field in facts and not _is_nonneg_int(facts[field]):
                errors.append(f'baseline.facts.{field} 唔合格')

    files = baseline.get('files')
    if not isinstance(files, dict):
        errors.append('baseline.files 缺失或唔係 object')
    else:
        extra = sorted(set(files) - set(REQUIRED_PRESERVED))
        missing = sorted(set(REQUIRED_PRESERVED) - set(files))
        if extra:
            errors.append(f'baseline.files 有唔應該存在嘅 entries：{extra}')
        if missing:
            errors.append(f'baseline.files 漏咗 required entries：{missing}')
        for rel, value in files.items():
            if rel not in REQUIRED_PRESERVED:
                continue
            if os.path.isabs(rel) or '..' in rel.split('/') or not rel:
                errors.append(f'baseline.files 有不安全路徑：{rel!r}')
            if not _is_sha256(value):
                errors.append(f'baseline.files[{rel}] hash 形狀唔正確')
    return errors


# ---------------------------------------------------------------- commands


def run_preflight(args):
    now_dt = _parse_utc(args.now, '--now') if args.now else datetime.now(timezone.utc)
    checks = []

    def rec(name, errors, detail=None):
        checks.append({'check': name, 'pass': not errors,
                       'errors': list(errors), 'detail': detail})

    errors, head = _check_git(args, args.expected_head)
    rec('git-clean-and-head', errors, head)

    errors = _check_required_inputs(args)
    rec('required-inputs-present', errors, {'count': len(REQUIRED_PRESERVED)})

    metadata = None
    facts = None
    try:
        metadata = _load_json(args.metadata, 'metadata.json')
    except CheckError as e:
        rec('metadata-load', [str(e)])
    else:
        rec('metadata-load', [])
        errors = _check_commit_ancestry(args, metadata)
        rec('metadata-commit-ancestry', errors, {'commit': metadata.get('commit')})
        errors, csv_rel = _check_payload(args, metadata)
        rec('payload-and-metadata-contract', errors, {'csvRel': csv_rel})
        if csv_rel and csv_rel != args.csv_rel:
            rec('payload-csv-path', [f'payload CSV {csv_rel} != 預期 {args.csv_rel}'])
        errors = _check_receipts(args, metadata)
        rec('receipt-raw-binding', errors)
        errors, facts = _check_receipt_facts(args, metadata, now_dt)
        rec('receipt-facts', errors,
            None if not facts else {'datasetDate': facts.get('datasetDate'),
                                    'datasetRetrievedAt': facts.get('datasetRetrievedAt'),
                                    'datasetHash': facts.get('datasetHash')})
        if facts:
            errors = _check_age(facts, now_dt)
            rec('age-within-72h', errors, {'now': now_dt.strftime('%Y-%m-%dT%H:%M:%SZ')})
    errors = _check_price_stage(args)
    rec('price-stage-inactive-force-false', errors)

    ok = all(c['pass'] for c in checks)
    baseline_state = {'written': False, 'invalidated': False, 'path': None}
    if ok:
        try:
            files = _hash_required(args)
            baseline = {'schemaVersion': SCHEMA_VERSION, 'createdAt': _utc_now_text(),
                        'head': head, 'metadataCommit': metadata.get('commit'),
                        'facts': _collect_facts(metadata), 'files': files}
            baseline_errors = _validate_baseline(baseline)
            if baseline_errors:
                raise CheckError('baseline 自我驗證失敗：' + '；'.join(baseline_errors))
            baseline_path = _resolve_out(args.baseline_out, args.repo, args.allow_repo_path)
            _atomic_write_json(baseline_path, baseline)
            baseline_state.update({'written': True, 'path': os.path.basename(baseline_path)})
            rec('baseline-written', [], {'files': len(files)})
        except CheckError as e:
            rec('baseline-written', [str(e)])
            note = _invalidate_baseline(args, 'baseline-build-failed')
            rec('baseline-invalidated', [note] if note else [])
            baseline_state.update({'invalidated': True, 'path': args.baseline_out})
    else:
        note = _invalidate_baseline(args, 'preflight-checks-failed')
        rec('baseline-invalidated', [note] if note else [])
        baseline_state.update({'invalidated': True, 'path': args.baseline_out})

    ok = all(c['pass'] for c in checks)
    report = {'schemaVersion': SCHEMA_VERSION, 'mode': 'preflight', 'ok': ok,
              'checks': checks, 'baseline': baseline_state,
              'snapshot': (None if not metadata else {
                  'metadataCommit': metadata.get('commit'),
                  'datasetDate': metadata.get('datasetDate'),
                  'datasetRetrievedAt': metadata.get('datasetRetrievedAt'),
                  'datasetHash': metadata.get('datasetHash'),
                  'releasePayloadHash': metadata.get('releasePayloadHash'),
              })}
    _write_report(args, report)
    return 0 if ok else 1


def _check_git_changes(args, baseline):
    """worktree 同 index 改動只准係三個 generated outputs；git 失敗即 fail。"""
    errors = []
    head = _git_text(args.repo, 'rev-parse', 'HEAD')
    if not head or not HEX40_RE.match(head):
        errors.append('git HEAD 讀取失敗或唔係完整 40-hex')
    elif head != baseline.get('head'):
        errors.append(f'HEAD {head} != baseline {baseline.get("head")}')
    raw = _git_bytes(args.repo, 'status', '--porcelain', '-z')
    if raw is None:
        return errors + ['git status 讀取失敗'], []
    try:
        entries = _parse_porcelain_z(raw)
    except CheckError as e:
        return errors + [str(e)], []
    changed = []
    for status, path, src in entries:
        if 'R' in status or 'C' in status:
            errors.append(f'唔准 rename/copy：{status} {src} -> {path}')
            continue
        if any(ch in status for ch in 'ADTU'):
            errors.append(f'唔准 {status} 改動：{path}')
            continue
        if path not in ALLOWED_GENERATED:
            errors.append(f'generated outputs 以外嘅改動：{status} {path}')
            continue
        changed.append(path)
    return errors, changed


def _check_metadata_contract(args, baseline):
    errors = []
    meta = None
    try:
        vm = _load_module('aircon_validate_metadata_rebuild',
                          os.path.join(SCRIPTS, 'validate_metadata.py'))
        schema = vm._load_schema()
        if schema is None:
            return ['治理 Schema 提取失敗'], None
        meta = _load_json(os.path.join(args.files_root, 'metadata.json'), 'metadata.json')
        errors.extend(f'metadata schema: {e}' for e in vm.validate(meta, schema))
    except CheckError as e:
        return [str(e)], None
    if errors:
        return errors, meta
    for field, expected in (baseline.get('facts') or {}).items():
        if meta.get(field) != expected:
            errors.append(f'重建後 metadata.{field} 改變：baseline={expected!r} '
                          f'now={meta.get(field)!r}')
    return errors, meta


def run_guard(args):
    checks = []

    def rec(name, errors, detail=None):
        checks.append({'check': name, 'pass': not errors,
                       'errors': list(errors), 'detail': detail})

    baseline = None
    try:
        baseline = _load_json(args.baseline, 'baseline')
        rec('baseline-load', [])
    except CheckError as e:
        rec('baseline-load', [str(e)])
    schema_errors = _validate_baseline(baseline) if baseline is not None else ['baseline 缺失']
    rec('baseline-schema', schema_errors)

    if baseline is not None and not schema_errors:
        errors, changed = _check_git_changes(args, baseline)
        rec('git-changes-limited-to-generated', errors, {'changed': changed})

        changed_files = []
        for rel, want in (baseline.get('files') or {}).items():
            path = os.path.join(args.files_root, *rel.split('/'))
            if os.path.islink(path):
                changed_files.append(f'{rel}（symlink）')
                continue
            if not os.path.isfile(path):
                changed_files.append(f'{rel}（缺檔）')
                continue
            got = 'sha256:' + _sha256_file(path)
            if got != want:
                changed_files.append(rel)
        rec('preserved-sources-unchanged', changed_files)

        missing = []
        for rel in ALLOWED_GENERATED:
            path = os.path.join(args.files_root, rel)
            if not os.path.isfile(path) or os.path.islink(path):
                missing.append(rel)
        rec('generated-present', missing)

        errors, meta = _check_metadata_contract(args, baseline)
        rec('generated-metadata-contract', errors,
            None if not meta else {'version': meta.get('version'),
                                   'datasetDate': meta.get('datasetDate')})

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
