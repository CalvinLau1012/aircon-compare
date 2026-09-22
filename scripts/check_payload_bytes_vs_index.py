#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 防線：payload 工作樹 bytes 必須等於 Git index blob bytes。

點解需要（GitHub Pages hash 鏈）：
  `.gitattributes` 係 `* text=auto eol=lf`。如果工作樹檔案係 CRLF（例如
  `csv.writer` 預設 lineterminator='\\r\\n'），`git add` 之後 index blob 會變 LF，
  最終 push 同 Pages 發佈嘅 bytes 同 pipeline 計 hash 用嘅工作樹 bytes 唔同 →
  metadata.datasetHash／releasePayloadHash 同線上檔案唔一致。

用法（workflow：`git add -A` 之後、`git commit` 之前）：
  python scripts/check_payload_bytes_vs_index.py [--repo .] [--manifest deploy_payload.json]

退出碼：0 = 全部一致；1 = 有任何唔一致／未 stage／讀唔到。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def index_blob_bytes(repo, rel):
    """讀取 index 內該路徑嘅 blob bytes（未 stage 會 raise）"""
    r = subprocess.run(['git', '-C', repo, 'cat-file', 'blob', f':{rel}'],
                       capture_output=True)
    if r.returncode != 0:
        msg = r.stderr.decode('utf-8', errors='replace').strip() or f'git cat-file 失敗（{rel} 未 stage？）'
        raise RuntimeError(msg)
    return r.stdout


def _load_gen_metadata():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'aircon_gen_metadata_for_index',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gen-metadata.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(repo, manifest_path):
    manifest = json.load(open(manifest_path, encoding='utf-8'))
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        print('❌ manifest 缺少非空 files 陣列', file=sys.stderr)
        return 1
    # 共用 manifest 路徑契約（相對路徑／無 ..／無重複／無 symlink escape）
    try:
        files = _load_gen_metadata().validate_manifest_files(files, base=repo)
    except (ValueError, FileNotFoundError) as e:
        print(f'❌ manifest 路徑契約失敗：{e}', file=sys.stderr)
        return 1
    bad = 0
    for rel in files:
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            print(f'❌ 缺少 payload 檔案：{rel}', file=sys.stderr)
            bad += 1
            continue
        wt = sha256_bytes(open(path, 'rb').read())
        try:
            idx = sha256_bytes(index_blob_bytes(repo, rel))
        except RuntimeError as e:
            print(f'❌ {rel}：{e}', file=sys.stderr)
            bad += 1
            continue
        if wt == idx:
            print(f'✅ {rel}：{wt}')
        else:
            print(f'❌ {rel}：worktree={wt} != index={idx}'
                  '（clean/smudge 正規化會令線上 bytes 同 hash 唔一致）', file=sys.stderr)
            bad += 1
    if bad:
        print('❌ payload bytes 同 git index 唔一致（共 %d 個）；hash 鏈會被破壞' % bad, file=sys.stderr)
        return 1
    print('✅ payload 全部檔案 worktree bytes == git index bytes')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.')
    ap.add_argument('--manifest', default=None)
    args = ap.parse_args()
    manifest = args.manifest or os.path.join(args.repo, 'deploy_payload.json')
    return check(args.repo, manifest)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
