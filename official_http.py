#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict official-site transport shared by the catalogue adapters.

The production image intentionally runs curl (added to the runtime image with
CA certificates) because some official hosts reject urllib's HTTP/1.1 client.
This module is the only curl boundary:

- HTTPS only, including redirects; allowed host and path prefixes are checked
  before the request and again on the effective URL;
- bounded timeout, redirect count and response size;
- a missing executable is a distinct ``dependency-missing`` failure, never a
  cache miss, parse error or coverage pending;
- errors are reduced to a stable safe code.  Raw URLs (which may carry query
  strings), credentials and response bodies are never returned in receipts.
"""
from __future__ import annotations

import re
import subprocess
import urllib.parse

MARKER = b'\n__AIRCON_EFFECTIVE_URL__'
DEFAULT_MAX_BYTES = 25_000_000
DEFAULT_MAX_REDIRECTS = 3
_HTTP_RE = re.compile(r'^http-([0-9]{3})$')


class OfficialTransportError(RuntimeError):
    def __init__(self, reason, detail=''):
        self.reason = reason
        self.detail = str(detail)[:160]
        super().__init__(reason)


def _validate(url, allowed_hosts, path_prefixes):
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme != 'https':
        raise OfficialTransportError('insecure-url')
    if parsed.hostname not in allowed_hosts:
        raise OfficialTransportError('host-not-approved')
    if path_prefixes and not any((parsed.path or '').startswith(p)
                                 for p in path_prefixes):
        raise OfficialTransportError('path-not-approved')
    return parsed


def curl_bytes(url, *, allowed_hosts, path_prefixes=(), timeout=40,
               max_bytes=DEFAULT_MAX_BYTES, max_redirects=DEFAULT_MAX_REDIRECTS,
               user_agent=None, runner=None):
    """Fetch a page with strict TLS/redirect/effective-URL/size checks."""
    _validate(url, allowed_hosts, path_prefixes)
    fmt = (MARKER.decode('ascii') + '%{http_code} %{url_effective}').encode('ascii')
    argv = [
        'curl', '--fail', '--silent', '--show-error', '--location',
        '--proto', '=https', '--proto-redir', '=https',
        '--max-redirs', str(max_redirects),
        '--max-time', str(timeout), '--max-filesize', str(max_bytes),
        '--write-out', fmt.decode('utf-8'),
    ]
    if user_agent:
        argv += ['--user-agent', user_agent]
    argv.append(url)
    run = runner or subprocess.run
    try:
        proc = run(argv, capture_output=True, timeout=timeout + 10)
    except FileNotFoundError as exc:
        raise OfficialTransportError('dependency-missing', 'curl executable missing') from exc
    except subprocess.TimeoutExpired as exc:
        raise OfficialTransportError('timeout') from exc
    except OSError as exc:
        raise OfficialTransportError('transport-error', type(exc).__name__) from exc
    stdout = proc.stdout or b''
    stderr = (proc.stderr or b'').decode('utf-8', 'replace').lower()
    if MARKER not in stdout:
        if 'ssl' in stderr or 'certificate' in stderr or 'tls' in stderr:
            raise OfficialTransportError('tls-error')
        if proc.returncode == 63:
            raise OfficialTransportError('size-limit')
        raise OfficialTransportError('transport-error', f'rc={proc.returncode}')
    body, meta = stdout.rsplit(MARKER, 1)
    try:
        status_text, effective = meta.decode('utf-8', 'replace').strip().split(' ', 1)
        status = int(status_text)
        _validate(effective, allowed_hosts, path_prefixes)
    except (ValueError, OfficialTransportError) as exc:
        raise OfficialTransportError('integrity-error', 'effective URL/status invalid') from exc
    if status < 200 or status >= 300:
        raise OfficialTransportError(f'http-{status}', 'http status')
    if len(body) > max_bytes:
        raise OfficialTransportError('size-limit')
    if proc.returncode != 0:
        if 'ssl' in stderr or 'certificate' in stderr or 'tls' in stderr:
            raise OfficialTransportError('tls-error')
        raise OfficialTransportError('transport-error', f'rc={proc.returncode}')
    return body, effective


def safe_reason(exc):
    """Stable receipt code; never include URL/query/credential text."""
    if isinstance(exc, OfficialTransportError):
        return exc.reason
    text = f'{type(exc).__name__}: {exc}'.lower()
    if 'certificate' in text or 'ssl' in text or 'tls' in text:
        return 'tls-error'
    if 'pagination' in text or 'pagecount' in text or 'total changed' in text:
        return 'pagination-error'
    if 'nuxt' in text or 'json' in text or 'parse' in text:
        return 'parse-error'
    if isinstance(exc, ValueError):
        return 'integrity-error'
    return 'transport-error'


def is_coverage_pending_reason(reason):
    return reason in ('http-404', 'http-410')
