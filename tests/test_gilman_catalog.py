# -*- coding: utf-8 -*-
"""Gilman Nuxt pagination, cache time and failure-preservation contracts."""
import hashlib
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BASE), str(BASE / 'scripts')]
import fetch_gilman as g


def _encode(obj):
    values = [None]

    def enc(value):
        index = len(values)
        values.append(None)
        if isinstance(value, dict):
            values[index] = {k: enc(v) for k, v in value.items()}
        elif isinstance(value, list):
            values[index] = [enc(v) for v in value]
        else:
            values[index] = value
        return index

    root = enc(obj)
    values[0] = ['Reactive', root]
    return json.dumps(values, ensure_ascii=False)


def _html(page, page_count, total, models=('GWT07ES',)):
    items = []
    for i, model in enumerate(models):
        items.append({'id': page * 100 + i, 'attributes': {
            'model_number': model, 'display_model_number': model,
            'name_en': 'GREE ' + model, 'name_tc': '格力 ' + model,
            'slug': model.lower()}})
    payload = {'data': {'products': {'data': items,
                                     'meta': {'pagination': {
                                         'page': page, 'pageSize': len(models) or 1,
                                         'pageCount': page_count, 'total': total}}}}}
    return '<html><script id="__NUXT_DATA__" type="application/json">' + _encode(payload) + '</script></html>'


def _manifest(tmp_path, pages):
    rows = []
    for page, html, fetched in pages:
        tmp_path.mkdir(parents=True, exist_ok=True)
        p = tmp_path / f'page-{page}.html'
        p.write_text(html, encoding='utf-8')
        rows.append({'url': g.BRANDS['GREE']['url'] if page == 1 else g.BRANDS['GREE']['url'] + f'?page={page}',
                     'path': str(p),
                     'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                     'fetchedAt': fetched})
    p = tmp_path / 'manifest.json'
    p.write_text(json.dumps({'pages': rows}), encoding='utf-8')
    return p


def test_nuxt_pagination_is_used_and_cache_time_is_not_mtime(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'BRANDS', {'GREE': g.BRANDS['GREE']})
    manifest = _manifest(tmp_path, [
        (1, _html(1, 2, 2, ('GWT07ES', 'OTHER')), '2026-10-20T09:00:00Z'),
        (2, _html(2, 2, 2, ('OTHER2',)), None),
    ])
    # File mtime is deliberately changed; it must not become fetchedAt.
    os.utime(tmp_path / 'page-2.html', (999999999, 999999999))
    catalog, targets, attempted, failures, _ = g.scan(cache_manifest=str(manifest))
    assert failures == [] and attempted == 2
    assert catalog['GREE']['pageCount'] == 2 and catalog['GREE']['total'] == 2
    assert targets == {'GWT07ES'}
    assert catalog['GREE']['pages'][1]['fetchedAt'] is None


def test_wrong_page_and_changed_page_count_are_integrity_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'BRANDS', {'GREE': g.BRANDS['GREE']})
    m1 = _manifest(tmp_path / 'wrong', [
        (1, _html(1, 2, 2), None),
        (2, _html(1, 2, 2), None),
    ])
    _, _, _, failures, reasons = g.scan(cache_manifest=str(m1))
    assert failures and reasons['GREE:page2'] == 'integrity-error'

    m2 = _manifest(tmp_path / 'changed', [
        (1, _html(1, 2, 2), None),
        (2, _html(2, 3, 3), None),
    ])
    _, _, _, failures, reasons = g.scan(cache_manifest=str(m2))
    assert failures and reasons['GREE:page2'] == 'integrity-error'


def test_missing_page_keeps_existing_output(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'BRANDS', {'GREE': g.BRANDS['GREE']})
    manifest = _manifest(tmp_path, [(1, _html(1, 2, 2), None)])
    out = tmp_path / 'gilman_official.json'
    out.write_text('{"_catalog":{"sentinel":true}}', encoding='utf-8')
    old = out.read_bytes()
    assert g.main(['--cache-manifest', str(manifest), '--out', str(out)]) != 0
    assert out.read_bytes() == old


def test_explicit_cache_manifest_never_falls_back_to_live_http(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'BRANDS', {'GREE': g.BRANDS['GREE']})
    manifest = _manifest(tmp_path, [(1, _html(1, 2, 2), None)])
    requests = []
    def unexpected_fetch(url):
        requests.append(url)
        raise RuntimeError('network must remain unused in explicit cache replay')
    monkeypatch.setattr(g, '_curl', unexpected_fetch)
    _, _, _, failures, _ = g.scan(cache_manifest=str(manifest))
    assert failures
    assert requests == [], 'a missing cache page must fail without live fallback'
