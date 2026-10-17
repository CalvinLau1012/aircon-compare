# -*- coding: utf-8 -*-
"""Strict TLS + host-scoped reviewed intermediate regression (offline)."""
import datetime as dt
import hashlib
import os
import socket
import ssl
import sys
import threading
from pathlib import Path

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import crawl_utils  # noqa: E402


def _make_tls_fixture():
    """Generate test-only PKI in a temp dir at runtime.

    Private keys are intentionally NOT committed: a public-repo history
    credential audit must stay at zero findings.  openssl is available in the
    CI image; the generated keys exist only for this pytest process.
    """
    import subprocess
    import tempfile
    root = tempfile.mkdtemp(prefix='aircon-tls-test-')
    def run(*args):
        subprocess.run(args, cwd=root, check=True, capture_output=True)
    run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', 'root.key', '-out', 'root.pem', '-days', '3650',
        '-subj', '/CN=Aircon Test Root CA',
        '-addext', 'basicConstraints=critical,CA:TRUE',
        '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
    run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
        'intermediate.key', '-out', 'intermediate.csr',
        '-subj', '/CN=Aircon Test Intermediate CA')
    Path(root, 'intermediate.ext').write_text(
        'basicConstraints=critical,CA:TRUE,pathlen:0\n'
        'keyUsage=critical,keyCertSign,cRLSign\n', encoding='utf-8')
    run('openssl', 'x509', '-req', '-in', 'intermediate.csr', '-CA', 'root.pem',
        '-CAkey', 'root.key', '-CAcreateserial', '-out', 'intermediate.pem',
        '-days', '1825', '-extfile', 'intermediate.ext')
    run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
        'server.key', '-out', 'server.csr', '-subj', '/CN=localhost')
    Path(root, 'server.ext').write_text(
        'basicConstraints=critical,CA:FALSE\n'
        'keyUsage=critical,digitalSignature,keyEncipherment\n'
        'extendedKeyUsage=serverAuth\n'
        'subjectAltName=DNS:localhost,IP:127.0.0.1\n', encoding='utf-8')
    run('openssl', 'x509', '-req', '-in', 'server.csr', '-CA', 'intermediate.pem',
        '-CAkey', 'intermediate.key', '-CAcreateserial', '-out', 'server.pem',
        '-days', '365', '-extfile', 'server.ext')
    return root


TLS = _make_tls_fixture()


def _pem(name):
    return os.path.join(TLS, name)


def _spec(sha=None, not_after='2040-01-01T00:00:00Z', hosts=('localhost',)):
    return {
        'hosts': hosts,
        'pem': _pem('intermediate.pem'),
        'sha256': sha or hashlib.sha256(
            __import__('subprocess').run(
                ['openssl', 'x509', '-in', _pem('intermediate.pem'), '-outform', 'DER'],
                capture_output=True, check=True).stdout).hexdigest(),
        'issuer': 'Aircon Test Root CA',
        'not_after': not_after,
    }


def _server():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(_pem('server.pem'), _pem('server.key'))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(('127.0.0.1', 0))
    srv.listen(5)

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(16)
                    tls.sendall(b'ok')
            except (ssl.SSLError, OSError):
                pass

    threading.Thread(target=run, daemon=True).start()
    return srv


def _probe(context, port, hostname):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=hostname) as tls:
            tls.sendall(b'ping')
            return tls.recv(16)


def test_no_verify_helper_is_gone_and_default_is_strict():
    with pytest.raises(RuntimeError):
        crawl_utils.no_verify_ssl_context()
    ctx = crawl_utils.official_ssl_context()
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_missing_chain_rejected_without_reviewed_intermediate():
    srv = _server()
    try:
        ctx = crawl_utils.official_ssl_context(cafile=_pem('root.pem'))
        with pytest.raises(ssl.SSLCertVerificationError):
            _probe(ctx, srv.getsockname()[1], 'localhost')
    finally:
        srv.close()


def test_reviewed_intermediate_allows_missing_chain_but_keeps_hostname_and_root():
    srv = _server()
    try:
        ctx = crawl_utils.official_ssl_context(
            cafile=_pem('root.pem'), reviewed_intermediates=[_spec()],
            trusted_cafile=_pem('root.pem'))
        assert _probe(ctx, srv.getsockname()[1], 'localhost') == b'ok'
        with pytest.raises(ssl.SSLCertVerificationError):
            _probe(ctx, srv.getsockname()[1], 'wrong.example')
        # A different trust root must not accept the leaf even with the
        # intermediate loaded.
        other = crawl_utils.official_ssl_context(
            cafile=_pem('server.pem'), reviewed_intermediates=[],
            trusted_cafile=_pem('root.pem'))
        with pytest.raises(ssl.SSLCertVerificationError):
            _probe(other, srv.getsockname()[1], 'localhost')
    finally:
        srv.close()


def test_reviewed_intermediate_hash_and_expiry_pins_fail_closed():
    with pytest.raises(crawl_utils.TLSConfigError):
        crawl_utils.official_ssl_context(
            cafile=_pem('root.pem'), reviewed_intermediates=[_spec(sha='0' * 64)],
            trusted_cafile=_pem('root.pem'))
    with pytest.raises(crawl_utils.TLSConfigError):
        crawl_utils.official_ssl_context(
            cafile=_pem('root.pem'),
            reviewed_intermediates=[_spec(not_after='2020-01-01T00:00:00Z')],
            trusted_cafile=_pem('root.pem'))


def test_host_scope_registry_does_not_apply_intermediate_to_unlisted_host():
    ctx = crawl_utils.official_ssl_context_for_url('https://example.com/x')
    assert ctx.check_hostname is True
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    # The reviewed registry must not silently apply to an arbitrary host.
    assert crawl_utils._reviewed_specs_for_host('example.com') == []
