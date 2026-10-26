#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crawl a local static site and verify internal navigation, assets, downloads, fragments.

The private site is a generated static tree, so the same check can be run before
deployment without touching production. External URLs are inventoried but never
fetched by default: an anti-bot/transport error on an external site must not be
reported as an internal broken link.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urldefrag, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIRED = ['index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv', 'metadata.json']


class LinkCollector(HTMLParser):
    ATTRS = {
        'a': ('href',), 'link': ('href',), 'script': ('src',), 'img': ('src',),
        'source': ('src', 'srcset'), 'video': ('src', 'poster'),
        'audio': ('src',), 'object': ('data',), 'embed': ('src',),
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for attr in self.ATTRS.get(tag.lower(), ()):
            value = (attrs.get(attr) or '').strip()
            if not value:
                continue
            if attr == 'srcset':
                for part in value.split(','):
                    candidate = part.strip().split(' ')[0]
                    if candidate:
                        self.links.append((tag, attr, candidate))
            else:
                self.links.append((tag, attr, value))


class _RedirectRecorder(HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _ascii_url(url: str) -> str:
    u = urlparse(url)
    path = quote(unquote(u.path), safe='/%')
    return urlunparse((u.scheme, u.netloc, path, u.params, u.query, ''))


def _fetch(url: str, timeout: float = 20) -> dict:
    url = _ascii_url(url)
    rec = _RedirectRecorder()
    opener = build_opener(rec)
    req = Request(url, headers={'User-Agent': 'aircon-site-link-check/1.0'})
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read()
            return {
                'status': getattr(r, 'status', 200),
                'finalUrl': r.geturl(),
                'redirects': rec.chain,
                'contentType': (r.headers.get('Content-Type') or '').split(';')[0].strip().lower(),
                'bytes': len(body),
                'body': body,
            }
    except HTTPError as e:
        body = e.read() if e.fp else b''
        return {'status': e.code, 'finalUrl': url, 'redirects': rec.chain,
                'contentType': (e.headers.get('Content-Type') or '').split(';')[0].strip().lower(),
                'bytes': len(body), 'body': body, 'error': str(e)}
    except (URLError, TimeoutError, OSError) as e:
        return {'status': None, 'finalUrl': url, 'redirects': rec.chain,
                'contentType': '', 'bytes': 0, 'body': b'', 'error': str(e)}


def _is_skippable(raw: str) -> bool:
    return raw.lower().startswith(('mailto:', 'tel:', 'javascript:', 'data:', 'sms:'))


def _norm(base: str, raw: str) -> tuple[str, str]:
    absolute = urljoin(base, raw)
    no_frag, frag = urldefrag(absolute)
    return _ascii_url(no_frag), unquote(frag)


def _ids_from_html(body: bytes) -> set[str]:
    text = body.decode('utf-8', 'replace')
    return set(re.findall(r'''\b(?:id|name)\s*=\s*["']([^"']+)["']''', text, re.I))


class FragmentProbe:
    """Use the real browser (JS adds heading ids) with static fallback."""

    def __init__(self, base: str):
        self.base = base
        self.playwright = None
        self.browser = None
        self.method = 'static'
        try:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            browser_name = os.environ.get('AIRCON_LINK_BROWSER', 'chromium')
            self.browser = getattr(self.playwright, browser_name).launch()
            self.method = f'browser:{browser_name}'
        except Exception:
            self.close()

    def close(self):
        if self.browser is not None:
            try:
                self.browser.close()
            except Exception:
                pass
        if self.playwright is not None:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.browser = None
        self.playwright = None

    def ids(self, url: str, static_body: bytes) -> set[str]:
        if self.browser is None:
            return _ids_from_html(static_body)
        page = self.browser.new_page()
        try:
            page.route('**/*', lambda route: route.continue_()
                       if route.request.url.startswith(self.base) else route.abort())
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            try:
                page.wait_for_load_state('networkidle', timeout=3000)
            except Exception:
                pass
            return set(page.eval_on_selector_all('[id]', 'els => els.map(e => e.id)'))
        finally:
            page.close()

    def fragment_ok(self, fragment: str, ids: set[str]) -> bool:
        if fragment in ids:
            return True
        key = fragment.replace('-', '').lower()
        return any(i.replace('-', '').lower() == key for i in ids)


def _server(site_root: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(site_root), **kw)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, f'http://127.0.0.1:{httpd.server_port}/'


def check(site_root: Path, required: list[str] | None = None) -> dict:
    site_root = site_root.resolve()
    if not (site_root / 'index.html').is_file():
        raise FileNotFoundError(f'index.html missing: {site_root}')
    required = required or DEFAULT_REQUIRED
    httpd, base = _server(site_root)
    base_host = urlparse(base).netloc
    probe = FragmentProbe(base)
    try:
        pages: dict[str, dict] = {}
        queue = [base]
        seen_pages: set[str] = set()
        internal: dict[str, dict] = {}
        external: dict[str, dict] = {}
        while queue:
            page_url = queue.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            got = _fetch(page_url)
            body = got.pop('body', b'')
            pages[page_url] = got
            if not (200 <= (got['status'] or 0) < 300):
                continue
            if got['contentType'] not in ('text/html', 'application/xhtml+xml'):
                continue
            collector = LinkCollector()
            collector.feed(body.decode('utf-8', 'replace'))
            for tag, attr, raw in collector.links:
                if _is_skippable(raw):
                    continue
                absolute, frag = _norm(page_url, raw)
                u = urlparse(absolute)
                item = {
                    'sourcePage': page_url, 'tag': tag, 'attribute': attr,
                    'raw': raw, 'url': absolute, 'fragment': frag,
                }
                if u.netloc != base_host:
                    external.setdefault(absolute, {
                        'url': absolute, 'sources': [],
                        'status': 'not_checked_external',
                    })['sources'].append(page_url)
                    continue
                key = absolute + (('#' + frag) if frag else '')
                if key not in internal:
                    internal[key] = {**item, 'sources': [page_url]}
                else:
                    internal[key]['sources'].append(page_url)
                if u.scheme in ('http', 'https') and u.path.lower().endswith(
                        ('.html', '.htm', '/')):
                    if absolute not in seen_pages:
                        queue.append(absolute)
                elif u.path in ('', '/'):
                    if absolute not in seen_pages:
                        queue.append(absolute)

        broken: list[dict] = []
        fetch_cache: dict[str, dict] = {}
        for key, item in internal.items():
            url = item['url']
            if url not in fetch_cache:
                fetch_cache[url] = _fetch(url)
            got = dict(fetch_cache[url])
            body = got.pop('body', b'')
            fragment = item.get('fragment') or ''
            frag_ok = True
            if fragment:
                if got['status'] == 200 and got['contentType'] in (
                        'text/html', 'application/xhtml+xml'):
                    frag_ok = probe.fragment_ok(fragment, probe.ids(url, body))
                else:
                    frag_ok = True  # non-HTML downloads cannot own an HTML fragment
            item.update(got)
            item['fragmentOk'] = frag_ok
            if not (200 <= (got['status'] or 0) < 300) or not frag_ok:
                broken.append(item)

        required_results = []
        for rel in required:
            url = urljoin(base, quote(rel))
            got = _fetch(url)
            got.pop('body', None)
            ok = 200 <= (got['status'] or 0) < 300
            required_results.append({'path': rel, 'url': url, 'ok': ok, **got})
            if not ok:
                broken.append({'kind': 'required-download', 'path': rel, 'url': url, **got})

        # Content-type sanity for the four public downloads.
        type_expectations = {
            'index.html': ('text/html',),
            '空調對比報告.pdf': ('application/pdf',),
            'emsd_空調能源標籤.csv': ('text/csv', 'application/csv', 'application/octet-stream', 'text/plain'),
            'metadata.json': ('application/json', 'text/json', 'text/plain'),
        }
        for row in required_results:
            expected = type_expectations.get(row['path'])
            if row['ok'] and expected and row['contentType'] not in expected:
                broken.append({'kind': 'required-download-type', 'path': row['path'],
                               'contentType': row['contentType'], 'expected': expected})

        return {
            'ok': not broken,
            'siteRoot': str(site_root),
            'fragmentMethod': probe.method,
            'pagesCrawled': len(seen_pages),
            'internalLinks': len(internal),
            'broken': broken,
            'external': list(external.values()),
            'externalCount': len(external),
            'requiredDownloads': required_results,
        }
    finally:
        probe.close()
        httpd.shutdown()
        httpd.server_close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='內部網站連結／資產／下載／fragment 檢查')
    ap.add_argument('--site-root', default=str(ROOT))
    ap.add_argument('--report', default=None)
    ap.add_argument('--require', nargs='*', default=None)
    args = ap.parse_args(argv)
    result = check(Path(args.site_root), args.require)
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
    print(json.dumps({k: result[k] for k in
                      ('ok', 'fragmentMethod', 'pagesCrawled', 'internalLinks',
                       'externalCount')}, ensure_ascii=False))
    print('required:', ', '.join(
        f"{r['path']}={r['status']}" for r in result['requiredDownloads']))
    for item in result['broken'][:30]:
        print(f"❌ {item.get('sourcePage', item.get('kind', ''))} -> "
              f"{item.get('raw', item.get('path', ''))} "
              f"({item.get('status')}, fragmentOk={item.get('fragmentOk')})",
              file=sys.stderr)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
