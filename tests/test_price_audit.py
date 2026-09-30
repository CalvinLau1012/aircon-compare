# -*- coding: utf-8 -*-
"""價錢疑點唯讀審計（離線）回歸：分組、determinism、唯讀、路徑 gate、無網絡。"""
import json
import os
import socket
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import audit_price_suspects as audit  # noqa: E402


def _write_repo(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'prices.json').write_text(json.dumps({
        'A': {'price': '$45起', 'pid': '1'},
        'B': {'price': '$2,000起', 'pid': '1'},
        'C': {'price': '$100起'},
        'D': '$300起',
    }, ensure_ascii=False), encoding='utf-8')
    (repo / 'biggo_prices.json').write_text(json.dumps({
        'X': {'price': '$50起', 'merchants': 1,
              'url': 'https://biggo.hk/s/?q=X', 'updated': '2026-09-01'},
        'Y': {'price': '$2,000起', 'merchants': 3,
              'url': 'https://biggo.hk/s/?q=Y', 'updated': '2026-09-01'},
        'Z': {'price': '$80起', 'merchants': 2,
              'url': 'https://biggo.hk/s/?q=Z', 'updated': '2026-09-01',
              'matchedTitle': 'Z 窗口機'},
    }, ensure_ascii=False), encoding='utf-8')
    (repo / 'gemini_prices.json').write_text(json.dumps({
        'G': {'price': '$100起', 'url': 'https://www.google.com/search?q=G'},
    }, ensure_ascii=False), encoding='utf-8')
    return repo


def test_collect_groups_and_reasons(tmp_path):
    report = audit.collect(str(_write_repo(tmp_path)), threshold=500)
    candidates = {(c['source'], c['model']): c for c in report['candidates']}
    assert set(candidates) == {
        ('price_legacy', 'A'), ('price_legacy', 'B'), ('price_legacy', 'C'),
        ('price_legacy', 'D'), ('biggo', 'X'), ('biggo', 'Z'), ('gemini', 'G'),
    }
    assert candidates[('price_legacy', 'A')]['reasons'] == ['low_price', 'shared_pid']
    assert candidates[('price_legacy', 'B')]['reasons'] == ['shared_pid']
    assert candidates[('price_legacy', 'C')]['reasons'] == [
        'low_price', 'missing_identity_evidence']
    assert candidates[('price_legacy', 'D')]['reasons'] == [
        'low_price', 'missing_identity_evidence']
    assert candidates[('biggo', 'X')]['reasons'] == [
        'low_price', 'missing_identity_evidence', 'single_merchant']
    assert candidates[('biggo', 'Z')]['reasons'] == ['low_price']
    assert candidates[('biggo', 'Z')]['matchedTitle'] == 'Z 窗口機'
    assert candidates[('gemini', 'G')]['reasons'] == [
        'low_price', 'missing_identity_evidence']
    assert report['summary']['candidateCount'] == 7
    assert report['summary']['bySource'] == {'biggo': 2, 'gemini': 1, 'price_legacy': 4}
    assert report['mode'] == 'read-only-offline'
    assert any('not a path to restart scraping' in n for n in report['notes'])


def test_collect_is_deterministic(tmp_path):
    repo = _write_repo(tmp_path)
    first = json.dumps(audit.collect(str(repo)), ensure_ascii=False, sort_keys=True)
    second = json.dumps(audit.collect(str(repo)), ensure_ascii=False, sort_keys=True)
    assert first == second


def test_collect_is_read_only(tmp_path):
    repo = _write_repo(tmp_path)
    before = {name: (repo / name).read_bytes()
              for name in ('prices.json', 'biggo_prices.json', 'gemini_prices.json')}
    audit.collect(str(repo))
    for name, data in before.items():
        assert (repo / name).read_bytes() == data, name


def test_collect_makes_no_network_calls(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('audit 唔可以發網絡請求')

    monkeypatch.setattr(socket, 'socket', boom)
    report = audit.collect(str(_write_repo(tmp_path)))
    assert report['summary']['candidateCount'] == 7


def test_threshold_only_changes_low_price_flag(tmp_path):
    repo = _write_repo(tmp_path)
    report = audit.collect(str(repo), threshold=100)
    candidates = {(c['source'], c['model']): c for c in report['candidates']}
    assert 'low_price' not in candidates[('price_legacy', 'D')]['reasons']
    assert 'missing_identity_evidence' in candidates[('price_legacy', 'D')]['reasons']
    assert 'low_price' in candidates[('price_legacy', 'A')]['reasons']


def test_candidate_has_no_invalid_verdict_fields(tmp_path):
    report = audit.collect(str(_write_repo(tmp_path)))
    for candidate in report['candidates']:
        assert set(candidate) == {'model', 'source', 'rawPrice', 'pid', 'url',
                                  'merchants', 'matchedTitle', 'reasons'}
        assert candidate['reasons'], 'candidate 必須有 review reason'
    assert report['kind'] == 'price-suspect-audit'


def test_main_writes_outside_repo(tmp_path, capsys):
    repo = _write_repo(tmp_path)
    out = tmp_path / 'out' / 'audit.json'
    rc = audit.main(['--repo', str(repo), '--out', str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['summary']['candidateCount'] == 7
    assert str(repo) not in out.read_text(encoding='utf-8'), '報告唔可以含絕對 repo 路徑'


def test_main_refuses_out_inside_repo(tmp_path):
    repo = _write_repo(tmp_path)
    out = repo / 'audit.json'
    rc = audit.main(['--repo', str(repo), '--out', str(out)])
    assert rc == 2
    assert not out.exists()
    rc_allow = audit.main(['--repo', str(repo), '--out', str(out),
                           '--allow-repo-path'])
    assert rc_allow == 0 and out.exists()


def test_main_stdout(tmp_path, capsys):
    repo = _write_repo(tmp_path)
    rc = audit.main(['--repo', str(repo)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['summary']['candidateCount'] == 7


def test_missing_files_yield_empty_report(tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    report = audit.collect(str(empty))
    assert report['candidates'] == []
    assert report['sources']['biggo']['present'] is False
    assert audit.main(['--repo', str(empty)]) == 0
