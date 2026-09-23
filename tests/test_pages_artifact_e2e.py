# -*- coding: utf-8 -*-
"""端到端：fixture release → Pages envelope → 真 HTTP → metadata／payload hash／
run identity／PDF 重建／瀏覽器 runtime（唔係只 assert 檔名）。"""
import importlib.util
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_SPEC = importlib.util.spec_from_file_location(
    'make_fixture_mod', os.path.join(BASE, 'scripts', 'make_fixture_release.py'))
mfr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mfr)
_BPA_SPEC = importlib.util.spec_from_file_location(
    'bpa_e2e', os.path.join(BASE, 'scripts', 'build_pages_artifact.py'))
bpa = importlib.util.module_from_spec(_BPA_SPEC)
_BPA_SPEC.loader.exec_module(bpa)
_VC_SPEC = importlib.util.spec_from_file_location(
    'verify_candidate_e2e', os.path.join(BASE, 'scripts', 'verify_candidate.py'))
vc = importlib.util.module_from_spec(_VC_SPEC)
_VC_SPEC.loader.exec_module(vc)


def _fixture_and_site(tmp_path):
    fixture = tmp_path / 'release-fixture'
    fixture_report = mfr.build_fixture(BASE, str(fixture),
                                       build='B20260923.E2E-FIXTURE',
                                       commit='f' * 40)
    site = tmp_path / 'site'
    result = bpa.build(str(fixture), str(fixture / 'deploy_payload.json'),
                       str(fixture / 'deploy_envelope.json'),
                       str(fixture / 'metadata.json'), str(site))
    return fixture, fixture_report, site, result


def test_fixture_envelope_http_identity_pdf_and_browser(tmp_path):
    fixture, fixture_report, site, result = _fixture_and_site(tmp_path)
    report_path = tmp_path / 'candidate.json'
    rc = vc.main(['--artifacts-dir', str(site),
                  '--manifest', str(fixture / 'deploy_payload.json'),
                  '--report', str(report_path)])
    assert rc == 0, report_path.read_text(encoding='utf-8')
    report = json.load(open(report_path, encoding='utf-8'))
    checks = {c['check']: c for c in report['checks']}
    checks.update({c.get('check', c.get('name')): c for c in report.get('browser') or []})
    assert checks['metadata.expected_schema_valid']['pass'] is True
    assert checks['payload.releasePayloadHash']['pass'] is True
    assert checks['payload.csv_datasetHash']['pass'] is True
    assert checks['payload.pdf_matches_metadata']['pass'] is True
    assert checks['browser.version_from_metadata']['pass'] is True
    assert checks['browser.no_console_errors']['pass'] is True
    # run identity：site metadata 同 fixture metadata 完全一致（同一封包）
    site_meta = json.loads((site / 'metadata.json').read_text(encoding='utf-8'))
    fixture_meta = json.loads((fixture / 'metadata.json').read_text(encoding='utf-8'))
    assert site_meta == fixture_meta
    assert site_meta['commit'] == fixture_report['commit'] == 'f' * 40
    assert site_meta['build'] == fixture_report['build']
    assert result['releasePayloadHash'] == fixture_report['releasePayloadHash']
    assert result['files'] == fixture_report['files']


def test_tampered_site_fails_http_payload_hash(tmp_path):
    fixture, _fr, site, _result = _fixture_and_site(tmp_path)
    csv = site / 'emsd_空調能源標籤.csv'
    original = csv.read_bytes()
    csv.write_bytes(original.replace(b'M1', b'M9') if b'M1' in original else original + b' ')
    report_path = tmp_path / 'tampered.json'
    rc = vc.main(['--artifacts-dir', str(site),
                  '--manifest', str(fixture / 'deploy_payload.json'),
                  '--no-browser', '--retries', '1',
                  '--report', str(report_path)])
    assert rc == 1, 'tampered payload 必須 fail-closed'
    text = report_path.read_text(encoding='utf-8')
    assert '"ok": false' in text or 'mismatch' in text.lower()


def test_build_rejects_site_whose_metadata_does_not_match_payload(tmp_path):
    fixture, _fr, _site, _result = _fixture_and_site(tmp_path)
    meta_path = fixture / 'metadata.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    meta['releasePayloadHash'] = 'sha256:' + '0' * 64
    meta_path.write_text(json.dumps(meta), encoding='utf-8')
    try:
        bpa.build(str(fixture), str(fixture / 'deploy_payload.json'),
                  str(fixture / 'deploy_envelope.json'), str(meta_path),
                  str(tmp_path / 'should-not-exist'))
        raise AssertionError('metadata mismatch 必須失敗')
    except ValueError:
        pass
    assert not (tmp_path / 'should-not-exist').exists()
