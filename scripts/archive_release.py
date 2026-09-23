#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""長期可審計發佈歸檔（GATE-09）

把一次部署嘅 Web／PDF／CSV／metadata／manifest／payload、測試同部署後報告，
連同逐檔 SHA-256、可追溯 provenance 打包成不可變歸檔目錄 + zip。

事實分離（唔可以假裝相同）：
  - `archiveCommit`   = 今次歸檔所 checkout 嘅不可變 commit（workflow HEAD）；
  - `sourceCommit`    = `metadata.json.commit`，實際產生資料嘅輸入 commit；
  - `deploymentCommit`= 平台 deployment 記錄嘅 commit（如有提供）。

歸檔等級：
  - `draft`（預設）：資產完整但報告未必齊；唔可以當 GATE-09 發布完成；
  - `release`（--release）：必需要求機器可讀報告齊全兼有效（feature-check ok、
    postdeploy ok、pytest JUnit 無 fail/error、schema/data/metadata 報告非空）。

不變式：
  - 歸檔前重驗 metadata 完整 Schema、tag 同名版本、payload hash；
  - 同一 tag 重跑：目錄同 zip 都要存在、實際 bytes 同新歸檔一致、CHECKSUMS
    同實際檔案一致、zip entries 無重複／缺漏；任何一項唔同即拒絕（禁止 clobber）；
  - 發佈前掃描歸檔資產私隱（只報 rule/path/count）；
  - 目錄與 zip 以 staging 建立、最後原子換名；失敗即回滾並明確報錯。
"""
import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
from validate_metadata import validate  # noqa: E402
from extract_governance import extract_blocks, GOV_FILE  # noqa: E402
import check_public_privacy  # noqa: E402

SEMVER_TAG_RE = re.compile(
    r'^v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    r'(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)'
    r'(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?'
    r'(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$')
_VOLATILE = ('PROVENANCE.json', 'CHECKSUMS.sha256')
# release 歸檔最少要齊嘅機器證據
REQUIRED_RELEASE_REPORTS = (
    'acceptance.json', 'feature-check.json', 'postdeploy.json', 'pytest-junit.xml',
)
# acceptance runner gate ID → canonical argv（must exactly once、rc=0、log hash 符）
ACCEPTANCE_ARGV = {
    'GOVERNANCE_EXTRACT': ['python', 'scripts/extract_governance.py'],
    'VALIDATE_DATA': ['python', 'validate_data.py'],
    'VALIDATE_METADATA': ['python', 'scripts/validate_metadata.py'],
    'PRIVACY_WORKTREE': ['python', 'scripts/check_public_privacy.py', '--mode', 'worktree'],
    'PYTEST': ['python', '-m', 'pytest', 'tests/', '-q'],
    'FEATURE_CHECK': ['python', 'scripts/feature-check.py', '--run-tests'],
    'DIFF_CHECK': ['git', 'diff', '--check'],
}
REQUIRED_ACCEPTANCE_GATES = (
    'GOVERNANCE_EXTRACT', 'VALIDATE_DATA', 'VALIDATE_METADATA', 'PRIVACY_WORKTREE',
    'PYTEST', 'FEATURE_CHECK',
)
REQUIRED_POSTDEPLOY_CHECKS = {
    'metadata.http_200', 'metadata.full_object_equal', 'metadata.online_schema_valid',
    'payload.releasePayloadHash', 'payload.csv_datasetHash', 'payload.pdf_matches_metadata',
}
REQUIRED_POSTDEPLOY_BROWSER = {
    'browser.version_from_metadata', 'browser.last_deploy_hkt', 'browser.dataset_date',
    'browser.compare_modal', 'browser.responsive_no_overflow',
}
REQUIRED_PAYLOAD_SUFFIXES = ('.html', '.pdf', '.csv')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _load_gen_metadata():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'aircon_gen_metadata', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_tag(tag):
    """嚴格 SemVer（可帶 v）；拒絕換行、控制字、路徑分隔、空白。"""
    if not isinstance(tag, str) or not tag:
        return False
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7f for c in tag):
        return False
    if any(c in tag for c in '/\\'):
        return False
    return bool(SEMVER_TAG_RE.match(tag))


def tag_matches_version(tag, version):
    """tag（去掉單一 v 前綴）必須同 metadata.version 逐字相同（1.2.9-test ≠ 1.2.9）。"""
    if not isinstance(tag, str) or not isinstance(version, str):
        return False
    bare = tag[1:] if tag.startswith('v') else tag
    return bare == version


def verify_metadata(meta_path, manifest_path, artifacts_dir, source_commit=None, build=None):
    """重驗 metadata（完整 Schema + payload hash + manifest 路徑契約）。"""
    errors = []
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    with open(meta_path, encoding='utf-8') as f:
        meta = json.load(f)
    errors.extend(f'metadata schema: {e}' for e in validate(meta, schema))
    if source_commit and meta.get('commit') != source_commit:
        errors.append(f"metadata.commit {meta.get('commit')} != --source-commit {source_commit}")
    if build and meta.get('build') != build:
        errors.append(f"metadata.build {meta.get('build')} != --build {build}")
    try:
        gen = _load_gen_metadata()
        with open(manifest_path, encoding='utf-8') as f:
            spec = json.load(f)
        files = spec.get('files')
        if not isinstance(files, list) or not files:
            raise ValueError('manifest files 必須係非空陣列')
        gen.validate_manifest_files(files, base=artifacts_dir)
        computed = gen.hash_files(files, base=artifacts_dir)
    except Exception as e:  # noqa: BLE001
        errors.append(f'manifest／payload 驗證失敗：{e}')
        return errors
    if computed != meta.get('releasePayloadHash'):
        errors.append(f"releasePayloadHash 唔一致：manifest={computed} metadata={meta.get('releasePayloadHash')}")
    suffixes = {os.path.splitext(f)[1].lower() for f in files}
    missing = [s for s in REQUIRED_PAYLOAD_SUFFIXES if s not in suffixes]
    if missing:
        errors.append(f'payload manifest 缺少必需類型：{missing}')
    return errors


def _collect_files(artifacts_dir, manifest, reports_dir):
    """回傳 [(歸檔內相對路徑, 真實來源路徑)]；reports 來源必須喺 reports_dir。"""
    entries = []
    extra = ['metadata.json', 'deploy_payload.json']
    if os.path.isfile(os.path.join(artifacts_dir, 'deploy_envelope.json')):
        extra.append('deploy_envelope.json')
    for rel in list(dict.fromkeys(manifest['files'])) + extra:
        entries.append((rel.replace('\\', '/'), os.path.join(artifacts_dir, rel)))
    # 公開 sidecar：只有同本次 metadata／CSV 一致才歸檔，避免舊 raw receipt 被誤當本次。
    meta = None
    try:
        with open(os.path.join(artifacts_dir, 'metadata.json'), encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = None
    if meta is not None:
        for rel in ('emsd_receipt.json', 'emsd_raw_receipt.json', 'official_batch_status.json'):
            src = os.path.join(artifacts_dir, rel)
            if not os.path.isfile(src):
                continue
            if rel != 'official_batch_status.json':
                try:
                    side = json.load(open(src, encoding='utf-8'))
                    if side.get('success') is not True or side.get('datasetHash') != meta.get('datasetHash'):
                        continue
                except (OSError, ValueError):
                    continue
            entries.append((rel, src))
    changelog = os.path.join(artifacts_dir, 'CHANGELOG.md')
    if os.path.isfile(changelog):
        entries.append(('CHANGELOG.md', changelog))
    if reports_dir and os.path.isdir(reports_dir):
        for root, _dirs, names in os.walk(reports_dir):
            for name in sorted(names):
                src = os.path.join(root, name)
                rel = os.path.relpath(src, reports_dir).replace('\\', '/')
                entries.append((f'reports/{rel}', src))
    missing = [rel for rel, src in entries if not os.path.isfile(src)]
    if missing:
        raise FileNotFoundError('歸檔缺少檔案：' + '、'.join(missing))
    return entries


def _hash_dir(root):
    out = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace('\\', '/')
            out[rel] = sha256_file(full)
    return out


def _zip_entries_list(zip_path):
    """回 zip 內 entry 名列表；壞 zip raise。"""
    with zipfile.ZipFile(zip_path) as z:
        if z.testzip() is not None:
            raise ValueError('zip CRC 校驗失敗')
        names = [i.filename.replace('\\', '/') for i in z.infolist() if not i.is_dir()]
    if len(names) != len(set(names)):
        raise ValueError('zip 內有重複 entry')
    return names


def _zip_checksums(zip_path):
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            data = z.read(info.filename)
            out[info.filename.replace('\\', '/')] = hashlib.sha256(data).hexdigest()
    return out


def _strip_volatile(d):
    return {k: v for k, v in d.items() if k not in _VOLATILE}


def same_content(existing, new):
    return _strip_volatile(existing) == _strip_volatile(new)


def _provenance_equal(a, b):
    """除 archivedAt 外所有 provenance facts 必須一致。"""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    ignore = {'archivedAt'}
    keys = (set(a) | set(b)) - ignore
    return all(a.get(k) == b.get(k) for k in keys)


def _parse_checksums(path):
    out = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('  ', 1)
            if len(parts) != 2:
                raise ValueError(f'CHECKSUMS 行格式錯：{line!r}')
            digest, rel = parts
            if rel in out:
                raise ValueError(f'CHECKSUMS 有重複路徑：{rel}')
            out[rel] = digest
    return out


def existing_is_consistent(target_dir, target_zip, new_checksums, new_provenance):
    """已存在歸檔必須：實際目錄 bytes、CHECKSUMS、zip entries 同新歸檔逐一一致。"""
    # 1) 目錄實際 bytes（唔可以只信 CHECKSUMS 檔）
    actual = _hash_dir(target_dir)
    if _strip_volatile(actual) != _strip_volatile(new_checksums):
        return False, '目錄實際檔案集合／bytes 同新歸檔唔一致'
    # 2) CHECKSUMS 同實際 bytes 自洽（捉篡改 CHECKSUMS／檔案）
    try:
        declared = _parse_checksums(os.path.join(target_dir, 'CHECKSUMS.sha256'))
    except (OSError, ValueError) as e:
        return False, f'CHECKSUMS 無法讀取：{e}'
    if declared != {k: v for k, v in actual.items() if k != 'CHECKSUMS.sha256'}:
        return False, 'CHECKSUMS 同目錄實際 bytes 唔一致（可能被篡改）'
    # 3) zip entries：無重複、集合完全一致、bytes 一致
    try:
        zip_names = _zip_entries_list(target_zip)
        zip_hash = _zip_checksums(target_zip)
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return False, f'zip 無法讀取：{e}'
    if set(zip_names) != set(actual):
        return False, 'zip entries 同目錄檔案集合唔一致（有多餘／缺漏）'
    if _strip_volatile(zip_hash) != _strip_volatile(actual):
        return False, 'zip 內 bytes 同目錄 bytes 唔一致'
    # 4) provenance（除 archivedAt 外逐欄一致）
    try:
        old_prov = json.load(open(os.path.join(target_dir, 'PROVENANCE.json'), encoding='utf-8'))
        zip_prov = json.loads(zipfile.ZipFile(target_zip).read('PROVENANCE.json').decode('utf-8'))
    except Exception as e:  # noqa: BLE001
        return False, f'PROVENANCE 無法讀取：{e}'
    if not _provenance_equal(old_prov, new_provenance):
        return False, 'PROVENANCE facts 同新歸檔唔一致（commit/run/build 等）'
    if not _provenance_equal(zip_prov, new_provenance):
        return False, 'zip 內 PROVENANCE facts 同新歸檔唔一致'
    return True, ''


def safe_report_path(reports_dir, rel):
    """只接受 reports 根內嘅 canonical POSIX 相對路徑；拒絕逃逸／symlink。

    - 非空字串、無 NUL、無 backslash（避免 Windows ambiguity）、無 drive/UNC；
    - 每段唔可以係 ''／'.'／'..'，normpath 後必須同原本一致；
    - 任何一段（包括父層）係 symlink 即拒絕；realpath 必須仍喺 reports 根內；
    - 檔案必須實際存在（唔可以用外部 bytes 只驗 hash）。
    """
    if not isinstance(rel, str) or not rel:
        raise ValueError('report path 必須係非空字串')
    if '\x00' in rel:
        raise ValueError('report path 唔可以有 NUL')
    if '\\' in rel:
        raise ValueError(f'report path 唔可以有 backslash：{rel!r}')
    if posixpath.isabs(rel) or re.match(r'^[A-Za-z]:', rel):
        raise ValueError(f'report path 必須係相對路徑：{rel!r}')
    parts = rel.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError(f'report path 有非法段：{rel!r}')
    if posixpath.normpath(rel) != rel:
        raise ValueError(f'report path 唔係 canonical：{rel!r}')
    root = os.path.realpath(reports_dir)
    cur = reports_dir
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise ValueError(f'report path 有 symlink：{rel!r}')
    full = os.path.join(reports_dir, *parts)
    if not os.path.isfile(full):
        raise FileNotFoundError(f'report path 唔存在：{rel!r}')
    real = os.path.realpath(full)
    if os.path.commonpath([root, real]) != root:
        raise ValueError(f'report path 逃逸 reports root：{rel!r}')
    return full


def _find_exactly_one(reports_dir, name):
    """required machine report 必須 exactly one；duplicate／巢狀冒充拒絕。"""
    hits = []
    for root, _dirs, files in os.walk(reports_dir):
        if name in files:
            rel = os.path.relpath(os.path.join(root, name), reports_dir).replace('\\', '/')
            hits.append(rel)
    if len(hits) != 1:
        raise ValueError(f'{name} 必須 exactly one（got {len(hits)}：{hits}）')
    return hits[0]


def _load_report(reports_dir, name):
    rel = _find_exactly_one(reports_dir, name)
    path = safe_report_path(reports_dir, rel)
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return rel, data


_TS_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def _validate_acceptance(reports_dir, errors, expected_commit=None):
    try:
        rel, rep = _load_report(reports_dir, 'acceptance.json')
    except (OSError, ValueError, json.JSONDecodeError) as e:
        errors.append(f'acceptance.json 無效：{e}')
        return
    if not isinstance(rep, dict):
        errors.append('acceptance.json 必須係 object')
        return
    if rep.get('schemaVersion') != 1 or isinstance(rep.get('schemaVersion'), bool):
        errors.append('acceptance.schemaVersion 必須係 1')
    if rep.get('runner') != 'scripts/run_acceptance.py':
        errors.append(f"acceptance.runner 唔正確：{rep.get('runner')!r}")
    if rep.get('ok') is not True:
        errors.append('acceptance.ok 必須係 exact true')
    commit = rep.get('commit')
    if not isinstance(commit, str) or not re.match(r'^[0-9a-f]{40}$', commit):
        errors.append(f'acceptance.commit 必須係 40 hex：{commit!r}')
    elif expected_commit and commit != expected_commit:
        errors.append(f'acceptance.commit {commit} 唔等於 archiveCommit {expected_commit}')
    for field in ('startedAt', 'finishedAt'):
        if not isinstance(rep.get(field), str) or not _TS_RE.match(rep[field]):
            errors.append(f'acceptance.{field} 格式唔正確：{rep.get(field)!r}')
    gates = rep.get('gates')
    if not isinstance(gates, list):
        errors.append('acceptance.gates 必須係 array')
        return
    ids = [g.get('id') for g in gates if isinstance(g, dict)]
    if len(ids) != len(set(ids)):
        errors.append('acceptance.gates 有 duplicate ID')
    unknown = [g for g in ids if g not in ACCEPTANCE_ARGV]
    if unknown:
        errors.append(f'acceptance.gates 有 unknown gate：{unknown}')
    for gid in REQUIRED_ACCEPTANCE_GATES:
        matches = [g for g in gates if isinstance(g, dict) and g.get('id') == gid]
        if len(matches) != 1:
            errors.append(f'acceptance gate {gid} 必須 exactly once（got {len(matches)}）')
            continue
        g = matches[0]
        rc = g.get('returncode')
        if not (isinstance(rc, int) and not isinstance(rc, bool) and rc == 0):
            errors.append(f'acceptance gate {gid} rc != 0（{rc!r}）')
        if g.get('argv') != ACCEPTANCE_ARGV[gid]:
            errors.append(f'acceptance gate {gid} argv 唔符 allowlist：{g.get("argv")}')
        try:
            log_path = safe_report_path(reports_dir, g.get('log'))
        except (ValueError, OSError) as e:
            errors.append(f'acceptance gate {gid} log path 無效：{e}')
            continue
        if sha256_file(log_path) != g.get('logSha256'):
            errors.append(f'acceptance gate {gid} log hash 唔符（tamper？）')
        if gid == 'PYTEST':
            try:
                junit_path = safe_report_path(reports_dir, g.get('junit'))
            except (ValueError, OSError) as e:
                errors.append(f'acceptance PYTEST junit path 無效：{e}')
                continue
            junit_rel = os.path.relpath(junit_path, reports_dir).replace('\\', '/')
            try:
                all_junit = _find_exactly_one(reports_dir, 'pytest-junit.xml')
            except ValueError as e:
                errors.append(str(e))
                continue
            if junit_rel != all_junit:
                errors.append(f'acceptance junit 唔等於唯一 junit 檔：{junit_rel} vs {all_junit}')
                continue
            if sha256_file(junit_path) != g.get('junitSha256'):
                errors.append('acceptance PYTEST junit hash 唔符（tamper？）')
                continue
            try:
                root_el = ET.parse(junit_path).getroot()
                n = bad = 0
                for c in root_el.iter('testcase'):
                    n += 1
                    if c.find('failure') is not None or c.find('error') is not None:
                        bad += 1
                if n == 0:
                    errors.append('pytest-junit.xml 冇 testcase')
                if bad:
                    errors.append(f'pytest-junit.xml 有 {bad} 個 failure/error')
            except (ET.ParseError, OSError) as e:
                errors.append(f'pytest-junit.xml 無法解析：{e}')


def _validate_feature_report(reports_dir, errors):
    try:
        _rel, d = _load_report(reports_dir, 'feature-check.json')
    except (OSError, ValueError, json.JSONDecodeError) as e:
        errors.append(f'feature-check.json 無效：{e}')
        return
    if not isinstance(d, dict) or d.get('ok') is not True:
        errors.append('feature-check.json ok != true')
        return
    coll = d.get('collection') or {}
    matched = coll.get('matchedNodes')
    if not isinstance(matched, list) or not matched:
        errors.append('feature-check.json required selection 為空')
    if coll.get('deselected'):
        errors.append('feature-check.json 有 deselected 節點')
    for binding, entry in (d.get('bindings') or {}).items():
        if not isinstance(entry, dict) or entry.get('status') != 'passed':
            errors.append(f'feature-check binding 未 passed：{binding}')
    if d.get('failures'):
        errors.append('feature-check.json 有 failures')
    run = d.get('run') or {}
    if run.get('exitStatus') != 0:
        errors.append('feature-check run exitStatus != 0')
    for node, entry in (run.get('tests') or {}).items():
        if not isinstance(entry, dict) or entry.get('result') != 'passed':
            errors.append(f'feature-check run node 未 passed：{node}')


def _validate_postdeploy_report(reports_dir, errors):
    try:
        _rel, d = _load_report(reports_dir, 'postdeploy.json')
    except (OSError, ValueError, json.JSONDecodeError) as e:
        errors.append(f'postdeploy.json 無效：{e}')
        return
    if not isinstance(d, dict) or d.get('ok') is not True or d.get('failures'):
        errors.append('postdeploy.json 唔係有效通過報告（ok/failures）')
        return
    passed = {c.get('check') for c in (d.get('checks') or [])
              if isinstance(c, dict) and c.get('pass') is True}
    missing = sorted(REQUIRED_POSTDEPLOY_CHECKS - passed)
    if missing:
        errors.append(f'postdeploy.json 缺 required checks：{missing}')
    browser = {c.get('check') for c in (d.get('browser') or [])
               if isinstance(c, dict) and c.get('pass') is True}
    missing_b = sorted(REQUIRED_POSTDEPLOY_BROWSER - browser)
    if missing_b:
        errors.append(f'postdeploy.json 缺 required browser evidence：{missing_b}')


def verify_release_reports(reports_dir, expected_commit=None):
    """release 歸檔要求：machine acceptance + 專屬報告結構驗證（拒絕任意文字／路徑逃逸）。"""
    errors = []
    if not reports_dir or not os.path.isdir(reports_dir):
        return ['release 歸檔需要 reports-dir'] + [f'缺少報告：{r}' for r in REQUIRED_RELEASE_REPORTS]
    _validate_acceptance(reports_dir, errors, expected_commit)
    _validate_feature_report(reports_dir, errors)
    _validate_postdeploy_report(reports_dir, errors)
    return errors


def _build_staging(tag, args, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=f'.staging-{tag}-', dir=out_dir)
    try:
        return _build_staging_inner(tag, args, staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _build_staging_inner(tag, args, staging):
    root = os.path.join(staging, 'archive')
    os.makedirs(root)
    entries = _collect_files(args.artifacts_dir, args.manifest_data, args.reports_dir)
    checksums = {}
    for rel, src in entries:
        dst = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        checksums[rel.replace('\\', '/')] = sha256_file(dst)
    provenance = {
        'tag': tag,
        'archiveType': args.archive_type,
        'version': args.metadata.get('version'),
        'build': args.metadata.get('build'),
        'archiveCommit': args.archive_commit,
        'sourceCommit': args.source_commit or args.metadata.get('commit'),
        'deploymentCommit': args.deployment_commit,
        'workflowRunId': args.run_id or args.metadata.get('workflowRunId'),
        'deploySha': args.deploy_sha,
        'deployTime': args.metadata.get('deployTime'),
        'datasetDate': args.metadata.get('datasetDate'),
        'datasetRetrievedAt': args.metadata.get('datasetRetrievedAt'),
        'datasetHash': args.metadata.get('datasetHash'),
        'releasePayloadHash': args.metadata.get('releasePayloadHash'),
        'archivedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'tool': 'scripts/archive_release.py',
        'notes': ('archiveCommit = 今次歸檔 checkout 嘅不可變 commit；'
                  'sourceCommit = metadata.commit（產生資料嘅輸入 commit）；'
                  '兩者唔同係 daily bot commit 嘅正常情況。draft 唔可以當 GATE-09 發布完成；'
                  'release 需齊 feature-check／postdeploy／junit 有效報告。'
                  'Actions artifact 只係中間產物；唔可以用舊 bytes 冒充同一 tag。'),
    }
    with open(os.path.join(root, 'PROVENANCE.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2)
    checksums['PROVENANCE.json'] = sha256_file(os.path.join(root, 'PROVENANCE.json'))
    with open(os.path.join(root, 'CHECKSUMS.sha256'), 'w', encoding='utf-8', newline='\n') as f:
        for rel in sorted(checksums):
            f.write(f'{checksums[rel]}  {rel}\n')
    # 發佈前私隱掃描（只報 rule/path/count；掃描失敗亦 fail-closed）
    privacy = check_public_privacy.scan_mode(root, 'tree')
    if privacy:
        raise PermissionError('歸檔資產私隱掃描命中：'
                              + '；'.join(f'{r} {p} x{c}' for r, p, c in privacy[:10]))
    zip_path = os.path.join(staging, f'archive-{tag}.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, names in os.walk(root):
            for name in sorted(names):
                full = os.path.join(dirpath, name)
                z.write(full, os.path.relpath(full, root))
    return staging, root, zip_path, checksums, provenance


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='長期發佈歸檔（GATE-09）')
    ap.add_argument('--tag', required=True, help='發布 SemVer tag（例如 v1.2.9）')
    ap.add_argument('--artifacts-dir', default=BASE, help='部署檔案根目錄（預設 repo 根）')
    ap.add_argument('--manifest', default=os.path.join(BASE, 'deploy_payload.json'))
    ap.add_argument('--reports-dir', default=None, help='測試／部署後報告目錄（可選）')
    ap.add_argument('--archive-commit', default=None,
                    help='今次歸檔 checkout 嘅不可變 commit（workflow HEAD）')
    ap.add_argument('--source-commit', default=None,
                    help='expected metadata.commit；省略時用 metadata 值')
    ap.add_argument('--deployment-commit', default=None, help='平台 deployment commit（可選）')
    ap.add_argument('--build', default=None, help='expected metadata.build（可選）')
    ap.add_argument('--run-id', default=None, help='workflow run id（同 metadata 矛盾即失敗）')
    ap.add_argument('--deploy-sha', default=None, help='平台 deployment SHA（可選）')
    ap.add_argument('--release', action='store_true',
                    help='release 歸檔：要求機器報告齊全兼有效')
    ap.add_argument('--out-dir', default=os.path.join(BASE, 'dist', 'archive'))
    args = ap.parse_args(argv)
    args.archive_type = 'release' if args.release else 'draft'

    if not valid_tag(args.tag):
        print(f'❌ 非法 tag（只接受 SemVer，可帶 v）：{args.tag!r}', file=sys.stderr)
        return 1
    try:
        with open(args.manifest, encoding='utf-8') as f:
            args.manifest_data = json.load(f)
        if not isinstance(args.manifest_data, dict) or not isinstance(args.manifest_data.get('files'), list) \
                or not args.manifest_data['files']:
            raise ValueError('manifest 必須有非空 files 陣列')
        with open(os.path.join(args.artifacts_dir, 'metadata.json'), encoding='utf-8') as f:
            args.metadata = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ 讀取 manifest／metadata 失敗：{e}', file=sys.stderr)
        return 1

    if not tag_matches_version(args.tag, args.metadata.get('version')):
        print(f"❌ tag {args.tag!r} 唔等於 metadata.version {args.metadata.get('version')!r}"
              '（唔可以將 1.2.9-test 當 1.2.9）', file=sys.stderr)
        return 1
    meta_run = args.metadata.get('workflowRunId')
    if args.run_id and meta_run and args.run_id != meta_run:
        print(f'❌ --run-id {args.run_id!r} 同 metadata.workflowRunId {meta_run!r} 矛盾',
              file=sys.stderr)
        return 1

    errors = verify_metadata(os.path.join(args.artifacts_dir, 'metadata.json'), args.manifest,
                             args.artifacts_dir, args.source_commit, args.build)
    if errors:
        print('❌ 歸檔前驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1

    if args.release:
        rep_errors = verify_release_reports(args.reports_dir, args.archive_commit)
        if rep_errors:
            print('❌ release 歸檔報告唔齊／無效：', file=sys.stderr)
            for e in rep_errors:
                print('  -', e, file=sys.stderr)
            return 1

    target = os.path.join(args.out_dir, args.tag)
    target_zip = os.path.join(args.out_dir, f'archive-{args.tag}.zip')
    target_exists = os.path.exists(target)
    zip_exists = os.path.exists(target_zip)
    try:
        staging, root, zip_path, checksums, provenance = _build_staging(args.tag, args, args.out_dir)
    except (OSError, ValueError, PermissionError, FileNotFoundError) as e:
        print(f'❌ 建立 staging 失敗（唔會留低半成品歸檔）：{e}', file=sys.stderr)
        return 1
    if target_exists or zip_exists:
        try:
            if not (target_exists and zip_exists):
                missing = 'zip' if target_exists else '目錄'
                print(f'❌ 不完整歸檔（缺少 {missing}）；拒絕覆寫或宣稱成功。'
                      f'請人手核對後移除 {args.out_dir}/{args.tag}* 再重試', file=sys.stderr)
                return 1
            ok, reason = existing_is_consistent(target, target_zip, checksums, provenance)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        if ok:
            print(f'ℹ️ 歸檔已存在而且目錄／zip／payload／provenance 一致（唔重複、唔 clobber）：{target}')
            return 0
        print(f'❌ {target} 已存在但{reason}，拒絕覆寫（禁止 clobber）', file=sys.stderr)
        return 1

    replaced_zip = False
    try:
        os.replace(zip_path, target_zip)
        replaced_zip = True
        os.replace(root, target)
    except OSError as e:
        if replaced_zip:
            try:
                os.remove(target_zip)
            except OSError as ce:
                print(f'⚠️ 回滾 zip 失敗：{ce}；已留下不完整歸檔，下次執行會明確失敗', file=sys.stderr)
        print(f'❌ 歸檔換名失敗：{e}', file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f'✅ 已建立長期歸檔（{args.archive_type}）：{target}')
    print(f'   zip：{target_zip}')
    print(f'   檔案數：{len(checksums)} · archiveCommit={args.archive_commit} '
          f'sourceCommit={args.source_commit or args.metadata.get("commit")} '
          f'build={args.metadata.get("build")}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
