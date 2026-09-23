#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D4-A：全 reachable Git 歷史秘密／私人線審計（fail-closed）。

只報 type／blob／commit／path；唔會把命中值寫入 report、log 或 stdout。
- credentialFindings > 0 → exit 1（停止 push／PR，先處理／輪換）；
- selfHostFindings 可非零，作 D4-A residual risk 記錄；
- git 錯誤、report 寫入失敗 → exit 1，唔會假裝完成。

用法：
  python scripts/check_public_history.py --repo . --all-refs --report <repo 外 path>
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 以片段組合，避免 audit 腳本自身被自己的 pattern 誤命中。
_CREDENTIAL_PATTERNS = [
    ('PRIVATE_KEY', ''.join(['-----BEGIN ', '[A-Z ]*', 'PRIVATE KEY-----'])),
    ('GITHUB_TOKEN', r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'),
    ('GITHUB_PAT', r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'),
    ('AWS_ACCESS_KEY', r'\bAKIA[0-9A-Z]{16}\b'),
    ('SLACK_TOKEN', r'\bxox[baprs]-[A-Za-z0-9-]{10,}\b'),
    ('GOOGLE_API_KEY', r'\bAIza[0-9A-Za-z_-]{35}\b'),
    ('PRIVATE_TOKEN_ASSIGN', r'(?i)\b(?:api[_-]?key|client[_-]?secret|access[_-]?token|password)\s*[:=]\s*["\']?[A-Za-z0-9_\-]{20,}'),
]
_SELFHOST_PATTERNS = [
    ('SELFHOST_PATH', '/srv/' + 'aircon-compare'),
    ('SELFHOST_ENTRY', 'release-' + '299c3e9'),
    ('SELFHOST_PROJECT', 'aircon-' + 'docker'),
]
_CREDENTIAL_RE = [(t, re.compile(p)) for t, p in _CREDENTIAL_PATTERNS]
_SELFHOST_RE = [(t, re.compile(p)) for t, p in _SELFHOST_PATTERNS]


def _git(repo, args, binary=False):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True,
                          text=not binary, encoding=None if binary else 'utf-8',
                          errors=None if binary else 'replace')


def _refs(repo):
    r = _git(repo, ['rev-list', '--all'])
    if r.returncode != 0:
        raise RuntimeError(f'git rev-list 失敗：{r.stderr.strip()[:300]}')
    return [c for c in r.stdout.splitlines() if c.strip()]


def _blob_index(repo, commits):
    """回傳 {blob_sha: {'path': str, 'commits': [sha,...]}}；只收 blob。"""
    index = {}
    for commit in commits:
        r = _git(repo, ['ls-tree', '-r', '-z', commit], binary=True)
        if r.returncode != 0:
            raise RuntimeError(f'git ls-tree 失敗（{commit[:12]}）：{r.stderr[:200]!r}')
        for rec in r.stdout.split(b'\0'):
            if not rec or b'\t' not in rec:
                continue
            meta, raw_path = rec.split(b'\t', 1)
            parts = meta.split()
            if len(parts) != 3:
                raise RuntimeError(f'ls-tree entry 格式異常：{meta!r}')
            _mode, typ, sha = parts
            if typ != b'blob':
                continue
            try:
                path = raw_path.decode('utf-8')
            except UnicodeDecodeError:
                path = raw_path.decode('utf-8', 'replace')
            entry = index.setdefault(sha.decode('ascii'), {'path': path, 'commits': []})
            if commit not in entry['commits']:
                entry['commits'].append(commit)
    return index


def _read_blob(repo, sha):
    r = _git(repo, ['cat-file', 'blob', sha], binary=True)
    if r.returncode != 0:
        raise RuntimeError(f'git cat-file 失敗（{sha[:12]}）：{r.stderr[:200]!r}')
    return r.stdout


def audit(repo):
    commits = _refs(repo)
    if not commits:
        raise RuntimeError('冇 reachable commits 可審計')
    index = _blob_index(repo, commits)
    credentials = []
    selfhost = []
    for sha, entry in index.items():
        data = _read_blob(repo, sha)
        text = data.decode('utf-8', 'replace')
        for ptype, rx in _CREDENTIAL_RE:
            if rx.search(text):
                credentials.append({'type': ptype, 'blob': sha, 'path': entry['path'],
                                    'commits': sorted(entry['commits'])[:20]})
                break
        for ptype, rx in _SELFHOST_RE:
            if rx.search(text):
                selfhost.append({'type': ptype, 'blob': sha, 'path': entry['path'],
                                 'commits': sorted(entry['commits'])[:20]})
    credentials.sort(key=lambda x: (x['type'], x['path'], x['blob']))
    selfhost.sort(key=lambda x: (x['type'], x['path'], x['blob']))
    return {
        'schemaVersion': 1,
        'scannedCommits': len(commits),
        'scannedBlobs': len(index),
        'credentialFindings': credentials,
        'selfHostFindings': selfhost,
        'credentialFindingCount': len(credentials),
        'selfHostFindingCount': len(selfhost),
        'ok': not credentials,
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='全 Git 歷史秘密／私人線審計')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--all-refs', action='store_true', help='掃描所有 reachable refs（預設）')
    ap.add_argument('--report', required=True, help='報告輸出路徑（應喺 repo 外）')
    args = ap.parse_args(argv)
    try:
        report = audit(args.repo)
        report['repo'] = os.path.abspath(args.repo)
        report['scannedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        out = os.path.abspath(args.report)
        os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
        tmp = out + '.tmp'
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except (OSError, RuntimeError, ValueError) as e:
        print(f'❌ history audit 失敗（fail-closed）：{type(e).__name__}: {e}', file=sys.stderr)
        return 1
    print(f"✅ history audit：commits={report['scannedCommits']} "
          f"blobs={report['scannedBlobs']} "
          f"credentialFindings={report['credentialFindingCount']} "
          f"selfHostFindings={report['selfHostFindingCount']} report={out}")
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
