#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Official Gilman agent catalogue scanner for GREE / MHI targets.

The scanner reads the real Nuxt ``__NUXT_DATA__`` payload and its pagination
metadata.  It traverses pageCount pages, verifies each response page identity
and pageCount/total consistency, and records page URL/hash/time boundaries.
A catalogue page is evidence only; it never completes a model without a
separately verified product-detail/specification page.
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
from collections import Counter

from crawl_utils import emit_fetch_receipt, norm_model, save_json
import official_http

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, 'gilman_official.json')
BRANDS = {
    'GREE': {
        'models': ['GEH63ZE/GEH63ZC', 'GWT07ES', 'GWT09ES', 'GWT12ES'],
        'url': 'https://www.gilman-group.com/zh-hk/brand/%E6%A0%BC%E5%8A%9B',
    },
    'MITSUBISHIHEAVY': {
        'models': ['WRK07MIZH1', 'WRK09MIZH1', 'WRK18MIZH1'],
        'url': 'https://www.gilman-group.com/zh-hk/brand/%E4%B8%89%E8%8F%B1%E9%87%8D%E5%B7%A5',
    },
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36'
ALLOWED_HOST = 'www.gilman-group.com'
MARKER = b'\n__AIRCON_EFFECTIVE_URL__'
MAX_PAGES = 50


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _validate_url(url):
    p = urllib.parse.urlparse(str(url))
    if p.scheme != 'https' or p.hostname != ALLOWED_HOST or not p.path.startswith('/zh-hk/brand/'):
        raise RuntimeError('effective URL is not an approved Gilman brand page')


def _curl(url):
    _validate_url(url)
    return official_http.curl_bytes(
        url, allowed_hosts={ALLOWED_HOST}, path_prefixes=('/zh-hk/brand/',),
        timeout=40, user_agent=UA)


def _decode_nuxt(html):
    """Decode Nuxt's devalue flat array without treating every integer as a ref."""
    match = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError('Nuxt payload missing')
    values = json.loads(match.group(1))
    memo = {}

    def hydrate(index):
        if not isinstance(index, int) or isinstance(index, bool) or index < 0 or index >= len(values):
            raise ValueError('Nuxt reference out of range')
        if index in memo:
            return memo[index]
        memo[index] = None
        value = values[index]
        if isinstance(value, list):
            if value and value[0] == 'Reactive':
                result = hydrate(value[1])
            elif value and value[0] == 'Set':
                raw = value[1] if len(value) > 1 else []
                result = [hydrate(item) for item in raw] if isinstance(raw, list) else []
            elif value and value[0] == 'Date' and len(value) > 1:
                result = value[1]
            elif value and value[0] in ('null', 'undefined'):
                result = None
            else:
                result = [hydrate(item) for item in value]
        elif isinstance(value, dict):
            result = {key: hydrate(item) for key, item in value.items()}
        else:
            result = value
        memo[index] = result
        return result

    root = hydrate(0)
    if not isinstance(root, dict):
        raise ValueError('Nuxt root is not an object')
    return root


def _page_meta(html, expected_url):
    root = _decode_nuxt(html)
    data = root.get('data')
    products = data.get('products') if isinstance(data, dict) else None
    if not isinstance(products, dict):
        raise ValueError('Nuxt products block missing')
    meta = products.get('meta')
    pagination = meta.get('pagination') if isinstance(meta, dict) else None
    if not isinstance(pagination, dict):
        raise ValueError('Nuxt pagination missing')
    page = pagination.get('page')
    size = pagination.get('pageSize')
    count = pagination.get('pageCount')
    total = pagination.get('total')
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0
               for x in (page, size, count, total)):
        raise ValueError('Nuxt pagination types invalid')
    if size <= 0 or count <= 0 or count > MAX_PAGES or total < 0:
        raise ValueError('Nuxt pagination bounds invalid')
    items = products.get('data')
    if not isinstance(items, list):
        raise ValueError('Nuxt product list missing')
    return {'url': expected_url, 'page': page, 'pageSize': size,
            'pageCount': count, 'total': total, 'items': items}


def _target_hits(items, target_models):
    """Exact alphanumeric-token matching, including indoor/outdoor pairs.

    Separators/spaces must not make an exact target permanently unmatchable.
    Substring matching is deliberately avoided (GWT07ES must not match a
    longer GWT07ESX model).
    """
    hits = set()
    for item in items:
        attrs = item.get('attributes') if isinstance(item, dict) else None
        if not isinstance(attrs, dict):
            continue
        haystack = ' '.join(str(attrs.get(k, '')) for k in
                            ('model_number', 'display_model_number',
                             'name_en', 'name_tc', 'name_sc', 'slug'))
        tokens = {norm_model(t) for t in re.findall(r'[A-Za-z0-9]+', haystack)}
        tokens.discard('')
        for model in target_models:
            parts = [norm_model(p) for p in str(model).split('/') if norm_model(p)]
            combined = norm_model(model)
            if not parts:
                continue
            if len(parts) == 1:
                matched = parts[0] in tokens
            else:
                # Accept both "GEH63ZE / GEH63ZC" and the slug-style
                # "GEH63ZEGEH63ZC" without accepting a single approximation.
                matched = all(p in tokens for p in parts) or combined in tokens
            if matched:
                hits.add(model)
    return hits


def _load_cache_manifest(path):
    if not path:
        return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    rows = data.get('pages') if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError('cache manifest pages invalid')
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('cache manifest row invalid')
        url = row.get('url')
        path = row.get('path')
        sha = row.get('sha256')
        fetched = row.get('fetchedAt')
        if not isinstance(url, str) or not isinstance(path, str) \
                or not re.fullmatch(r'[0-9a-f]{64}', str(sha or '')):
            raise ValueError('cache manifest identity invalid')
        if fetched is not None:
            try:
                _dt.datetime.fromisoformat(fetched.replace('Z', '+00:00'))
            except (ValueError, AttributeError) as e:
                raise ValueError('cache manifest fetchedAt invalid') from e
        if url in out:
            raise ValueError('cache manifest duplicate URL')
        out[url] = {'path': path, 'sha256': sha, 'fetchedAt': fetched}
    return out


def _cache_page(cache, url):
    row = cache.get(url)
    if row is None:
        raise RuntimeError('cache page unavailable')
    path = row['path']
    if not os.path.isfile(path):
        raise RuntimeError('cache page missing')
    raw = open(path, 'rb').read()
    if hashlib.sha256(raw).hexdigest() != row['sha256']:
        raise ValueError('cache page hash mismatch')
    return raw, (row.get('fetchedAt') or None)


def scan(cache_dir=None, cache_manifest=None):
    cache = _load_cache_manifest(cache_manifest)
    pages = {}
    targets = set()
    attempted = 0
    failures = []
    reasons = {}
    catalog = {}
    for brand, spec in BRANDS.items():
        rows = []
        expected_pages = None
        expected_total = None
        for page in range(1, MAX_PAGES + 1):
            if expected_pages is not None and page > expected_pages:
                break
            url = spec['url'] if page == 1 else f"{spec['url']}?page={page}"
            attempted += 1
            try:
                cached = None
                if cache:
                    cached = cache.get(url)
                if cached:
                    raw, stamp = _cache_page(cache, url)
                    effective = url
                elif cache_dir or cache_manifest:
                    raise RuntimeError('cache manifest lacks page')
                else:
                    raw, effective = _curl(url)
                    stamp = _now()
                meta = _page_meta(raw.decode('utf-8', 'replace'), url)
                if meta['page'] != page:
                    raise ValueError(f'page identity mismatch: requested {page}, got {meta["page"]}')
                if expected_pages is None:
                    expected_pages = meta['pageCount']
                    expected_total = meta['total']
                if meta['pageCount'] != expected_pages or meta['total'] != expected_total:
                    raise ValueError('pageCount/total changed during traversal')
                expected_items = meta['pageSize'] if page < expected_pages else expected_total - meta['pageSize'] * (expected_pages - 1)
                if len(meta['items']) != expected_items:
                    raise ValueError('page product count mismatch')
                rows.append({'page': page, 'url': effective,
                             'fetchedAt': stamp, 'rawSha256': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                             'bytes': len(raw), 'pageCount': meta['pageCount'],
                             'pageSize': meta['pageSize'], 'total': meta['total'],
                             'itemCount': len(meta['items']),
                             'targetsFound': sorted(_target_hits(meta['items'], spec['models']))})
                targets.update(rows[-1]['targetsFound'])
            except Exception as e:  # noqa: BLE001
                failures.append(f'{brand}:page{page}')
                reason = official_http.safe_reason(e)
                reasons[f'{brand}:page{page}'] = reason
                break
        catalog[brand] = {'source': 'GILMAN GROUP official agent catalogue',
                          'url': spec['url'], 'pages': rows,
                          'pageCount': expected_pages, 'total': expected_total,
                          'targets': spec['models']}
    return catalog, targets, attempted, failures, reasons


def main(argv=None):
    ap = argparse.ArgumentParser(description='Gilman official catalogue scanner')
    ap.add_argument('--cache-dir', default=os.environ.get('AIRCON_OFFICIAL_CACHE_DIR'))
    ap.add_argument('--cache-manifest', default=os.environ.get('AIRCON_OFFICIAL_CACHE_MANIFEST'))
    ap.add_argument('--out', default=OUTPUT)
    args = ap.parse_args(argv)
    catalog, targets, attempted, failures, reasons = scan(args.cache_dir, args.cache_manifest)
    out = {'_catalog': {
        'schemaVersion': 2,
        'kind': 'gilman-official-catalogue-scan',
        'brands': catalog,
        'targetsFound': sorted(targets),
        'completedTargets': [],
        'status': 'coverage-pending' if targets else 'no-exact-target-found',
        'reason': 'targets require a separately verified product detail page',
    }}
    if not failures:
        save_json(args.out, out, indent=1)
    failure_counts = Counter(reasons.values())
    extra = {'catalogPages': attempted,
             'cataloguePagesAttempted': attempted,
             'cataloguePagesFailed': len(failures),
             'catalogueFailureReasons': dict(sorted(failure_counts.items())),
             'pageCounts': {b: v.get('pageCount') for b, v in catalog.items()},
             'targetsFound': sorted(targets),
             'sourceAcquiredAt': None,
             'sourceAcquiredAtKind': 'catalogue-only'}
    if failures:
        # A catalogue page failure means no target from that brand can be
        # verified this run.  Report real attempted/failed target counts and a
        # distinct safe cause; the old output and queue are preserved, and a
        # missing executable/TLS/HTTP failure is never disguised as coverage
        # pending.
        failed_brands = sorted({f.split(':', 1)[0] for f in failures})
        failed_targets = sorted({m for b in failed_brands for m in BRANDS[b]['models']})
        brand_reason = {}
        for brand in failed_brands:
            values = [reasons[k] for k in reasons if k.startswith(brand + ':')]
            brand_reason[brand] = values[0] if values else 'transport-error'
        model_reasons = {m: 'catalogue-' + brand_reason[b]
                         for b in failed_brands for m in BRANDS[b]['models']}
        emit_fetch_receipt('fetch_gilman.py', len(failed_targets), 0,
                           len(failed_targets),
                           succeeded_models=[], already_verified=[],
                           failed_models=failed_targets, covers=[], skipped=0,
                           failure_reasons=model_reasons, extra=extra)
        print(f'❌ Gilman catalogue {len(failures)} page(s) failed '
              f'({", ".join(sorted(failure_counts))}); existing output retained',
              file=sys.stderr)
        return 1
    emit_fetch_receipt('fetch_gilman.py', 0, 0, 0,
                       succeeded_models=[], already_verified=[], failed_models=[],
                       covers=[], skipped=0, failure_reasons={}, extra=extra)
    print(f'Gilman catalogue scanned: pages={attempted} exact_targets={sorted(targets)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
