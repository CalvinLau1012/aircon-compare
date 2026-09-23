# -*- coding: utf-8 -*-
"""PDF／HTML 生成事實核查（第二輪）

- invalid／missing metadata 唔可以生成 production PDF（唔可以靜默回 {}）
- core metadata（無 payloadHash）可以用 placeholder 驗核事實後生成
- 同輸入重建 PDF／HTML 必須 byte-for-byte 一致（生成物可重現）
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import generate_html  # noqa: E402
import generate_pdf  # noqa: E402


def _h(p):
    with open(p, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def _valid_core():
    return {
        'schemaVersion': '1.0.0', 'version': '1.2.9', 'build': 'B20260922.TEST',
        'commit': 'a' * 40, 'deployTime': '2026-09-22T03:00:00Z',
        'workflowRunId': '42', 'deploymentType': 'release',
        'datasetDate': '2026-09-22', 'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-09-21T18:59:57Z',
        'datasetSourceUrl': 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php',
        'datasetSnapshotId': 'emsd-2026-09-22-abc',
        'datasetHash': 'sha256:' + 'c' * 64, 'recordCount': 1,
    }


def test_invalid_metadata_cannot_build_pdf(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('{"datasetDate": "2026-02-30"}', encoding='utf-8')
    out = tmp_path / 'x.pdf'
    with pytest.raises(ValueError):
        generate_pdf.build_pdf(str(out), metadata_path=str(bad))
    assert not out.exists(), 'invalid metadata 唔應該留低 PDF'


def test_missing_metadata_cannot_build_pdf(tmp_path):
    out = tmp_path / 'x.pdf'
    with pytest.raises(ValueError):
        generate_pdf.build_pdf(str(out), metadata_path=str(tmp_path / 'nope.json'))
    assert not out.exists()


def test_empty_metadata_cannot_build_pdf(tmp_path):
    empty = tmp_path / 'empty.json'
    empty.write_text('{}', encoding='utf-8')
    out = tmp_path / 'x.pdf'
    with pytest.raises(ValueError):
        generate_pdf.build_pdf(str(out), metadata_path=str(empty))
    assert not out.exists()


def test_core_metadata_builds_and_is_reproducible(tmp_path):
    core = tmp_path / 'core.json'
    core.write_text(json.dumps(_valid_core(), ensure_ascii=False), encoding='utf-8')
    a, b = tmp_path / 'a.pdf', tmp_path / 'b.pdf'
    generate_pdf.build_pdf(str(a), metadata_path=str(core))
    generate_pdf.build_pdf(str(b), metadata_path=str(core))
    assert a.read_bytes() == b.read_bytes(), '同輸入 PDF 必須 byte-for-byte 一致'


def test_pdf_cli_invalid_metadata_readable_error(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text('{oops', encoding='utf-8')
    r = subprocess.run([sys.executable, os.path.join(BASE, 'generate_pdf.py'),
                        '--metadata', str(bad), '--out', str(tmp_path / 'x.pdf')],
                       cwd=BASE, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    assert r.returncode != 0
    assert 'Traceback' not in r.stderr
    assert not (tmp_path / 'x.pdf').exists()


def test_html_build_is_reproducible(monkeypatch):
    outputs = []
    monkeypatch.setattr(generate_html, 'write_html_output',
                        lambda path, html: outputs.append(html))
    generate_html.build_html()
    generate_html.build_html()
    assert outputs[0] == outputs[1], '同輸入 HTML 必須一致（可重現建置）'
    assert '__TOTAL_MODELS__' not in outputs[0]
