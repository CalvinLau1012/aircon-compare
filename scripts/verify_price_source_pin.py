#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the configured price-chain source revision and exact code closure.

A data-only daily commit changes HEAD but not these files; the fixed deployment
ack revision therefore remains valid.  Any code/workflow change requires a new
ack revision.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

REV_RE = re.compile(r'^[0-9a-f]{40}$')
# Explicit price-chain closure.  Keep this list complete when adding a module
# used by collection, canonicalisation, demand, cycle, apply, coordination,
# receiving or the price/provider workflow entries.
CODE_PATHS = (
    'fetch_biggo.py', 'fetch_emsd.py', 'batch_utils.py', 'price_utils.py',
    'queue_utils.py', 'crawl_utils.py', 'model_lifecycle.py',
    'biggo_canonical.py', 'biggo_limiter.py', 'biggo_candidate.py',
    'biggo_smoke_result.py', 'biggo_acquisition.py', 'biggo_apply.py',
    'biggo_coordinator.py', 'price_demand.py',
    'scripts/biggo_stage_runner.py', 'scripts/sync_biggo_completed.py',
    'scripts/price_status.py', 'scripts/price_cycle.py',
    'scripts/price_cycle_entry.py', 'scripts/price_demand_entry.py',
    'scripts/price_cycle_migrate.py', 'scripts/price_runtime.py',
    'scripts/verify_price_source_pin.py', 'scripts/source_status.py',
    'scripts/source_health_monitor.py', 'scripts/check_health_summary.py',
    'scripts/gen-metadata.py',
    '.github/workflows/daily-update.yml',
    '.github/workflows/biggo-alert.yml',
    '.github/workflows/freshness-monitor.yml',
)
_PRIVATE_RUNTIME_PATHS = (
    'scripts/source_health_monitor.py', 'scripts/check_health_summary.py')
# Public/private candidates may intentionally differ only at the provider
# runner.  The shared modules above are byte-identical; workflow files are
# verified from the same deployed tree, not across ends.


def _sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _git(repo, *args):
    return subprocess.run(['git', '-C', repo, *args], capture_output=True)


def verify_git(repo, rev):
    if not isinstance(rev, str) or not REV_RE.match(rev):
        raise ValueError('source-revision-invalid')
    if _git(repo, 'cat-file', '-e', rev + '^{commit}').returncode != 0:
        raise ValueError('source-revision-missing')
    if _git(repo, 'merge-base', '--is-ancestor', rev, 'HEAD').returncode != 0:
        raise ValueError('source-revision-not-ancestor')
    mismatches, missing = [], []
    for rel in CODE_PATHS:
        path = os.path.join(repo, *rel.split('/'))
        try:
            current = open(path, 'rb').read()
        except OSError:
            missing.append(rel)
            continue
        shown = _git(repo, 'show', f'{rev}:{rel}')
        if shown.returncode != 0:
            missing.append(rel)
        elif shown.stdout != current:
            mismatches.append(rel)
    if missing or mismatches:
        raise ValueError('source-closure-mismatch:missing=%s:mismatch=%s' % (
            ','.join(missing), ','.join(mismatches)))
    return {'ok': True, 'mode': 'git', 'revision': rev,
            'files': len(CODE_PATHS), 'missing': [], 'mismatches': []}


def verify_image(repo, source_root, rev=None):
    release = rev or ''
    marker = os.path.join(source_root, '.release-commit')
    try:
        image_rev = open(marker, encoding='utf-8').read().strip()
    except OSError as e:
        raise ValueError('image-release-revision-missing') from e
    if not REV_RE.match(image_rev):
        raise ValueError('image-release-revision-invalid')
    if release and release != image_rev:
        raise ValueError('configured-revision-not-image-release')
    mismatches, missing = [], []
    for rel in CODE_PATHS:
        a = os.path.join(repo, *rel.split('/'))
        b = os.path.join(source_root, *rel.split('/'))
        try:
            left = open(a, 'rb').read()
            right = open(b, 'rb').read()
        except OSError:
            missing.append(rel)
            continue
        if not left or not right or _sha(left) != _sha(right):
            mismatches.append(rel)
    if missing or mismatches:
        raise ValueError('image-source-mismatch:missing=%s:mismatch=%s' % (
            ','.join(missing), ','.join(mismatches)))
    return {'ok': True, 'mode': 'image', 'revision': image_rev,
            'files': len(CODE_PATHS), 'missing': [], 'mismatches': []}


def verify_configured(repo, env=None):
    env = env if env is not None else os.environ
    rev = env.get('AIRCON_PRICE_SOURCE_REVISION') or ''
    if not rev:
        raise ValueError('source-revision-unconfigured')
    repo = env.get('AIRCON_PRICE_SOURCE_REPO') or repo
    if os.path.isdir(os.path.join(repo, '.git')):
        return verify_git(repo, rev)
    source_root = env.get('AIRCON_PRICE_SOURCE_ROOT') or '/opt/aircon-src'
    return verify_image(repo, source_root, rev)


def main(argv=None):
    ap = argparse.ArgumentParser(description='verify fixed price source pin')
    ap.add_argument('--mode', choices=['git', 'image'], required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--source-root', default=None)
    ap.add_argument('--rev', default=None)
    args = ap.parse_args(argv)
    try:
        if args.mode == 'git':
            result = verify_git(args.repo, args.rev or '')
        else:
            result = verify_image(args.repo, args.source_root or '/opt/aircon-src',
                                  args.rev)
    except (ValueError, OSError) as e:
        print('❌ price source pin failed: ' + str(e), file=sys.stderr)
        return 2
    print('✅ price source pin ok: mode=%s revision=%s files=%s' % (
        result['mode'], result['revision'], result['files']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
