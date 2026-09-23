# -*- coding: utf-8 -*-
"""D1-B：coverage pending 公開狀態投影與 UI 待核提示。"""
import importlib.util
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BASE, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub = _load('publish_official_status_mod', 'scripts/publish_official_status.py')


def _receipt(tmp_path, decision, missing=('M9',)):
    r = {
        'schemaVersion': 1, 'stage': 1, 'decision': decision,
        'missingModels': list(missing), 'missingCanonicalModels': [m.upper() for m in missing],
        'startedAt': '2026-09-22T00:00:00Z', 'finishedAt': '2026-09-22T00:01:00Z',
    }
    p = tmp_path / 'receipt.json'
    p.write_text(json.dumps(r), encoding='utf-8')
    return p


def test_publish_pending_status_projection(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'queue-kept-pending-coverage')),
                     '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['pendingCoverage'] is True
    assert st['decision'] == 'queue-kept-pending-coverage'
    assert st['missingModels'] == ['M9']
    assert str(tmp_path) not in json.dumps(st), '公開狀態檔不可有本機絕對路徑'


def test_publish_advanced_status_is_not_pending(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'advanced', ())),
                     '--out', str(out)]) == 0
    st = json.load(open(out, encoding='utf-8'))
    assert st['pendingCoverage'] is False


def test_publish_rejects_unknown_decision(tmp_path):
    out = tmp_path / 'status.json'
    assert pub.main(['--receipt', str(_receipt(tmp_path, 'weird')), '--out', str(out)]) == 1
    assert not out.exists()


def test_generate_html_pending_hint_and_placeholder(tmp_path, monkeypatch):
    gen = _load('generate_html_mod_cov', 'generate_html.py')
    monkeypatch.setattr(gen, 'BASE', str(tmp_path))
    assert gen.official_pending_hint() == ''
    (tmp_path / 'official_batch_status.json').write_text(json.dumps({
        'schemaVersion': 1, 'decision': 'queue-kept-pending-coverage',
        'pendingCoverage': True, 'missingModels': ['M1', 'M2'],
    }), encoding='utf-8')
    hint = gen.official_pending_hint()
    assert '官網規格待核' in hint and 'M1' in hint and 'M2' in hint
    assert '__PENDING_HINT__' in gen.HTML_TEMPLATE


def test_daily_workflow_wires_pending_status_projection():
    text = open(os.path.join(BASE, '.github', 'workflows', 'daily-update.yml'),
                encoding='utf-8').read()
    assert 'scripts/publish_official_status.py' in text
    assert 'official_batch_status.json' in text
