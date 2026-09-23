# -*- coding: utf-8 -*-
"""D7-A：原始 EMSD response bytes、公開 hash receipt、私人 sink、90 日 retention。"""
import hashlib
import importlib.util
import json
import os
import sys
import datetime as dt

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
_SPEC = importlib.util.spec_from_file_location('fetch_emsd_raw_mod', os.path.join(BASE, 'fetch_emsd.py'))
fetch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch)


HEADER = ['品牌', '型號'] + [f'c{i}' for i in range(13)]


def _html(rows, header=True):
    trs = []
    if header:
        trs.append('<tr>' + ''.join(f'<th>{c}</th>' for c in HEADER) + '</tr>')
    for r in rows:
        trs.append('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>')
    return '<table>' + ''.join(trs) + '</table>'


def _rows(n, start='M'):
    return [[f'品牌{i}', f'{start}{i}'] + [str(i)] * 13 for i in range(n)]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    monkeypatch.setattr(fetch, 'RAW_RECEIPT_PATH', str(tmp_path / 'emsd_raw_receipt.json'))
    monkeypatch.setattr(fetch, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch, 'MIN_EMSD_ROWS', 100)
    monkeypatch.setattr(fetch.random, 'uniform', lambda a, b: 0)
    monkeypatch.delenv('AIRCON_EMSD_REQUIRE_RAW_SINK', raising=False)
    monkeypatch.delenv('AIRCON_EMSD_RAW_SINK_DIR', raising=False)
    return tmp_path


def _run_main(pages):
    calls = {'i': 0}

    def fake_fetch(p):
        calls['i'] += 1
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch
    try:
        fetch.main()
        return 0
    except SystemExit as e:
        return e.code


class FakeHeaders:
    def __init__(self, **kw):
        self._kw = kw

    def get(self, name, default=None):
        return self._kw.get(name, default)


def test_raw_page_record_exact_bytes_and_hash():
    raw = b'<html>exact&#39;</html>'
    rec = fetch.raw_page_record(2, raw, {'lastModified': 'Tue, 22 Sep 2026 00:00:00 GMT',
                                         'etag': '"abc"'})
    assert rec['page'] == 2 and rec['byteLength'] == len(raw)
    assert rec['sha256'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
    assert rec['lastModified'].startswith('Tue')
    assert rec['etag'] == '"abc"'
    assert rec['_raw'] == raw


def test_build_raw_receipt_rejects_missing_duplicate_and_zero():
    recs = [fetch.raw_page_record(1, b'a'), fetch.raw_page_record(2, b'b')]
    receipt = fetch.build_raw_receipt(recs, dataset_hash='sha256:' + '0' * 64,
                                      retrieved_at='2026-09-22T00:00:00Z',
                                      source_url='https://example.invalid/', total_rows=2,
                                      per_page_rows=[1, 1])
    assert receipt['pageCount'] == 2 and receipt['archiveHash'].startswith('sha256:')
    assert 'sha256:' + '0' * 64 == receipt['datasetHash']
    assert all('_raw' not in p for p in receipt['pages'])
    with pytest.raises(ValueError):
        fetch.build_raw_receipt([], 'sha256:x', '2026-09-22T00:00:00Z', 'u', 0, [])
    with pytest.raises(ValueError):
        fetch.build_raw_receipt([recs[0], recs[0]], 'sha256:x',
                                '2026-09-22T00:00:00Z', 'u', 2, [1, 1])
    with pytest.raises(ValueError):
        fetch.build_raw_receipt([recs[1]], 'sha256:x', '2026-09-22T00:00:00Z', 'u', 1, [1])


def test_persist_raw_archive_and_retention_boundary(tmp_path):
    sink = tmp_path / 'private-sink'
    sink.mkdir()
    recs = [fetch.raw_page_record(1, b'a'), fetch.raw_page_record(2, b'bb')]
    out = fetch.persist_raw_archive(recs, sink_dir=str(sink))
    assert out['persisted'] is True and out['archiveHash'].startswith('sha256:')
    final = sink / out['objectId']
    assert (final / 'p01.html').read_bytes() == b'a'
    assert (final / 'p02.html').read_bytes() == b'bb'
    manifest = json.loads((final / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['archiveHash'] == out['archiveHash']
    # 91 日會被刪；90 日邊界保留。
    now = dt.datetime(2026, 9, 22, 12, 0, 0, tzinfo=dt.timezone.utc)
    old91 = sink / 'run-20260623T120000Z'
    old90 = sink / 'run-20260624T120000Z'
    for d, created in ((old91, '2026-06-23T12:00:00Z'), (old90, '2026-06-24T12:00:00Z')):
        d.mkdir()
        (d / 'manifest.json').write_text(json.dumps({'createdAt': created}), encoding='utf-8')
    removed = fetch.cleanup_expired_raw_sink(str(sink), now=now)
    assert 'run-20260623T120000Z' in removed
    assert 'run-20260624T120000Z' not in removed and old90.exists(), '90 日邊界唔應該提早刪'


def test_success_writes_public_raw_receipt_and_private_sink(env, monkeypatch):
    sink = env.parent / ('sink-' + env.name)
    monkeypatch.setenv('AIRCON_EMSD_RAW_SINK_DIR', str(sink))
    monkeypatch.setenv('AIRCON_EMSD_REQUIRE_RAW_SINK', '1')
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 0
    raw_receipt = json.loads((env / 'emsd_raw_receipt.json').read_text(encoding='utf-8'))
    success = json.loads((env / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert raw_receipt['success'] is True and raw_receipt['pageCount'] == 2
    assert raw_receipt['privateArchive']['persisted'] is True
    assert raw_receipt['archiveHash'] == raw_receipt['privateArchive']['archiveHash']
    assert success['rawReceiptHash'] == 'sha256:' + hashlib.sha256(
        (env / 'emsd_raw_receipt.json').read_bytes()).hexdigest()
    # raw bytes 唔喺公開 repo 工作樹（BASE_DIR）出現；只喺 private sink。
    public_bytes = b''.join(
        p.read_bytes() for p in env.iterdir() if p.is_file())
    assert b'<table>' not in public_bytes, '公開工作樹不可有 raw HTML'
    assert list(sink.glob('run-*/p01.html')), 'private sink 要有 raw page'


def test_partial_fetch_writes_no_success_raw_receipt(env, monkeypatch):
    monkeypatch.setenv('AIRCON_EMSD_RAW_SINK_DIR', str(env.parent / ('sink-' + env.name)))
    monkeypatch.setenv('AIRCON_EMSD_REQUIRE_RAW_SINK', '1')
    calls = {'n': 0}

    def fake_fetch(p):
        calls['n'] += 1
        if p == 1:
            return _html(_rows(50, 'A'), header=True)
        raise RuntimeError('page 2 down')

    fetch.fetch_page = fake_fetch
    with pytest.raises(SystemExit) as e:
        fetch.main()
    assert e.value.code == 1
    assert not (env / 'emsd_raw_receipt.json').exists()


def test_private_sink_required_missing_blocks_success_receipt(env, monkeypatch):
    monkeypatch.setenv('AIRCON_EMSD_REQUIRE_RAW_SINK', '1')
    pages = [_html(_rows(50, 'A'), header=True), _html(_rows(50, 'B')), '']
    assert _run_main(pages) == 1
    assert not (env / 'emsd_receipt.json').exists(), 'raw sink 失敗唔可以有成功 CSV 收據'
