# -*- coding: utf-8 -*-
"""部署後核對回歸（GATE-08）

- 以指定發佈事實作 expected，線上 metadata／payload hash／CSV hash 逐項一致
- 錯版本最終必須失敗（有界重試處理 CDN，但唔會 fallback 最新版假通過）
- 不受信任 host／userinfo 拒絕；GET 永唔帶 secrets
- 本地 fixture HTTP server（唔碰 live 網站）；瀏覽器 runtime Version／date／HKT 核對
"""
import importlib.util
import json
import os
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys_path_scripts = os.path.join(BASE, 'scripts')
import sys  # noqa: E402
sys.path.insert(0, sys_path_scripts)

_SPEC = importlib.util.spec_from_file_location(
    'postdeploy_mod', os.path.join(BASE, 'scripts', 'postdeploy_check.py'))
postdeploy = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(postdeploy)

_GM_SPEC = importlib.util.spec_from_file_location(
    'gen_metadata_pd', os.path.join(BASE, 'scripts', 'gen-metadata.py'))
gen_metadata = importlib.util.module_from_spec(_GM_SPEC)
_GM_SPEC.loader.exec_module(gen_metadata)

FILES = ['index.html', '空調對比報告.pdf', 'emsd_空調能源標籤.csv']
META = {
    'schemaVersion': '1.0.0', 'version': '1.2.9', 'build': 'B20260921.TEST',
    'commit': 'a' * 40, 'deployTime': '2026-09-21T03:00:00Z',
    'workflowRunId': '42', 'deploymentType': 'release',
    'datasetDate': '2026-09-21', 'datasetDateBasis': 'retrieval-date-fallback',
    'datasetRetrievedAt': '2026-09-20T18:59:57Z',
    'datasetSourceUrl': 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php',
    'datasetSnapshotId': 'emsd-2026-09-21-abc', 'recordCount': 1,
}


@pytest.fixture
def site(tmp_path):
    """生成自洽 fixture：expected metadata、PDF（由同一 metadata 重建）、payload hash。"""
    import hashlib
    # index.html 用 repo 生成物（瀏覽器測試需要真實 DOM）；CSV 用真 repo 快照
    shutil.copy2(os.path.join(BASE, 'index.html'), os.path.join(tmp_path, 'index.html'))
    shutil.copy2(os.path.join(BASE, 'emsd_空調能源標籤.csv'),
                 os.path.join(tmp_path, 'emsd_空調能源標籤.csv'))
    csv_path = os.path.join(tmp_path, 'emsd_空調能源標籤.csv')
    digest = 'sha256:' + hashlib.sha256(open(csv_path, 'rb').read()).hexdigest()
    meta = dict(META)
    meta['datasetHash'] = digest
    with open(os.path.join(tmp_path, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    # PDF 必須由 fixture metadata 重建，令「PDF 同 metadata 同一事實」可驗證
    import generate_pdf
    generate_pdf.build_pdf(os.path.join(tmp_path, '空調對比報告.pdf'),
                           metadata_path=os.path.join(tmp_path, 'metadata.json'))
    meta['releasePayloadHash'] = gen_metadata.hash_files(FILES, base=str(tmp_path))
    with open(os.path.join(tmp_path, 'metadata.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    manifest = {'files': FILES}
    with open(os.path.join(tmp_path, 'deploy_payload.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    return tmp_path, meta


def serve(directory, handler_cls=None):
    class Handler(handler_cls or SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory, **kwargs)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{srv.server_address[1]}/'


def expected_paths(tmp_path):
    return (os.path.join(tmp_path, 'metadata.json'),
            os.path.join(tmp_path, 'deploy_payload.json'),
            os.path.join(tmp_path, 'postdeploy-report.json'))


def test_trusted_base_rules():
    assert postdeploy.ensure_trusted_base('http://127.0.0.1:8080/')
    assert postdeploy.ensure_trusted_base('https://calvinlau1012.github.io/aircon-compare/')
    for bad in ('https://evil.example.com/', 'http://calvinlau1012.github.io/x/',
                'ftp://calvinlau1012.github.io/', 'https://user:pass@calvinlau1012.github.io/'):
        with pytest.raises(ValueError):
            postdeploy.ensure_trusted_base(bad)
    with pytest.raises(ValueError):
        postdeploy.ensure_trusted_base('https://other.example.com/')


def test_hkt_display_fractional_seconds():
    assert postdeploy.hkt_display('2026-09-21T03:00:00Z') == '2026-09-21 11:00'
    assert postdeploy.hkt_display('2026-09-21T19:28:14.500Z') == '2026-09-22 03:28'
    assert postdeploy.hkt_display('2026-09-21 03:00:00Z') is None


def test_postdeploy_http_checks_pass(site):
    tmp_path, meta = site
    srv, base = serve(str(tmp_path))
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '2',
                              '--no-browser'])
        assert rc == 0, open(report, encoding='utf-8').read()
        data = json.load(open(report, encoding='utf-8'))
        assert data['ok'] is True and not data['failures']
    finally:
        srv.shutdown()


def test_postdeploy_wrong_online_version_fails(site):
    tmp_path, meta = site
    meta_path, manifest_path, report = expected_paths(tmp_path)
    expected_copy = os.path.join(tmp_path, 'expected-metadata.json')
    shutil.copy2(meta_path, expected_copy)
    bad = dict(meta)
    bad['version'] = '9.9.9'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(bad, f, ensure_ascii=False)
    srv, base = serve(str(tmp_path))
    try:
        rc = postdeploy.main(['--expected-metadata', expected_copy, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '2',
                              '--no-browser'])
        assert rc == 1, '錯版本最終必須失敗，唔可以回退最新版假通過'
        data = json.load(open(report, encoding='utf-8'))
        assert any('metadata.version' in c['check'] for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_full_object_equality_catches_non_required_field(site):
    """8 欄位以外（recordCount 等）被改都要失敗"""
    tmp_path, meta = site
    meta_path, manifest_path, report = expected_paths(tmp_path)
    expected_copy = os.path.join(tmp_path, 'expected-metadata.json')
    shutil.copy2(meta_path, expected_copy)
    online = dict(meta)
    online['recordCount'] = 999  # 唔喺 REQUIRED_COMPARE_FIELDS
    online['datasetSnapshotId'] = 'tampered-snapshot'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(online, f, ensure_ascii=False)
    srv, base = serve(str(tmp_path))
    try:
        rc = postdeploy.main(['--expected-metadata', expected_copy, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1, '非 8 欄位改動都要被完整 object 檢查攔截'
        data = json.load(open(report, encoding='utf-8'))
        assert any(c['check'] == 'metadata.full_object_equal' for c in data['failures'])
        assert not any('metadata.version' in c['check'] for c in data['failures'])
        detail = next(c for c in data['failures'] if c['check'] == 'metadata.full_object_equal')
        assert 'recordCount' in detail['detail'] and 'datasetSnapshotId' in detail['detail']
    finally:
        srv.shutdown()


def test_postdeploy_online_schema_invalid_fails(site):
    """線上 metadata 違反完整 Schema（minimum）→ 明確失敗"""
    tmp_path, meta = site
    meta_path, manifest_path, report = expected_paths(tmp_path)
    expected_copy = os.path.join(tmp_path, 'expected-valid.json')
    shutil.copy2(meta_path, expected_copy)
    online = dict(meta)
    online['rawRecordCount'] = -1  # Schema minimum 0
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(online, f, ensure_ascii=False)
    srv, base = serve(str(tmp_path))
    try:
        rc = postdeploy.main(['--expected-metadata', expected_copy, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1
        data = json.load(open(report, encoding='utf-8'))
        assert any(c['check'] == 'metadata.online_schema_valid' for c in data['failures'])
        assert not any(c['check'] == 'metadata.expected_schema_valid' for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_expected_schema_invalid_fails(site):
    """expected metadata 自己唔過 Schema → 即阻斷（不能當有效比對基準）"""
    tmp_path, meta = site
    meta_path, manifest_path, report = expected_paths(tmp_path)
    bad = dict(meta)
    bad['datasetDate'] = '2026-02-30'  # 唔存在嘅日曆日期
    bad_path = os.path.join(tmp_path, 'expected-bad.json')
    with open(bad_path, 'w', encoding='utf-8') as f:
        json.dump(bad, f, ensure_ascii=False)
    srv, base = serve(str(tmp_path))
    try:
        rc = postdeploy.main(['--expected-metadata', bad_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1
        data = json.load(open(report, encoding='utf-8'))
        assert any(c['check'] == 'metadata.expected_schema_valid' for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_payload_hash_mismatch_fails(site):
    tmp_path, meta = site
    with open(os.path.join(tmp_path, 'index.html'), 'a', encoding='utf-8') as f:
        f.write('<!-- tampered -->')
    srv, base = serve(str(tmp_path))
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '2',
                              '--no-browser'])
        assert rc == 1
        data = json.load(open(report, encoding='utf-8'))
        assert any('releasePayloadHash' in c['check'] for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_retries_stale_cdn_then_succeeds(site):
    """CDN 舊快取：頭兩次 metadata 係舊版、第三次正確 → 有界重試後仍要通過"""
    tmp_path, meta = site
    state = {'n': 0}
    real_open = open

    class StaleHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)

        def do_GET(self):
            if 'metadata.json' in self.path:
                state['n'] += 1
                if state['n'] <= 2:
                    stale = dict(meta)
                    stale['version'] = '0.0.1-stale'
                    body = json.dumps(stale).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            super().do_GET()

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), StaleHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '4',
                              '--no-browser'])
        assert rc == 0, '有界重試後攞到正確版本應該通過'
        assert state['n'] >= 3
    finally:
        srv.shutdown()


def test_postdeploy_pdf_not_matching_metadata_fails(site):
    """PDF 只係 %PDF 但唔係由 metadata 重建 → 必須失敗"""
    tmp_path, meta = site
    with open(os.path.join(tmp_path, '空調對比報告.pdf'), 'ab') as f:
        f.write(b'\n% trailing junk')
    srv, base = serve(str(tmp_path))
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1
        data = json.load(open(report, encoding='utf-8'))
        assert any(c['check'] == 'payload.pdf_matches_metadata' for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_expected_invalid_stops_requests(site):
    """expected 唔過 Schema 時，唔可以再發任何 request，但仍要寫失敗報告"""
    tmp_path, meta = site
    bad = dict(meta)
    bad['datasetDate'] = '2026-02-30'
    bad_path = os.path.join(tmp_path, 'expected-bad.json')
    with open(bad_path, 'w', encoding='utf-8') as f:
        json.dump(bad, f, ensure_ascii=False)
    hits = {'n': 0}
    real_open = open

    class CountingHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def do_GET(self):
            hits['n'] += 1
            super().do_GET()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), CountingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        _meta, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', bad_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1
        assert hits['n'] == 0, 'expected 無效時唔應該再發任何 request'
        data = json.load(open(report, encoding='utf-8'))
        assert data['ok'] is False
        assert any(c['check'] == 'metadata.expected_schema_valid' for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_manifest_traversal_stops_requests(site):
    tmp_path, meta = site
    manifest_path = os.path.join(tmp_path, 'deploy_payload.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump({'files': ['../evil.txt', 'index.html']}, f)
    hits = {'n': 0}

    class CountingHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def do_GET(self):
            hits['n'] += 1
            super().do_GET()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), CountingHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        meta_path, _manifest, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--no-browser'])
        assert rc == 1
        assert hits['n'] == 0
        data = json.load(open(report, encoding='utf-8'))
        assert any(c['check'] == 'manifest.paths' for c in data['failures'])
    finally:
        srv.shutdown()


def test_postdeploy_report_write_failure_nonzero(site):
    tmp_path, meta = site
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    srv, base = serve(str(tmp_path))
    try:
        meta_path, manifest_path, _report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', str(blocker / 'r.json'),
                              '--retries', '1', '--no-browser'])
        assert rc == 1, '報告寫入失敗必須非零'
    finally:
        srv.shutdown()


def test_postdeploy_retries_until_full_object_match(site):
    """重試條件係完整 object：改非 8 欄位亦要重試到一致／失敗"""
    tmp_path, meta = site
    state = {'n': 0}

    class FlakyHandler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def do_GET(self):
            if 'metadata.json' in self.path:
                state['n'] += 1
                if state['n'] <= 2:
                    stale = dict(meta)
                    stale['recordCount'] = 999  # 非 8 欄位
                    body = json.dumps(stale).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
            super().do_GET()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(('127.0.0.1', 0), FlakyHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}/'
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '4',
                              '--no-browser'])
        assert rc == 0, open(report, encoding='utf-8').read()
        assert state['n'] >= 3, '非 8 欄位唔一致都要觸發重試'
    finally:
        srv.shutdown()


def test_postdeploy_browser_runtime_check(site):
    """瀏覽器 runtime Version／Last Deploy HKT／Last Update 同 core 路徑核對"""
    pytest.importorskip('playwright.sync_api')
    tmp_path, meta = site
    srv, base = serve(str(tmp_path))
    try:
        meta_path, manifest_path, report = expected_paths(tmp_path)
        rc = postdeploy.main(['--expected-metadata', meta_path, '--manifest', manifest_path,
                              '--base-url', base, '--report', report, '--retries', '1',
                              '--timeout', '60'])
        assert rc == 0, open(report, encoding='utf-8').read()
        data = json.load(open(report, encoding='utf-8'))
        browser_names = {c['check'] for c in data.get('browser', [])}
        assert 'browser.version_from_metadata' in browser_names
        assert 'browser.last_deploy_hkt' in browser_names
        assert 'browser.compare_modal' in browser_names
    finally:
        srv.shutdown()
