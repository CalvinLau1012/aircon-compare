#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail before a pure-code Pages deploy publishes stale generated artifacts.

metadata.json.commit names the source revision used to generate index.html/PDF.
If any generator input (or the report Markdown/requirements that determine PDF
bytes) differs from that revision, the checked-out artifact is stale.  A data
rebuild creates a child commit whose generator inputs are unchanged, so it
passes.  This is an additional pre-deploy gate; GATE-08 remains unchanged.
"""
import argparse
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUTS = ('generate_pdf.py', 'generate_html.py', 'crawl_utils.py',
          'models_data.py', 'requirements.txt', '空調對比報告.md')


def _git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True)


def _file(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except OSError:
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description='pre-deploy generated-source binding')
    ap.add_argument('--repo', default=BASE)
    ap.add_argument('--metadata', default=None)
    args = ap.parse_args(argv)
    repo = os.path.abspath(args.repo)
    meta_path = args.metadata or os.path.join(repo, 'metadata.json')
    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        print(f'❌ generated-source: metadata 讀取失敗: {type(e).__name__}', file=sys.stderr)
        return 1
    commit = meta.get('commit') if isinstance(meta, dict) else None
    if not isinstance(commit, str) or len(commit) != 40 or any(
            c not in '0123456789abcdef' for c in commit):
        print('❌ generated-source: metadata.commit 唔係 full 40-hex', file=sys.stderr)
        return 1
    if _git(repo, 'cat-file', '-e', commit + '^{commit}').returncode != 0:
        print('❌ generated-source: metadata.commit 唔存在本地', file=sys.stderr)
        return 1
    ancestry = _git(repo, 'merge-base', '--is-ancestor', commit, 'HEAD').returncode == 0 or \
        _git(repo, 'merge-base', '--is-ancestor', commit, 'origin/master').returncode == 0
    if not ancestry:
        print('❌ generated-source: metadata.commit 唔係 HEAD／origin/master 祖先', file=sys.stderr)
        return 1
    changed = []
    for rel in INPUTS:
        hist = _git(repo, 'show', f'{commit}:{rel}')
        if hist.returncode != 0:
            print(f'❌ generated-source: metadata.commit 缺 {rel}', file=sys.stderr)
            return 1
        current = _file(os.path.join(repo, *rel.split('/')))
        if current is None:
            print(f'❌ generated-source: checkout 缺 {rel}', file=sys.stderr)
            return 1
        if current != hist.stdout:
            changed.append(rel)
    if changed:
        print('❌ generated-source: 生成輸入同 metadata.commit 不一致：' + ', '.join(changed),
              file=sys.stderr)
        print('   必須先以目前程式重建 HTML/PDF/metadata，唔可以發布舊不相容產物。',
              file=sys.stderr)
        return 1
    print('✅ generated-source: HTML/PDF 生成輸入同 metadata.commit 一致')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
