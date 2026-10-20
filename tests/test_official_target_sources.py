# -*- coding: utf-8 -*-
"""Target-aware official routing and MECAN source-evidence adapter."""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import fetch_mecan
import run_official_batch as rob


def test_target_scoped_plan_skips_unrelated_legacy_catalogues():
    public = rob.target_scoped_plan(
        rob.STAGES[1], ['WRK07MIZH1', 'MC07VXH', 'GEH63ZE/GEH63ZC'])
    assert public == {'scripts': ['fetch_mecan.py', 'fetch_gilman.py'],
                      'outputs': ['mecan_official.json', 'gilman_official.json']}
    private = rob.target_scoped_plan(
        rob.STAGES[2], ['MC18VXH', 'GWT07ES', 'CWV-24CRFN9-AD5'])
    assert private == {'scripts': ['fetch_mecan.py', 'fetch_gilman.py'],
                       'outputs': ['mecan_official.json', 'gilman_official.json']}


def test_mecan_adapter_extracts_exact_model_and_writes_safe_evidence(tmp_path, monkeypatch):
    html = '''<html><head><title>MC07VXH - R32</title></head><body>
    型號 MC07VXH
    能源標籤級別 1
    冷凍能力 BTU / h 7,500
    尺寸(闊x高x深) 毫米 450 x 350 x 640
    淨重 公斤 27
    雪種 R32環保雪種
    全系列標配手機WIFI遙控器
    </body></html>'''.encode('utf-8')
    monkeypatch.setattr(fetch_mecan, '_curl_bytes',
                        lambda url: (html, url))
    monkeypatch.setattr(fetch_mecan, 'TARGETS', {'MC07VXH': 'https://mecanhk.com/product/mc07vxh'})
    out = tmp_path / 'mecan_official.json'
    raw = tmp_path / 'raw'
    assert fetch_mecan.main(['--out', str(out), '--raw-dir', str(raw)]) == 0
    data = json.loads(out.read_text())
    item = data['MC07VXH']
    assert item['size'] == '450×350×640'
    assert item['weight'] == '27kg'
    assert item['btu'] == '7,500'
    assert item['energy'] == '1級'
    assert item['gas'] == 'R32'
    evidence = item['sourceEvidence']
    assert evidence['rawSha256'] and len(evidence['rawSha256']) == 64
    assert evidence['url'].startswith('https://mecanhk.com/')
    assert (raw / 'MC07VXH.html').read_bytes() == html
    assert 'MUST-NOT' not in out.read_text()


def test_mecan_adapter_rejects_wrong_page_model(tmp_path, monkeypatch):
    html = b'<html><body>WRONG MODEL no specification</body></html>'
    monkeypatch.setattr(fetch_mecan, '_curl_bytes', lambda url: (html, url))
    monkeypatch.setattr(fetch_mecan, 'TARGETS', {'MC07VXH': 'https://mecanhk.com/wrong'})
    out = tmp_path / 'mecan_official.json'
    assert fetch_mecan.main(['--out', str(out)]) == 1
    assert not out.exists()

def test_legacy_stage_adapters_remain_reachable_under_target_scoped_mode():
    assert rob.target_scoped_plan(rob.STAGES[1], ['CW-SUL70BA']) == {
        'scripts': ['fetch_official.py'], 'outputs': ['official_specs.json']}
    assert rob.target_scoped_plan(rob.STAGES[2], ['CHK07LNE']) == {
        'scripts': ['fetch_carrier.py'], 'outputs': ['carrier_official.json']}
    assert rob.target_scoped_plan(rob.STAGES[2], ['W09R5A']) == {
        'scripts': ['fetch_specs.py'], 'outputs': ['specs.json']}


def test_mixed_legacy_and_adapter_and_unsupported_targets():
    mixed = rob.target_scoped_plan(
        rob.STAGES[1], ['CW-SUL70BA', 'MC07VXH', 'UNKNOWN-TARGET'])
    assert set(mixed['scripts']) == {'fetch_official.py', 'fetch_mecan.py'}
    assert set(mixed['outputs']) == {'official_specs.json', 'mecan_official.json'}
    assert rob.target_scoped_plan(rob.STAGES[1], ['UNKNOWN-TARGET']) == {
        'scripts': [], 'outputs': []}
