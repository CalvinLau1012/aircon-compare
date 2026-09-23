#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開 repo 私隱 gate：禁止個人／自建環境識別資料。

模式（語義明確，唔可以口講 HEAD 但讀 worktree）：
  --mode tracked  / head ：Git HEAD tree 內 tracked blobs（`git ls-tree` + cat-file）
  --mode index           ：目前 Git index blobs（staged 內容；stage 0 以外即 unmerged → 失敗）
  --mode worktree        ：tracked worktree bytes + untracked non-ignored 檔案
  --mode tree <dir>      ：指定目錄實際 bytes（歸檔發佈前掃描用）

Fail-closed：
- `git ls-files`／`git ls-tree`／`cat-file` 任何非零、header／type／size 不正確、
  輸出長度唔符、重複 path、unmerged stage 一律 raise，唔會回部分成功；
- 非 binary-allowlist 嘅候選文字若 UTF-8 解碼失敗，raise（唔可以靜默 skip）；
- binary allowlist 只涵蓋圖片／字型／PDF 等：**唔會**聲稱已掃描 PDF 內可見文字，
  其 privacy 證據上限係「檔案被識別為 binary 並排除」，需要另行審查。

輸出只列「規則 ID + 相對路徑 + 次數」，唔會打印命中內容。
退出碼：0 = 0 命中；1 = 有禁止 pattern 或掃描失敗（fail-closed）。
"""
import argparse
import os
import re
import subprocess
import sys

ALLOW_HOME_USERS = {'deploy-user', 'runner', 'app', 'aircon', 'example', 'user', 'srv', 'opt'}
SYNTHETIC_PATHS = ('tests/', 'release/sandbox/')
# binary allowlist（窄；PDF 只係被識別為 binary，唔代表已掃描內文）
BINARY_EXT = ('.pdf', '.webp', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.ttf')

RULES = [
    ('LAN_IPV4', re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b')),
    ('DDNS_DOMAIN', re.compile(r'\b[a-z0-9][a-z0-9-]{2,}\.ddns\.net\b', re.I)),
    ('SSH_AT_IP', re.compile(r'\b[a-z_][a-z0-9_-]{1,31}@(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b', re.I)),
    ('HOME_PATH', re.compile(r'/home/([a-z0-9_-]{2,32})')),
    ('WIN_USER_PATH', re.compile(r'\b[A-Za-z]:[\\/]Users[\\/][^\\/\s"\'<>]+')),
    ('AGENTS_PATH', re.compile(r'(?<![A-Za-z0-9_])\.agents[\\/][A-Za-z0-9_.-]+')),
    ('DOCKER_VOLUME_PATH', re.compile(r'/var/lib/docker/volumes/([A-Za-z0-9_.-]+)')),
    ('COMPOSE_VOLUME_NAME', re.compile(r'\b[a-z0-9-]+_aircon-[a-z0-9-]+\b')),
    ('PRIVATE_RELEASE_LINE', re.compile(r'release-299c3e9|aircon-docker|AIRCON_BASE_DIR|AIRCON_STAGING_PORT|/srv/aircon-compare|codex/durable-release')),
    ('PENDING_MARKER', re.compile(r'TODO\(PENDING-[A-Z0-9-]+\)|FIXME\(PENDING-[A-Z0-9-]+\)')),
    ('PRIVATE_BUILD_ID', re.compile(r'\bB\d{8}\.(?:docker|local|server)-[0-9A-Za-z-]+')),
    ('BACKUP_TIMESTAMP', re.compile(r'\b\d{8}T\d{6}Z(_\d+)?\b')),
    ('LOCAL_PORT', re.compile(r'\b(?:127\.0\.0\.1|localhost):(?:8[0-9]{3}|9[0-9]{3})\b')),
    ('TOKEN_PATTERN', re.compile(r'\b(?:gho_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|ghs_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|x-access-token[:@])')),
]


def _allowed(rule, path, match):
    if path.startswith(SYNTHETIC_PATHS) and rule in ('BACKUP_TIMESTAMP', 'LOCAL_PORT'):
        return True
    if rule == 'HOME_PATH' and match.group(1) in ALLOW_HOME_USERS:
        return True
    if rule == 'DOCKER_VOLUME_PATH' and ('example' in match.group(1) or 'fake' in match.group(1)):
        return True
    if rule == 'LOCAL_PORT' and match.group(0) in ('127.0.0.1:8080', 'localhost:8080'):
        return True
    if rule == 'AGENTS_PATH' and path == '.gitignore':
        return True
    if rule in ('LAN_IPV4',) and match.group(0).startswith('192.0.2.'):
        return True
    if rule in ('PRIVATE_RELEASE_LINE', 'PENDING_MARKER') and path == 'scripts/check_public_privacy.py':
        return True
    if rule == 'WIN_USER_PATH' and path.startswith(('tests/', 'release/sandbox/')):
        return True
    return False


def scan_text(rel, text):
    violations = []
    for name, rx in RULES:
        count = 0
        for m in rx.finditer(text):
            if not _allowed(name, rel, m):
                count += 1
        if count:
            violations.append((name, rel, count))
    return violations


def _decode_strict(rel, blob):
    try:
        return blob.decode('utf-8')
    except UnicodeDecodeError as e:
        raise RuntimeError(f'{rel} 唔係有效 UTF-8（唔可以靜默 skip）：{e}')


def scan_paths(repo, rels, base=None, allow_missing=False):
    """掃指定相對路徑清單（實際檔案 bytes）；讀取／解碼失敗即 raise。"""
    base = base or repo
    violations = []
    for rel in rels:
        if rel.startswith('.agents/'):
            violations.append(('TRACKED_PRIVATE_DIR', rel, 1))
            continue
        if any(rel.lower().endswith(e) for e in BINARY_EXT):
            continue
        path = os.path.join(base, rel)
        if not os.path.isfile(path):
            if allow_missing:
                continue
            raise FileNotFoundError(f'scan_paths 搵唔到檔案：{path}')
        with open(path, 'rb') as f:
            blob = f.read()
        violations.extend(scan_text(rel, _decode_strict(rel, blob)))
    return violations


def _git(repo, *args, check=True):
    r = subprocess.run(['git', '-C', repo, *args], capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError('git %s 失敗（exit %d）：%s'
                           % (' '.join(args), r.returncode,
                              r.stderr.decode('utf-8', 'replace')[:300]))
    return r


def _cat_file_batch(repo, entries):
    """entries: [(path, sha)]；嚴格解析 cat-file --batch 輸出，回 {path: bytes}。"""
    if not entries:
        return {}
    proc = subprocess.run(['git', '-C', repo, 'cat-file', '--batch'],
                          input=('\n'.join(sha for _, sha in entries) + '\n').encode(),
                          capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError('git cat-file --batch 失敗：'
                           + proc.stderr.decode('utf-8', 'replace')[:300])
    out = proc.stdout
    pos = 0
    blobs = {}
    for path, sha in entries:
        nl = out.find(b'\n', pos)
        if nl < 0:
            raise RuntimeError(f'cat-file 輸出缺少 header（{path}）')
        header = out[pos:nl].decode('utf-8', 'replace').split()
        if len(header) >= 2 and header[1] == 'missing':
            raise RuntimeError(f'cat-file 搵唔到 object（{path} {sha}）')
        if len(header) != 3 or header[0] != sha or header[1] != 'blob':
            raise RuntimeError(f'cat-file header 唔正確（{path}）：{header}')
        try:
            size = int(header[2])
        except ValueError:
            raise RuntimeError(f'cat-file size 唔正確（{path}）：{header[2]!r}')
        if size < 0:
            raise RuntimeError(f'cat-file size 負數（{path}）')
        start = nl + 1
        end = start + size
        if end > len(out):
            raise RuntimeError(f'cat-file 輸出長度不足（{path}）')
        blobs[path] = out[start:end]
        if end >= len(out) or out[end:end + 1] != b'\n':
            raise RuntimeError(f'cat-file 輸出缺少分隔換行（{path}）')
        pos = end + 1
    if pos != len(out):
        raise RuntimeError('cat-file 有未預期額外輸出')
    return blobs


def _head_entries(repo):
    out = _git(repo, 'ls-tree', '-r', '-z', 'HEAD').stdout
    entries = []
    seen = set()
    for e in out.decode('utf-8', 'replace').split('\0'):
        if not e:
            continue
        meta, path = e.split('\t', 1)
        mode, otype, sha = meta.split(' ', 2)
        if path in seen:
            raise RuntimeError(f'HEAD tree 有重複 path：{path}')
        seen.add(path)
        if otype != 'blob':
            continue
        entries.append((path, sha))
    return entries


def _index_entries(repo):
    out = _git(repo, 'ls-files', '-s', '-z').stdout
    entries = []
    seen = set()
    for e in out.decode('utf-8', 'replace').split('\0'):
        if not e:
            continue
        meta, path = e.split('\t', 1)
        parts = meta.split()
        if len(parts) != 3:
            raise RuntimeError(f'index entry 格式唔正確：{e!r}')
        _mode, sha, stage = parts
        if stage != '0':
            raise RuntimeError(f'index 有 unmerged entry（stage={stage}）：{path}')
        if path in seen:
            raise RuntimeError(f'index 有重複 path：{path}')
        seen.add(path)
        entries.append((path, sha))
    return entries


def _tracked_files(repo):
    return [f for f in _git(repo, 'ls-files', '-z').stdout.decode('utf-8', 'replace').split('\0') if f]


def _worktree_files(repo):
    out = _git(repo, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').stdout
    return [f for f in out.decode('utf-8', 'replace').split('\0') if f]


def _scan_blobs(entries, blobs):
    violations = []
    for path, _sha in entries:
        if path.startswith('.agents/'):
            violations.append(('TRACKED_PRIVATE_DIR', path, 1))
            continue
        if any(path.lower().endswith(e) for e in BINARY_EXT):
            continue
        violations.extend(scan_text(path, _decode_strict(path, blobs[path])))
    return violations


def scan_mode(repo, mode, tree_dir=None):
    if mode == 'tree':
        root = tree_dir or repo
        rels = []
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                full = os.path.join(dirpath, name)
                rels.append(os.path.relpath(full, root).replace('\\', '/'))
        return scan_paths(root, sorted(rels), base=root)
    if mode in ('tracked', 'head'):
        entries = _head_entries(repo)
        return _scan_blobs(entries, _cat_file_batch(repo, entries))
    if mode == 'index':
        entries = _index_entries(repo)
        return _scan_blobs(entries, _cat_file_batch(repo, entries))
    if mode == 'worktree':
        return scan_paths(repo, _worktree_files(repo), base=repo, allow_missing=True)
    raise ValueError(f'未知模式：{mode}')


def scan(repo):
    """兼容舊 API：tracked = HEAD tree blobs。"""
    return scan_mode(repo, 'tracked')


def main():
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='公開 repo 私隱 gate')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--mode', choices=['tracked', 'head', 'index', 'worktree', 'tree'],
                    default='worktree')
    ap.add_argument('--tree', default=None, help='--mode tree 時嘅根目錄')
    args = ap.parse_args()
    try:
        v = scan_mode(args.repo, args.mode, args.tree)
    except (RuntimeError, FileNotFoundError, ValueError) as e:
        print(f'❌ 私隱 gate 無法完成掃描（fail-closed）：{e}', file=sys.stderr)
        return 1
    for rule, path, count in v:
        print(f'❌ {rule} {path} x{count}')
    if v:
        print(f'❌ 公開 repo 私隱 gate 失敗（mode={args.mode}）：{len(v)} 個檔案有禁止 pattern',
              file=sys.stderr)
        return 1
    print(f'✅ 公開 repo 私隱 gate：mode={args.mode} 0 命中')
    return 0


if __name__ == '__main__':
    sys.exit(main())
