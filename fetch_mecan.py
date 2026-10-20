#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Target-aware MECAN Hong Kong official product-page adapter.

Only the seven EMSD queue models are addressed.  A completed model must have
the exact model in the official product title, a parsed specification table,
the full response SHA-256, fetch time and a validated HTTPS effective URL.
Raw response bytes are written outside the public candidate when --raw-dir is
supplied; the JSON output contains only safe hashes/metadata/extracted fields.
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
import urllib.error
import urllib.parse

from crawl_utils import emit_fetch_receipt, html_to_text, save_json

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(BASE, 'mecan_official.json')
TARGETS = {
    'MC07WVRQW': 'https://mecanhk.com/product/mc07wvrqw-3-4-inverter-window-aircon/',
    'MC09WVRQW': 'https://mecanhk.com/product/mc09wvrqw-1-inverter-window-aircon/',
    'MC07VXH': 'https://mecanhk.com/product/mc07vxh-3-4-inverter-window-aircon/',
    'MC09VXH': 'https://mecanhk.com/product/mc09vxh-1-inverter-window-aircon/',
    'MC12VXH': 'https://mecanhk.com/product/mc12vxh-1-5-inverter-window-aircon/',
    'MC18VXH': 'https://mecanhk.com/product/mc18vxh-2-inverter-window-aircon/',
    'MC24VXH': 'https://mecanhk.com/product/mc24vxh-2-5-inverter-window-aircon/',
}
ALLOWED_HOSTS = frozenset({'mecanhk.com', 'www.mecanhk.com'})
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36'
URL_MARKER = b'\n__AIRCON_EFFECTIVE_URL__'
REQUIRED_FIELDS = ('size', 'weight', 'btu', 'energy', 'gas', 'hp', 'wifi', 'remote', 'warranty')


class OfficialIntegrityError(RuntimeError):
    pass


def _now():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _validate_url(url):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != 'https' or parsed.hostname not in ALLOWED_HOSTS:
        raise OfficialIntegrityError('effective URL is not an approved MECAN HTTPS host')
    if not parsed.path.startswith('/product/'):
        raise OfficialIntegrityError('effective URL is not a MECAN product page')
    return parsed


def _curl_bytes(url):
    """Strict-TLS curl fetch with HTTPS-only redirects and effective-URL proof.

    The official host rejects urllib's HTTP/1.1 client at the WAF; curl uses the
    same public host with full certificate and hostname verification.
    """
    _validate_url(url)
    proc = subprocess.run(
        ['curl', '--fail', '--silent', '--show-error', '--location',
         '--proto', '=https', '--proto-redir', '=https', '--max-redirs', '3',
         '--max-time', '20', '--user-agent', UA,
         '--write-out', '\n__AIRCON_EFFECTIVE_URL__%{url_effective}', url],
        capture_output=True, timeout=30)
    if proc.returncode != 0:
        detail = proc.stderr[:160].decode('utf-8', 'replace').strip()
        raise RuntimeError(f'curl rc={proc.returncode}: {detail}')
    if URL_MARKER not in proc.stdout:
        raise OfficialIntegrityError('curl did not return an effective URL')
    body, effective = proc.stdout.rsplit(URL_MARKER, 1)
    effective = effective.decode('utf-8', 'replace').strip()
    _validate_url(effective)
    return body, effective


def _get_bytes(url):
    return _curl_bytes(url)


def _extract(html, model):
    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I)
    if not title_match:
        raise ValueError('official page has no title')
    title = html_to_text(title_match.group(1)).strip()
    if not re.search(r'(?<![A-Z0-9])' + re.escape(model) + r'(?![A-Z0-9])',
                     title, re.I):
        raise ValueError('official product title does not name the exact target model')
    text = html_to_text(html)
    start_match = re.search(r'型號\s*' + re.escape(model) + r'(?![A-Z0-9])',
                            text, re.I)
    if not start_match:
        raise ValueError('official specification table does not name the exact target model')
    tail_match = re.search(r'產品目錄|相關\s*型號', text[start_match.end():], re.I)
    end = start_match.end() + tail_match.start() if tail_match else len(text)
    region = text[start_match.start():end]
    pre_table = text[title_match.end():start_match.start()]

    def first(pattern):
        match = re.search(pattern, region, re.I)
        return match.group(1).strip() if match else ''

    size = first(r'尺寸\s*\([^)]*\)\s*毫米\s*([0-9.]+\s*[x×]\s*[0-9.]+\s*[x×]\s*[0-9.]+)')
    weight = first(r'淨重\s*公斤\s*([0-9.]+)')
    btu = first(r'冷凍能力\s*BTU\s*/\s*h\s*([0-9,]+)')
    energy = first(r'能源標籤級別\s*([0-9])')
    gas = first(r'雪種\s*(R32|R410A)')
    hp = first(r'匹數\s*([0-9./]+)')
    if not (size and weight and btu and energy and gas):
        raise ValueError('official specification table lacks required fields')
    if not re.match(r'^[0-9.]+\s*[x×]\s*[0-9.]+\s*[x×]\s*[0-9.]+$', size):
        raise ValueError('official size field is not parseable')
    wm = re.search(r'全機\s*([0-9]+年)\s*.*?壓縮機\s*([0-9]+年)', region, re.I | re.S)
    title_hp = re.search(r'[（(]\s*([0-9./]+)\s*匹\s*[）)]', title)
    return {
        'size': re.sub(r'\s*[x×]\s*', '×', size),
        'weight': weight + 'kg',
        'btu': btu,
        'energy': energy + '級',
        'gas': gas,
        'hp': hp or (title_hp.group(1) if title_hp else ''),
        'wifi': bool(re.search(r'Wi-?Fi|WIFI', region + ' ' + pre_table, re.I)),
        'remote': '遙控' in region,
        'warranty': f'{wm.group(1)}/{wm.group(2)}' if wm else '',
        'title': title,
    }


def _reason(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f'http-{exc.code}'
    if isinstance(exc, OfficialIntegrityError):
        return 'integrity-error'
    text = f'{type(exc).__name__}: {exc}'
    if isinstance(exc, ValueError):
        return 'parse-error'
    if 'SSL' in text or 'certificate' in text.lower() or 'TLS' in text:
        return 'tls-error'
    return 'transport-error'



def _valid_utc(value, *, now=None):
    if not isinstance(value, str) or not value.endswith('Z'):
        return False
    try:
        dt = _dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return dt <= now + _dt.timedelta(minutes=5)


def _cache_trusted(item, model):
    """Validate a reusable cache entry without trusting rawSha256 alone."""
    if not isinstance(item, dict):
        return False
    ev = item.get('sourceEvidence')
    if not isinstance(ev, dict):
        return False
    target_url = TARGETS.get(model)
    if ev.get('model') != model or not target_url:
        return False
    url = ev.get('url')
    if not isinstance(url, str) or url != target_url or item.get('url') != url:
        return False
    if urllib.parse.urlparse(url).hostname not in ALLOWED_HOSTS \
            or not urllib.parse.urlparse(url).path.startswith('/product/'):
        return False
    if ev.get('host') not in ALLOWED_HOSTS or ev.get('source') != 'MECAN AIRCONDITIONING (HONG KONG) LIMITED':
        return False
    if not _valid_utc(ev.get('fetchedAt')):
        return False
    if not re.fullmatch(r'[0-9a-f]{64}', str(ev.get('rawSha256', ''))):
        return False
    fields = ev.get('fields')
    if not isinstance(fields, dict):
        return False
    for key in REQUIRED_FIELDS:
        if key not in item or key not in fields or item.get(key) != fields.get(key):
            return False
    if not all(item.get(k) for k in ('size', 'weight', 'btu', 'energy', 'gas')):
        return False
    title = item.get('title')
    if not isinstance(title, str) or not re.search(
            r'(?<![A-Z0-9])' + re.escape(model) + r'(?![A-Z0-9])', title, re.I):
        return False
    return True


def fetch_targets(existing=None, *, raw_dir=None):
    existing = dict(existing or {})
    results = {}
    already = []
    failed = []
    failure_reasons = {}
    attempted = 0
    for model, url in TARGETS.items():
        old = existing.get(model)
        if _cache_trusted(old, model):
            if raw_dir:
                raw_path = os.path.join(raw_dir, f'{model}.html')
                if os.path.isfile(raw_path):
                    raw = open(raw_path, 'rb').read()
                    if hashlib.sha256(raw).hexdigest() != old['sourceEvidence']['rawSha256']:
                        failed.append(model)
                        failure_reasons[model] = 'integrity-error'
                        continue
            already.append(model)
            continue
        attempted += 1
        try:
            raw, effective_url = _get_bytes(url)
            digest = hashlib.sha256(raw).hexdigest()
            fields = _extract(raw.decode('utf-8', 'replace'), model)
            stamp = _now()
            item = dict(fields)
            item['url'] = effective_url
            item['sourceEvidence'] = {
                'source': 'MECAN AIRCONDITIONING (HONG KONG) LIMITED',
                'host': urllib.parse.urlparse(effective_url).hostname,
                'model': model,
                'url': effective_url,
                'fetchedAt': stamp,
                'rawSha256': digest,
                'fields': {k: v for k, v in fields.items() if k != 'title'},
            }
            results[model] = item
            if raw_dir:
                os.makedirs(raw_dir, exist_ok=True)
                with open(os.path.join(raw_dir, f'{model}.html'), 'wb') as f:
                    f.write(raw)
            print(f'{model}: {fields["size"]} | {fields["weight"]} | '
                  f'{fields["gas"]} | {fields["energy"]}')
        except Exception as e:  # noqa: BLE001 - classified
            failed.append(model)
            failure_reasons[model] = _reason(e)
            print(f'{model}: ERR {failure_reasons[model]} {str(e)[:100]}', file=sys.stderr)
        time.sleep(3.0)
    return results, already, failed, failure_reasons, attempted


def main(argv=None):
    ap = argparse.ArgumentParser(description='MECAN official window-aircon adapter')
    ap.add_argument('--raw-dir', default=os.environ.get('AIRCON_OFFICIAL_RAW_DIR'))
    ap.add_argument('--out', default=OUTPUT)
    args = ap.parse_args(argv)
    existing = {}
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding='utf-8') as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}
    results, already, failed, reasons, attempted = fetch_targets(
        existing, raw_dir=args.raw_dir)
    succeeded = list(results)
    covers = sorted(set(succeeded) | set(already))
    times = []
    for model in covers:
        item = results.get(model) or existing.get(model)
        try:
            ev = item.get('sourceEvidence') if isinstance(item, dict) else None
            if isinstance(ev, dict) and _valid_utc(ev.get('fetchedAt')):
                times.append(ev['fetchedAt'])
        except (TypeError, AttributeError):
            pass
    extra = ({'sourceAcquiredAt': max(times), 'sourceAcquiredAtKind': 'verified-official-fetch'}
             if times else None)
    emit_fetch_receipt(
        'fetch_mecan.py', attempted, len(succeeded), len(failed),
        succeeded_models=succeeded, already_verified=already,
        failed_models=failed, covers=covers, skipped=len(already),
        failure_reasons=reasons, extra=extra if times else None)
    if failed:
        print(f'❌ MECAN {len(failed)}/{attempted} 個官方目標失敗；保留既有快照',
              file=sys.stderr)
        return 1
    merged = dict(existing)
    merged.update(results)
    if merged != existing:
        save_json(args.out, merged, indent=1)
    print(f'完成 MECAN：succeeded={len(succeeded)} skipped={len(already)} total={len(merged)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
