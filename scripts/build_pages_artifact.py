#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 payload manifest + envelope 建立 GitHub Pages artifact（D2-A 修復）。

語義分離：
- `deploy_payload.json`：唯一 `releasePayloadHash` 範圍（HTML/PDF/CSV；唔會包含
  metadata.json，避免自引用）；
- `deploy_envelope.json`：實際部署封包；必須包含 payload 全部檔案 + 最終
  `metadata.json`，可選公開 sidecar（只收錄同本次 CSV/metadata 一致嘅）。

Fail-closed 契約：
- manifest／envelope 路徑安全（相對 POSIX、無 `..`、無 backslash、無 symlink escape）；
- metadata 必須通過治理內嵌 Schema、version == models_data.VERSION、
  `releasePayloadHash` == payload bytes 重算、`datasetHash` == CSV bytes、
  record 計數同 CSV 行數一致；任何錯配即失敗；
- 輸出目錄拒 repo 根／祖先、`.git`、symlink／junction 父層、與輸入重疊、
  任意已有資料目錄；用受控 staging + 安全替換，失敗唔會刪已有內容；
- copy 逐 bytes；輸出檔案集合必須精確等於 envelope。

用法：
  python scripts/build_pages_artifact.py --repo . \
      --payload-manifest deploy_payload.json --envelope deploy_envelope.json \
      --metadata metadata.json --out _site [--report <repo 外 path>] \
      [--check-only] [--replace-sealed]
退出碼：0 = 成功（或 check-only 驗證通過）；1 = 任何契約失敗。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (BASE, os.path.join(BASE, 'scripts')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class ArtifactError(ValueError):
    pass


def _normalize_rel(rel, label='path'):
    if not isinstance(rel, str) or not rel.strip():
        raise ArtifactError(f'{label} 必須係非空字串：{rel!r}')
    if rel != rel.strip():
        raise ArtifactError(f'{label} 唔可以有前後空白：{rel!r}')
    if '\\' in rel:
        raise ArtifactError(f'{label} 唔准用 backslash：{rel!r}')
    if rel.startswith('/'):
        raise ArtifactError(f'{label} 唔准係絕對路徑：{rel!r}')
    if '\x00' in rel:
        raise ArtifactError(f'{label} 唔可以有 NUL：{rel!r}')
    parts = rel.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ArtifactError(f'{label} 有非法 segment：{rel!r}')
    return '/'.join(parts)


def _load_json(path, label):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ArtifactError(f'{label} 讀取失敗：{e}')


def load_payload_manifest(path):
    data = _load_json(path, 'payload manifest')
    files = data.get('files') if isinstance(data, dict) else None
    if not isinstance(files, list) or not files:
        raise ArtifactError('deploy_payload.json 必須有非空 files array')
    out, seen = [], set()
    for raw in files:
        rel = _normalize_rel(raw, 'payload path')
        if rel in seen:
            raise ArtifactError(f'payload manifest 有重複 path：{rel}')
        seen.add(rel)
        out.append(rel)
    return out


def load_envelope(path):
    data = _load_json(path, 'deploy envelope')
    if not isinstance(data, dict) or not isinstance(data.get('requiredFiles'), list):
        raise ArtifactError('deploy_envelope.json 必須有 requiredFiles array')
    required, seen = [], set()
    for raw in data['requiredFiles']:
        rel = _normalize_rel(raw, 'envelope required path')
        if rel in seen:
            raise ArtifactError(f'envelope requiredFiles 有重複 path：{rel}')
        seen.add(rel)
        required.append(rel)
    optional, oseen = [], set()
    for raw in data.get('optionalFiles', []):
        rel = _normalize_rel(raw, 'envelope optional path')
        if rel in seen or rel in oseen:
            raise ArtifactError(f'envelope optionalFiles 重複 path：{rel}')
        oseen.add(rel)
        optional.append(rel)
    return required, optional


def _is_link_like(path):
    """symlink 或 Windows junction 都當 link-like（junction islink() 係 False）。"""
    if os.path.islink(path):
        return True
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return getattr(st, 'st_reparse_tag', 0) != 0


def _reject_link_components(path, label):
    cur = os.path.abspath(path)
    while True:
        if _is_link_like(cur):
            raise ArtifactError(f'{label} 有 symlink／junction：{path}')
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent


def validate_source_file(repo, rel):
    src = os.path.join(repo, *rel.split('/'))
    cur = os.path.abspath(repo)
    for part in rel.split('/'):
        cur = os.path.join(cur, part)
        if _is_link_like(cur):
            raise ArtifactError(f'來源係 symlink：{rel}')
    if not os.path.isfile(src):
        raise ArtifactError(f'來源唔存在或唔係普通檔案：{rel}')
    real = os.path.realpath(src)
    root = os.path.realpath(repo)
    if os.path.commonpath([real, root]) != root:
        raise ArtifactError(f'來源逃出 repo：{rel}')
    return src


def validate_out_dir(repo, out_dir, protected_inputs):
    repo_abs = os.path.abspath(repo)
    out_abs = os.path.abspath(out_dir)
    if out_abs == repo_abs:
        raise ArtifactError('輸出目錄唔可以係 repo 根')
    if repo_abs.startswith(out_abs + os.sep):
        raise ArtifactError('輸出目錄唔可以係 repo 祖先')
    parts = out_abs.replace('\\', '/').split('/')
    if '.git' in parts:
        raise ArtifactError('輸出目錄唔可以喺 .git 內')
    _reject_link_components(out_abs, '輸出目錄')
    if os.path.lexists(out_abs) and os.path.realpath(out_abs) != os.path.normpath(out_abs):
        raise ArtifactError('輸出目錄出現 junction／symlink 逃逸')
    for inp in protected_inputs:
        inp_abs = os.path.abspath(inp)
        if inp_abs == out_abs or inp_abs.startswith(out_abs + os.sep):
            raise ArtifactError(f'輸出目錄同輸入重疊：{os.path.relpath(inp_abs, repo_abs)}')
        if out_abs.startswith(inp_abs + os.sep):
            raise ArtifactError(f'輸出目錄喺輸入之下：{os.path.basename(inp_abs)}')
    parent = os.path.dirname(out_abs)
    if not os.path.isdir(parent):
        raise ArtifactError(f'輸出目錄 parent 唔存在：{parent}')
    _reject_link_components(parent, '輸出 parent')


def _load_gen_metadata():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'aircon_gen_metadata_pages', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return 'sha256:' + h.hexdigest()


def verify_metadata(repo, payload_files, metadata_path):
    """驗 metadata 同 payload／CSV 完全一致；回傳 (metadata, csv_rel, errors)。"""
    from validate_metadata import validate
    from extract_governance import extract_blocks, GOV_FILE
    errors = []
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    meta = _load_json(metadata_path, 'metadata.json')
    if not isinstance(meta, dict):
        return None, None, ['metadata 必須係 object']
    errors.extend(f'metadata schema: {e}' for e in validate(meta, schema))
    if 'releasePayloadHash' not in meta:
        errors.append('metadata 缺少 releasePayloadHash（未 finalize 唔可以部署）')
    csvs = [f for f in payload_files if f.lower().endswith('.csv')]
    if len(csvs) != 1:
        errors.append(f'payload 必須有恰好一個 CSV（got {csvs}）')
        return meta, None, errors
    csv_rel = csvs[0]
    try:
        computed = _load_gen_metadata().hash_files(payload_files, base=repo)
    except Exception as e:  # noqa: BLE001
        errors.append(f'payload hash 計算失敗：{e}')
        return meta, csv_rel, errors
    if meta.get('releasePayloadHash') != computed:
        errors.append(f'releasePayloadHash 唔一致：metadata={meta.get("releasePayloadHash")} '
                      f'computed={computed}')
    try:
        csv_hash = _sha256_file(os.path.join(repo, *csv_rel.split('/')))
    except OSError as e:
        errors.append(f'CSV 讀取失敗：{e}')
        return meta, csv_rel, errors
    if meta.get('datasetHash') != csv_hash:
        errors.append(f'datasetHash 唔一致：metadata={meta.get("datasetHash")} csv={csv_hash}')
    try:
        import csv as _csv
        with open(os.path.join(repo, *csv_rel.split('/')), encoding='utf-8-sig') as f:
            rows = sum(1 for _ in _csv.reader(f)) - 1
        if meta.get('rawRecordCount') is not None and meta['rawRecordCount'] != rows:
            errors.append(f'rawRecordCount {meta["rawRecordCount"]} != CSV {rows} 行')
        if meta.get('registrationCount') is not None and meta['registrationCount'] != rows:
            errors.append(f'registrationCount {meta["registrationCount"]} != CSV {rows} 行')
        if meta.get('modelCount') is not None and meta.get('recordCount') is not None \
                and meta['modelCount'] != meta['recordCount']:
            errors.append('modelCount != recordCount')
    except Exception as e:  # noqa: BLE001
        errors.append(f'CSV 行數檢查失敗：{e}')
    try:
        import models_data
        if meta.get('version') != models_data.VERSION:
            errors.append(f'metadata.version {meta.get("version")} != models_data.VERSION '
                          f'{models_data.VERSION}')
    except Exception as e:  # noqa: BLE001
        errors.append(f'models_data 讀取失敗：{e}')
    return meta, csv_rel, errors


def _optional_sidecar(repo, rel, meta, errors_as_skips):
    """回傳 (include, reason)：只有同本次 CSV/metadata 一致先收錄。"""
    src = os.path.join(repo, *rel.split('/'))
    if not os.path.exists(src):
        return False, 'missing'
    if _is_link_like(src) or not os.path.isfile(src):
        return False, 'not-regular-file'
    try:
        data = json.load(open(src, encoding='utf-8'))
    except (OSError, ValueError):
        return False, 'invalid-json'
    if not isinstance(data, dict):
        return False, 'not-object'
    if rel == 'emsd_receipt.json':
        if data.get('success') is not True:
            return False, 'not-success'
        if data.get('datasetHash') != meta.get('datasetHash'):
            return False, 'legacy-or-mismatch-datasetHash'
        return True, 'included'
    if rel == 'emsd_raw_receipt.json':
        if data.get('success') is not True:
            return False, 'not-success'
        if data.get('datasetHash') != meta.get('datasetHash'):
            return False, 'stale-or-mismatch-datasetHash'
        csv_receipt = os.path.join(repo, 'emsd_receipt.json')
        if os.path.isfile(csv_receipt) and not _is_link_like(csv_receipt):
            try:
                cr = json.load(open(csv_receipt, encoding='utf-8'))
                expect = _sha256_file(src)
                if cr.get('rawReceiptHash') != expect:
                    return False, 'csv-receipt-rawReceiptHash-mismatch'
            except (OSError, ValueError):
                return False, 'csv-receipt-unreadable'
        return True, 'included'
    if rel == 'official_batch_status.json':
        for key in ('decision', 'generatedAt'):
            if key not in data:
                return False, f'missing-{key}'
        return True, 'included'
    return True, 'included'


def _copy_staged(repo, rel, dst_root):
    src = validate_source_file(repo, rel)
    dst = os.path.join(dst_root, *rel.split('/'))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, 'rb') as fin, open(dst, 'wb') as fout:
        shutil.copyfileobj(fin, fout)
    if _sha256_file(src) != _sha256_file(dst):
        raise ArtifactError(f'staged bytes 唔一致：{rel}')


def _walk_files(root):
    out = set()
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            p = os.path.join(dirpath, name)
            if _is_link_like(p):
                raise ArtifactError(f'輸出有 symlink：{os.path.relpath(p, root)}')
            out.add(os.path.relpath(p, root).replace('\\', '/'))
    return out


def build(repo, payload_manifest_path, envelope_path, metadata_path, out_dir,
          check_only=False, replace_sealed=False):
    payload_files = load_payload_manifest(payload_manifest_path)
    required, optional = load_envelope(envelope_path)
    if 'metadata.json' not in required:
        raise ArtifactError('envelope requiredFiles 必須包含 metadata.json')
    if not set(payload_files) <= set(required):
        missing = sorted(set(payload_files) - set(required))
        raise ArtifactError(f'envelope 缺少 payload 檔案：{missing}')
    protected_inputs = [payload_manifest_path, envelope_path, metadata_path]
    protected_inputs += [os.path.join(repo, *rel.split('/')) for rel in payload_files]
    protected_inputs += [os.path.join(repo, *rel.split('/')) for rel in required]
    validate_out_dir(repo, out_dir, protected_inputs)
    meta, csv_rel, errors = verify_metadata(repo, payload_files, metadata_path)
    if errors:
        raise ArtifactError('metadata／payload／CSV 一致性驗證失敗：' + '；'.join(errors))
    skipped = []
    include_optional = []
    for rel in optional:
        ok, reason = _optional_sidecar(repo, rel, meta, skipped)
        if ok:
            include_optional.append(rel)
        else:
            skipped.append({'file': rel, 'reason': reason})
    files = required + include_optional
    for rel in files:
        validate_source_file(repo, rel)
    report = {
        'ok': True,
        'checkOnly': bool(check_only),
        'payloadFiles': payload_files,
        'envelopeFiles': files,
        'optionalSkipped': skipped,
        'releasePayloadHash': meta.get('releasePayloadHash'),
        'datasetHash': meta.get('datasetHash'),
        'metadataHash': _sha256_file(metadata_path),
        'version': meta.get('version'),
        'build': meta.get('build'),
        'commit': meta.get('commit'),
    }
    if check_only:
        tmp = tempfile.mkdtemp(prefix='aircon-pages-check-')
        try:
            for rel in files:
                _copy_staged(repo, rel, tmp)
            actual = _walk_files(tmp)
            if actual != set(files):
                raise ArtifactError(f'check-only 檔案集合唔符：extra={sorted(actual - set(files))} '
                                    f'missing={sorted(set(files) - actual)}')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        report['files'] = files
        return report

    out_abs = os.path.abspath(out_dir)
    staging = out_abs + '.staging-' + str(os.getpid())
    if os.path.lexists(staging):
        raise ArtifactError(f'staging 已存在，拒絕覆寫：{staging}')
    if os.path.lexists(out_abs):
        if _is_link_like(out_abs) or not os.path.isdir(out_abs):
            raise ArtifactError('輸出位置已存在但唔係普通目錄')
        existing = _walk_files(out_abs)
        if existing and not replace_sealed:
            raise ArtifactError('輸出目錄已有內容；要替換須加 --replace-sealed')
        if existing:
            for rel in existing:
                staged_probe = os.path.join(out_abs, *rel.split('/'))
                if rel not in files or _sha256_file(staged_probe) != _sha256_file(
                        os.path.join(repo, *rel.split('/'))):
                    raise ArtifactError(f'已有目錄唔似本 artifact（檔案／bytes 唔符）：{rel}')
    try:
        os.makedirs(staging)
        for rel in files:
            _copy_staged(repo, rel, staging)
        actual = _walk_files(staging)
        if actual != set(files):
            raise ArtifactError(f'staged 檔案集合唔符：extra={sorted(actual - set(files))} '
                                f'missing={sorted(set(files) - actual)}')
        # 安全替換：舊 out 改名保留，staging 改名入 out，最後才刪舊。
        old = None
        if os.path.lexists(out_abs):
            old = out_abs + '.old-' + str(os.getpid())
            if os.path.lexists(old):
                raise ArtifactError(f'舊備份名已存在：{old}')
            os.replace(out_abs, old)
        try:
            os.replace(staging, out_abs)
        except OSError:
            if old is not None and not os.path.lexists(out_abs):
                os.replace(old, out_abs)
            raise
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    report['files'] = files
    return report


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='建立 GitHub Pages artifact（payload + metadata envelope）')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--payload-manifest', default=None)
    ap.add_argument('--envelope', default=None)
    ap.add_argument('--metadata', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--report', default=None)
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--replace-sealed', action='store_true')
    args = ap.parse_args(argv)
    repo = args.repo
    payload_manifest = args.payload_manifest or os.path.join(repo, 'deploy_payload.json')
    envelope = args.envelope or os.path.join(repo, 'deploy_envelope.json')
    metadata = args.metadata or os.path.join(repo, 'metadata.json')
    out_dir = args.out or os.path.join(repo, '_site')
    try:
        result = build(repo, payload_manifest, envelope, metadata, out_dir,
                       check_only=args.check_only, replace_sealed=args.replace_sealed)
    except (OSError, ArtifactError, ValueError) as e:
        print(f'❌ Pages artifact 建立失敗（fail-closed）：{e}', file=sys.stderr)
        return 1
    if args.report:
        try:
            tmp = args.report + '.tmp'
            with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, args.report)
        except OSError as e:
            print(f'❌ report 寫入失敗：{e}', file=sys.stderr)
            return 1
    print('✅ Pages artifact ' + ('check-only 驗證通過' if args.check_only else f'已建立：{out_dir}') +
          f'（{len(result["files"])} 個 envelope 檔案；payloadHash={result["releasePayloadHash"]}）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
