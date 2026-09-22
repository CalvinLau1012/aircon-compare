#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 deploy_payload.json 建立 GitHub Pages artifact。

安全契約（fail-closed）：
- 只接受 manifest 列出嘅相對 POSIX 路徑；拒空、絕對、backslash、`.`／`..` segment；
- 拒 symlink、目錄、缺檔、重複路徑、realpath 逃出 repo；
- 輸出目錄只可以有 manifest 檔案，唔可以有額外私人檔案；
- copy 用 bytes，保持逐 bytes 一致；可選同時輸出 machine report。

用法：
  python scripts/build_pages_artifact.py --repo . --manifest deploy_payload.json \
      --out _site [--report <repo 外 path>] [--check-only]
退出碼：0 = 成功（或 check-only 驗證通過）；1 = 任何契約失敗。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _normalize_rel(rel):
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError(f'manifest path 必須係非空字串：{rel!r}')
    if rel != rel.strip():
        raise ValueError(f'manifest path 唔可以有前後空白：{rel!r}')
    if '\\' in rel:
        raise ValueError(f'manifest path 唔准用 backslash：{rel!r}')
    if rel.startswith('/'):
        raise ValueError(f'manifest path 唔准係絕對路徑：{rel!r}')
    parts = rel.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError(f'manifest path 有非法 segment：{rel!r}')
    return '/'.join(parts)


def load_manifest(repo, manifest_path):
    with open(manifest_path, encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get('files'), list) or not data['files']:
        raise ValueError('deploy_payload.json 必須有非空 files array')
    files = []
    seen = set()
    for raw in data['files']:
        rel = _normalize_rel(raw)
        if rel in seen:
            raise ValueError(f'manifest 有重複 path：{rel}')
        seen.add(rel)
        files.append(rel)
    return data, files


def validate_source_file(repo, rel):
    src = os.path.join(repo, *rel.split('/'))
    # 逐層拒絕 symlink，避免 realpath 逃逸或 symlink 內容變更
    cur = os.path.abspath(repo)
    for part in rel.split('/'):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise ValueError(f'來源係 symlink：{rel}')
    if not os.path.isfile(src):
        raise ValueError(f'來源唔存在或唔係普通檔案：{rel}')
    real = os.path.realpath(src)
    root = os.path.realpath(repo)
    if os.path.commonpath([real, root]) != root:
        raise ValueError(f'來源逃出 repo：{rel}')
    return src


def build(repo, manifest_path, out_dir, check_only=False):
    _data, files = load_manifest(repo, manifest_path)
    entries = []
    for rel in files:
        src = validate_source_file(repo, rel)
        entries.append((rel, src))
    if check_only:
        return {'ok': True, 'checkOnly': True, 'files': [r for r, _ in entries]}
    if os.path.lexists(out_dir):
        if os.path.islink(out_dir) or not os.path.isdir(out_dir):
            raise ValueError(f'輸出位置已存在但唔係普通目錄：{out_dir}')
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    checksums = {}
    for rel, src in entries:
        dst = os.path.join(out_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(src, 'rb') as fin, open(dst, 'wb') as fout:
            shutil.copyfileobj(fin, fout)
        checksums[rel] = hashlib.sha256(open(dst, 'rb').read()).hexdigest()
    # 輸出目錄只可有 manifest 檔案；任何額外檔（包括隱藏檔）都拒絕
    actual = set()
    for root, dirs, names in os.walk(out_dir):
        for name in names:
            p = os.path.join(root, name)
            if os.path.islink(p):
                raise ValueError(f'輸出有 symlink：{os.path.relpath(p, out_dir)}')
            actual.add(os.path.relpath(p, out_dir).replace('\\', '/'))
    extra = sorted(actual - set(files))
    missing = sorted(set(files) - actual)
    if extra or missing:
        raise ValueError(f'輸出檔案集合唔符 manifest；extra={extra} missing={missing}')
    return {'ok': True, 'checkOnly': False, 'files': files, 'sha256': checksums}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='建立 GitHub Pages artifact')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--out', default=None)
    ap.add_argument('--report', default=None)
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args(argv)
    repo = args.repo
    manifest_path = args.manifest or os.path.join(repo, 'deploy_payload.json')
    out_dir = args.out or os.path.join(repo, '_site')
    try:
        result = build(repo, manifest_path, out_dir, check_only=args.check_only)
    except (OSError, ValueError, json.JSONDecodeError) as e:
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
          f'（{len(result["files"])} 個 manifest 檔案）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
