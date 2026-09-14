#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公開 repo 私隱 gate：掃描 tracked HEAD，禁止個人／自建環境識別資料。

規則只報「規則 ID + 檔名 + 次數」，唔會打印命中內容。允許通用示例值
（`192.0.2.x`、`*.example.invalid`、`/home/deploy-user` 等）。

用法：
  python scripts/check_public_privacy.py [--repo .]
退出碼：0 = 0 命中；1 = 有禁止 pattern。
"""
import argparse
import os
import re
import subprocess
import sys

# 規則：名稱 → (regex, 允許檔案子路徑前綴, 額外 allow 條件)
ALLOW_HOME_USERS = {'deploy-user', 'runner', 'app', 'aircon', 'example', 'user', 'srv', 'opt'}
SYNTHETIC_PATHS = ('tests/', 'release/sandbox/')
RULES = [
    ('LAN_IPV4', re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b'), ()),
    ('DDNS_DOMAIN', re.compile(r'\b[a-z0-9][a-z0-9-]{2,}\.ddns\.net\b', re.I), ()),
    ('SSH_AT_IP', re.compile(r'\b[a-z_][a-z0-9_-]{1,31}@(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b', re.I), ()),
    ('HOME_PATH', re.compile(r'/home/([a-z0-9_-]{2,32})'), ()),
    ('WIN_USER_PATH', re.compile(r'\b[A-Za-z]:[\\/]Users[\\/][^\\/\s"\'<>]+'), ()),
    ('AGENTS_PATH', re.compile(r'(?<![A-Za-z0-9_])\.agents[\\/][A-Za-z0-9_.-]+'), ()),
    ('DOCKER_VOLUME_PATH', re.compile(r'/var/lib/docker/volumes/([A-Za-z0-9_.-]+)'), ()),
    ('COMPOSE_VOLUME_NAME', re.compile(r'\b[a-z0-9-]+_aircon-[a-z0-9-]+\b'), ()),
    ('BACKUP_TIMESTAMP', re.compile(r'\b\d{8}T\d{6}Z(_\d+)?\b'), SYNTHETIC_PATHS),
    ('PRIVATE_BUILD_ID', re.compile(r'\bB\d{8}\.(?:docker|local|server)-[0-9A-Za-z-]+'), ()),
    ('LOCAL_PORT', re.compile(r'\b(?:127\.0\.0\.1|localhost):(?:8[0-9]{3}|9[0-9]{3})\b'), SYNTHETIC_PATHS),
    ('TOKEN_PATTERN', re.compile(r'\b(?:gho_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|ghs_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}|x-access-token[:@])'), ()),
]
BINARY_EXT = ('.pdf', '.webp', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.ttf')


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
    return False


def scan(repo):
    out = subprocess.run(['git', '-C', repo, 'ls-files', '-z'], capture_output=True).stdout
    files = [f for f in out.decode('utf-8', 'replace').split('\0') if f]
    violations = []
    for rel in files:
        if rel.startswith('.agents/'):
            violations.append(('TRACKED_PRIVATE_DIR', rel, 1))
            continue
        if any(rel.lower().endswith(e) for e in BINARY_EXT):
            continue
        path = os.path.join(repo, rel)
        if not os.path.isfile(path):
            continue
        try:
            text = open(path, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        for name, rx, _ in RULES:
            count = 0
            for m in rx.finditer(text):
                if not _allowed(name, rel, m):
                    count += 1
            if count:
                violations.append((name, rel, count))
    return violations


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='.')
    args = ap.parse_args()
    v = scan(args.repo)
    for rule, path, count in v:
        print(f'❌ {rule} {path} x{count}')
    if v:
        print(f'❌ 公開 repo 私隱 gate 失敗：{len(v)} 個檔案有禁止 pattern', file=sys.stderr)
        return 1
    print('✅ 公開 repo 私隱 gate：tracked HEAD 0 命中')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
