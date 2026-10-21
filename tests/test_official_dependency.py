# -*- coding: utf-8 -*-
"""Runtime official-fetch dependency and safe failure receipts."""
import json
import os
import sys
import types

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import fetch_gilman as g
import official_http
import run_official_batch as rob


def _proc(stdout=b'', stderr=b'', rc=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=rc)


def test_transport_requires_strict_tls_host_and_effective_url():
    body = b'<html>ok</html>'
    marker = official_http.MARKER + b'200 https://www.gilman-group.com/zh-hk/brand/x'
    out = official_http.curl_bytes(
        'https://www.gilman-group.com/zh-hk/brand/x',
        allowed_hosts={'www.gilman-group.com'}, path_prefixes=('/zh-hk/brand/',),
        runner=lambda argv, **kw: _proc(body + marker))
    assert out[0] == body and out[1].endswith('/zh-hk/brand/x')
    # Redirect to a different host is rejected even with HTTP 200.
    bad = official_http.MARKER + b'200 https://evil.example/x'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'}, path_prefixes=('/zh-hk/brand/',),
            runner=lambda argv, **kw: _proc(body + bad))
    assert e.value.reason == 'integrity-error'


def test_transport_classifies_real_writeout_tls_timeout_and_http():
    marker = official_http.MARKER + b'000 https://www.gilman-group.com/zh-hk/brand/x'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            path_prefixes=('/zh-hk/brand/',),
            runner=lambda *a, **k: _proc(marker, b'SSL certificate problem', 60))
    assert e.value.reason == 'tls-error'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            path_prefixes=('/zh-hk/brand/',),
            runner=lambda *a, **k: _proc(marker, b'Operation timed out', 28))
    assert e.value.reason == 'timeout'
    bad404 = official_http.MARKER + b'404 https://www.gilman-group.com/zh-hk/brand/x'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            path_prefixes=('/zh-hk/brand/',),
            runner=lambda *a, **k: _proc(b'gone' + bad404, b'', 22))
    assert e.value.reason == 'http-404'


def test_transport_classifies_missing_executable_tls_http_and_size():
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            runner=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert e.value.reason == 'dependency-missing'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            runner=lambda *a, **k: _proc(b'', b'SSL certificate problem', 60))
    assert e.value.reason == 'tls-error'
    marker = official_http.MARKER + b'404 https://www.gilman-group.com/zh-hk/brand/x'
    with pytest.raises(official_http.OfficialTransportError) as e:
        official_http.curl_bytes(
            'https://www.gilman-group.com/zh-hk/brand/x',
            allowed_hosts={'www.gilman-group.com'},
            path_prefixes=('/zh-hk/brand/',),
            runner=lambda *a, **k: _proc(b'gone' + marker, b'', 22))
    assert e.value.reason == 'http-404'


def test_gilman_missing_curl_reports_real_target_failures_and_preserves_output(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(g, 'BRANDS', {'GREE': g.BRANDS['GREE']})

    def missing(url):
        raise official_http.OfficialTransportError('dependency-missing')

    monkeypatch.setattr(g, '_curl', missing)
    out = tmp_path / 'gilman_official.json'
    out.write_text('{"_catalog":{"sentinel":true}}', encoding='utf-8')
    old = out.read_bytes()
    rc = g.main(['--out', str(out)])
    assert rc == 1
    assert out.read_bytes() == old, 'hard dependency failure must preserve old outputs'
    lines = [l for l in capsys.readouterr().out.splitlines()
             if l.startswith('AIRCON_FETCH_RECEIPT ')]
    assert lines
    receipt = json.loads(lines[-1].split(' ', 1)[1])
    assert receipt['attempted'] == 4 and receipt['failed'] == 4
    assert receipt['failedModels'] == list(g.BRANDS['GREE']['models'])
    assert set(receipt['failureReasons'].values()) == {'catalogue-dependency-missing'}
    assert 'http' not in json.dumps(receipt).lower()


def test_dependency_failure_is_hard_not_coverage_pending():
    models = {'GWT07ES': {'size': 'x'}}
    receipt = {
        'schemaVersion': 1, 'script': 'fetch_gilman.py',
        'attempted': 1, 'succeeded': 0, 'failed': 1,
        'succeededModels': [], 'failedModels': ['GWT07ES'],
        'alreadyVerified': [], 'covers': [], 'skipped': 0,
        'failureReasons': {'GWT07ES': 'catalogue-dependency-missing'},
    }
    errors = rob.validate_marker(receipt, 'fetch_gilman.py', models)
    assert any('coverage pending' in e for e in errors)
    assert rob.coverage_pending_from_marker(receipt) == set()
