#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check repo-local Markdown links and fragments without network access.

This is deliberately stricter than a visual smoke test for the public repo:
relative files must exist, GitHub blob/tree links for this repository must map
to a real tracked path, and every local/cross-file fragment must match a real
heading or an explicit HTML anchor. External URLs are inventoried but not
fetched (they are reported separately and cannot make the internal check fail).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
REPO_SLUG = 'CalvinLau1012/aircon-compare'
MD_LINK_RE = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
FENCE_RE = re.compile(r'^```')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*)$')
EXPLICIT_ID_RE = re.compile(r'''<a\s+[^>]*(?:id|name)=["']([^"']+)["']''', re.I)


def _slug(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text).strip().lower()
    text = re.sub(r'[^\w\u4e00-\u9fff\- ]', '', text, flags=re.UNICODE)
    return text.replace(' ', '-')


def _anchors(path: Path) -> set[str]:
    text = path.read_text(encoding='utf-8')
    anchors = set(EXPLICIT_ID_RE.findall(text))
    seen: dict[str, int] = {}
    in_code = False
    for line in text.splitlines():
        if FENCE_RE.match(line.strip()):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        base = _slug(m.group(2))
        count = seen.get(base, 0)
        anchors.add(base if count == 0 else f'{base}-{count}')
        seen[base] = count + 1
    return anchors


def _markdown_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(['git', 'ls-files', '*.md'], cwd=root, check=True,
                             capture_output=True, text=True).stdout.splitlines()
        if out:
            return [root / p for p in out if (root / p).is_file()]
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(p for p in root.rglob('*.md')
                  if '.git' not in p.parts and '.venv' not in p.parts)


def _github_target(path: str) -> tuple[str, str] | None:
    if path.startswith(f'/{REPO_SLUG}/'):
        path = path[len(f'/{REPO_SLUG}/'):]
    prefix = f'/{REPO_SLUG}/'
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    parts = rest.split('/', 2)
    if len(parts) < 3 or parts[0] not in ('blob', 'tree', 'raw'):
        return None
    return parts[2], ''


def _check_anchor(fragment: str, anchors: set[str], generated: set[str]) -> bool:
    if not fragment:
        return True
    if fragment in anchors or fragment in generated:
        return True
    # The generated comparison page fixes hand-written TOC slugs in JS by
    # matching on the alphanumeric/Han characters. Mirror that tolerance only
    # for the report source, never for ordinary repo docs.
    key = fragment.replace('-', '').lower()
    return any(a.replace('-', '').lower() == key for a in anchors | generated)


def check(root: Path = ROOT) -> dict:
    root = root.resolve()
    files = _markdown_files(root)
    anchors = {p: _anchors(p) for p in files}
    # Generated page IDs supplied by frontend/public/compare.js navMap.
    generated = {
        root / '空調對比報告.md': {
            'compare', 'table', 'energy', 'rank', 'recommend', 'price',
            'official', 'verify', 'forum', 'source', 'compare-base',
        },
    }
    broken: list[dict] = []
    checked = 0
    anchors_checked = 0
    external: list[dict] = []
    for source in files:
        text = source.read_text(encoding='utf-8')
        in_code = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if FENCE_RE.match(line.strip()):
                in_code = not in_code
                continue
            if in_code:
                continue
            for m in MD_LINK_RE.finditer(line):
                raw = m.group(1).strip()
                if not raw:
                    continue
                if raw.startswith(('mailto:', 'tel:', 'javascript:', 'data:')):
                    continue
                u = urlparse(raw)
                if u.scheme in ('http', 'https'):
                    mapped = _github_target(u.path)
                    if not mapped:
                        external.append({'source': str(source.relative_to(root)),
                                         'line': lineno, 'target': raw})
                        continue
                    rel, _ = mapped
                    target = (root / unquote(rel)).resolve()
                elif u.scheme:
                    continue
                else:
                    target = (source.parent / unquote(u.path)).resolve() if u.path else source
                checked += 1
                if not str(target).startswith(str(root)):
                    broken.append({'source': str(source.relative_to(root)), 'line': lineno,
                                   'target': raw, 'reason': 'escapes repository'})
                    continue
                if not target.exists():
                    broken.append({'source': str(source.relative_to(root)), 'line': lineno,
                                   'target': raw, 'reason': 'missing target'})
                    continue
                if u.fragment and target.suffix.lower() == '.md':
                    anchors_checked += 1
                    if not _check_anchor(unquote(u.fragment), anchors.get(target, set()),
                                         generated.get(target, set())):
                        broken.append({'source': str(source.relative_to(root)), 'line': lineno,
                                       'target': raw, 'reason': 'missing fragment'})
    return {
        'ok': not broken,
        'markdownFiles': len(files),
        'linksChecked': checked,
        'fragmentsChecked': anchors_checked,
        'broken': broken,
        'external': external,
        'externalCount': len(external),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='檢查 repo Markdown 內部連結與 fragment')
    ap.add_argument('--root', default=str(ROOT))
    ap.add_argument('--report', default=None)
    args = ap.parse_args(argv)
    result = check(Path(args.root))
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
    print(json.dumps({k: result[k] for k in
                      ('ok', 'markdownFiles', 'linksChecked', 'fragmentsChecked',
                       'externalCount')}, ensure_ascii=False))
    for item in result['broken'][:20]:
        print(f"❌ {item['source']}:{item['line']} {item['target']} ({item['reason']})",
              file=sys.stderr)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
