#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Official Gilman agent catalogue scanner for GREE / MHI targets.

Gilman Group is the Hong Kong agent named by the EMSD provider chain.  This is
a target-aware catalogue evidence adapter: it traverses the official brand
catalogue pages and records the exact page hashes/times and every exact target
found.  It never invents an alias and never marks an unlisted target complete.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

from crawl_utils import emit_fetch_receipt, save_json

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, 'gilman_official.json')
BRANDS = {
    'GREE': {
        'models': ['GEH63ZE/GEH63ZC', 'GWT07ES', 'GWT09ES', 'GWT12ES'],
        'url': 'https://www.gilman-group.com/zh-hk/brand/%E6%A0%BC%E5%8A%9B',
        'pages': 7,
    },
    'MITSUBISHIHEAVY': {
        'models': ['WRK07MIZH1', 'WRK09MIZH1', 'WRK18MIZH1'],
        'url': 'https://www.gilman-group.com/zh-hk/brand/%E4%B8%89%E8%8F%B1%E9%87%8D%E5%B7%A5',
        'pages': 2,
    },
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36'
ALLOWED_HOST = 'www.gilman-group.com'
MARKER = b'\n__AIRCON_EFFECTIVE_URL__'


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _validate_url(url):
    p = urllib.parse.urlparse(str(url))
    if p.scheme != 'https' or p.hostname != ALLOWED_HOST or not p.path.startswith('/zh-hk/brand/'):
        raise RuntimeError('effective URL is not an approved Gilman brand page')


def _curl(url):
    _validate_url(url)
    proc = subprocess.run(
        ['curl', '--fail', '--silent', '--show-error', '--location',
         '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3',
         '--max-time', '40', '--max-filesize', '25000000',
         '--user-agent', UA, '--write-out', '\n__AIRCON_EFFECTIVE_URL__%{url_effective}', url],
        capture_output=True, timeout=50)
    if proc.returncode != 0:
        raise RuntimeError(f'curl rc={proc.returncode}: {proc.stderr[:120].decode("utf-8", "replace")}')
    if MARKER not in proc.stdout:
        raise RuntimeError('curl did not return an effective URL')
    body, eff = proc.stdout.rsplit(MARKER, 1)
    _validate_url(eff.decode('utf-8', 'replace').strip())
    return body, eff.decode('utf-8', 'replace').strip()


def _page_url(base, page):
    return base if page == 1 else f'{base}?page={page}'


def _target_hits(html):
    text = urllib.parse.unquote(html)
    return {model for brand in BRANDS.values() for model in brand['models']
            if model.replace('/', '') in text.replace('/', '')}


def scan(cache_dir=None):
    pages = {}
    targets = set()
    attempted = 0
    failures = []
    reasons = {}
    catalog = {}
    for brand, spec in BRANDS.items():
        rows = []
        for page in range(1, spec['pages'] + 1):
            url = _page_url(spec['url'], page)
            cache = None
            if cache_dir:
                candidates = [os.path.join(cache_dir, f'{brand.lower()}-page{page}.html'),
                              os.path.join(cache_dir, f'gilman-{brand.lower() if brand == "GREE" else "mhi"}{"-page"+str(page) if page > 1 else ""}.html')]
                cache = next((p for p in candidates if os.path.isfile(p)), None)
            attempted += 1
            try:
                if cache:
                    raw = open(cache, 'rb').read()
                    effective = url
                    stamp = _dt.datetime.fromtimestamp(os.stat(cache).st_mtime,
                                                       _dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                else:
                    raw, effective = _curl(url)
                    stamp = _now()
                digest = hashlib.sha256(raw).hexdigest()
                hits = _target_hits(raw.decode('utf-8', 'replace'))
                targets.update(hits)
                rows.append({'page': page, 'url': effective, 'fetchedAt': stamp,
                             'rawSha256': digest, 'bytes': len(raw),
                             'targetsFound': sorted(hits)})
            except Exception as e:  # noqa: BLE001
                failures.append(f'{brand}:page{page}')
                reasons[f'{brand}:page{page}'] = 'transport-error'
        catalog[brand] = {'source': 'GILMAN GROUP official agent catalogue',
                          'url': spec['url'], 'pages': rows,
                          'targets': spec['models']}
    return catalog, targets, attempted, failures, reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description='Gilman official catalogue scanner')
    ap.add_argument('--cache-dir', default=os.environ.get('AIRCON_OFFICIAL_CACHE_DIR'))
    ap.add_argument('--out', default=OUTPUT)
    args = ap.parse_args(argv)
    catalog, targets, attempted, failures, reasons = scan(args.cache_dir)
    # A catalogue page is evidence only; no model is marked complete until an
    # exact product detail/specification page is separately proven.
    out = {'_catalog': {
        'schemaVersion': 1,
        'kind': 'gilman-official-catalogue-scan',
        'brands': catalog,
        'targetsFound': sorted(targets),
        'completedTargets': [],
        'status': 'coverage-pending' if targets else 'no-exact-target-found',
        'reason': 'targets require a separately verified product detail page',
    }}
    if not failures:
        save_json(args.out, out, indent=1)
    emit_fetch_receipt('fetch_gilman.py', 0, 0, 0,
                       succeeded_models=[], already_verified=[], failed_models=[],
                       covers=[], skipped=0, failure_reasons={},
                       extra={'catalogPages': attempted,
                              'targetsFound': sorted(targets),
                              'sourceAcquiredAt': None,
                              'sourceAcquiredAtKind': 'catalogue-only'})
    if failures:
        print(f'❌ Gilman catalogue {len(failures)} pages failed; existing output retained',
              file=sys.stderr)
        return 1
    print(f'Gilman catalogue scanned: pages={attempted} exact_targets={sorted(targets)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
