# -*- coding: utf-8 -*-
"""EMSD 雙來源（B）聚焦測試：正規化／一致性／缺失／fail-closed／namespace。

無外網：HTTP 同 transport 全部用 fake／fixture。
"""
import csv
import hashlib
import importlib.util
import io
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import emsd_dual_source as dual  # noqa: E402
import private_raw_sink as prs  # noqa: E402

_SPEC = importlib.util.spec_from_file_location('fetch_emsd_dual_mod', os.path.join(BASE, 'fetch_emsd.py'))
fetch = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch)

HEADER = list(dual.CANONICAL_HEADER)
SUPPLIED_HEADER = 'Product being Supplied by Information Provider'


def _rows(csv_rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(HEADER + [SUPPLIED_HEADER])
    for row in csv_rows:
        w.writerow(list(row) + ['Yes'])
    return buf.getvalue().encode('utf-8-sig')


def _row(reg, brand='開利', model='CHK12BE', **overrides):
    values = dict(zip(dual.CANONICAL_FIELDS,
                      [brand, model, reg, '2020', '1', '525', '4.97', '4.8154', 'R32',
                       '1', '49', '4.84', '4.2779', '供應商', '是']))
    values.update(overrides)
    return [values[f] for f in dual.CANONICAL_FIELDS]


def _paginated(row):
    """paginated rows 用 canonical order 直接餵入。"""
    return [list(row)]


def _resp(body, status=200, headers=None):
    return dual.RawResponse(url=dual.CSV_URL, status=status, body=body,
                            headers=headers or {}, fetchedAt='2026-09-28T00:00:00Z')


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch, 'RECEIPT_PATH', str(tmp_path / 'emsd_receipt.json'))
    monkeypatch.setattr(fetch, 'RAW_RECEIPT_PATH', str(tmp_path / 'emsd_raw_receipt.json'))
    monkeypatch.setattr(fetch, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch, 'MIN_EMSD_ROWS', 1)
    monkeypatch.setattr(fetch.random, 'uniform', lambda a, b: 0)
    monkeypatch.delenv('AIRCON_EMSD_REQUIRE_RAW_SINK', raising=False)
    monkeypatch.delenv('AIRCON_EMSD_RAW_SINK_DIR', raising=False)
    return tmp_path


# ---------------------------------------------------------------- normalization


def test_parse_csv_alias_headers_and_canonical_order():
    """英文／亂序 header 仍映射到 canonical 15 欄。"""
    custom = ['Ref No.', 'Brand', 'Inverter', 'Model', 'Year',
              'Energy Efficiency Grade (Cooling) (1 to 5)',
              'Annual Energy Consumption (Cooling) (kWh)',
              'Cooling Capacity (kW)', 'CSPF', 'Refrigerant',
              'Energy Efficiency Grade (Heating) (1 to 5)',
              'Annual Energy Consumption (Heating) (kWh)',
              'Heating Capacity (kW)', 'HSPF', 'Data Provider', SUPPLIED_HEADER]
    values = dict(zip(dual.CANONICAL_FIELDS, _row('REG-1')))
    field_of = {
        'Ref No.': 'registrationNo', 'Brand': 'brand', 'Inverter': 'inverter',
        'Model': 'model', 'Year': 'year',
        'Energy Efficiency Grade (Cooling) (1 to 5)': 'coolingGrade',
        'Annual Energy Consumption (Cooling) (kWh)': 'coolingAnnualKwh',
        'Cooling Capacity (kW)': 'coolingCapacityKw', 'CSPF': 'cspf',
        'Refrigerant': 'refrigerant',
        'Energy Efficiency Grade (Heating) (1 to 5)': 'heatingGrade',
        'Annual Energy Consumption (Heating) (kWh)': 'heatingAnnualKwh',
        'Heating Capacity (kW)': 'heatingCapacityKw', 'HSPF': 'hspf',
        'Data Provider': 'provider',
    }
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(custom)
    w.writerow(['Yes' if c == SUPPLIED_HEADER else values[field_of[c]] for c in custom])
    parsed = dual.parse_open_data_csv(buf.getvalue().encode('utf-8-sig'))
    assert parsed['rowCount'] == 1
    assert list(parsed['rows'][0]) == list(_row('REG-1'))


# ------------------------------------------------- live DATA.GOV.HK CSV contract

LIVE_HEADER = [
    'Information Provider English', 'Information Provider Traditional Chinese',
    'Information Provider Simplified Chinese', 'Reference Number', 'Year',
    'Brand English', 'Brand Traditional Chinese', 'Brand Simplified Chinese',
    'Model', 'Category', 'Refrigerant',
    'Energy Efficiency Grade Cooling (1 to 5)',
    'Annual Energy Consumption Cooling (kWh)',
    'Rated Power Consumption Cooling (kW)', 'Rated Cooling Capacity (kW)',
    'Cooling Capacity (kW)', 'Cooling Seasonal Performance Factor (CSPF)',
    'Inverter', 'Energy Efficiency Grade Heating (1 to 5)',
    'Annual Energy Consumption Heating (kWh)',
    'Rated Power Consumption Heating (kW)', 'Rated Heating Capacity (kW)',
    'Heating Capacity (kW)', 'Heating Seasonal Performance Factor (HSPF)',
    'Place of Manufacture English', 'Place of Manufacture Traditional Chinese',
    'Place of Manufacture Simplified Chinese', SUPPLIED_HEADER,
    'Supply Information Last Updated Date',
]


def _live_row(reg, *, supplied='Yes', inverter='Y', heating=False):
    return ['Provider Ltd.', '供應商有限公司', '供应商有限公司', reg, '2020',
            'General', '珍寶', '珍宝', 'LIVE-MODEL', '4', 'R410A',
            '1', '525', '1.3', '4.9', '4.97', '4.8154', inverter,
            '1' if heating else '', '49' if heating else '',
            '1.4' if heating else '', '4.8' if heating else '',
            '4.84' if heating else '', '4.2779' if heating else '',
            'China', '中國', '中国', supplied, '2026-09-16']


def _live_bytes(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(LIVE_HEADER)
    w.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def test_live_open_data_csv_supplied_scope_and_canonical_values():
    """真實 29 欄英文 header：只取 Supplied=Yes，值轉受治理生產表示。"""
    parsed = dual.parse_open_data_csv(_live_bytes([
        _live_row('REG-LIVE-1', supplied='Yes', inverter='Y'),
        _live_row('REG-LIVE-2', supplied='No', inverter='N'),
        _live_row('REG-LIVE-3', supplied='No Information', inverter='N'),
    ]))
    assert parsed['rowCount'] == 1
    assert parsed['excludedRowCount'] == 2
    row = dict(zip(dual.CANONICAL_FIELDS, parsed['rows'][0]))
    assert row['brand'] == '珍寶', '繁中 brand 欄先係受治理比對值'
    assert row['provider'] == '供應商有限公司'
    assert row['model'] == 'LIVE-MODEL'
    assert row['cspf'] == '4.8154'
    assert row['inverter'] == '是'
    assert row['heatingGrade'] == '不適用'
    assert row['heatingAnnualKwh'] == '—'
    assert row['heatingCapacityKw'] == '—'
    assert row['hspf'] == '—'


def test_live_open_data_csv_heating_numeric_and_inverter_false_kept():
    parsed = dual.parse_open_data_csv(_live_bytes([
        _live_row('REG-LIVE-4', inverter='N', heating=True),
    ]))
    row = dict(zip(dual.CANONICAL_FIELDS, parsed['rows'][0]))
    assert row['inverter'] == '否'
    assert row['heatingGrade'] == '1'
    assert row['heatingAnnualKwh'] == '49'
    assert row['heatingCapacityKw'] == '4.84'
    assert row['hspf'] == '4.2779'


def test_live_open_data_csv_unknown_supplied_or_inverter_token_fails_closed():
    with pytest.raises(dual.SourceSchemaError):
        dual.parse_open_data_csv(_live_bytes([_live_row('REG-X', supplied='Maybe')]))
    with pytest.raises(dual.SourceSchemaError):
        dual.parse_open_data_csv(_live_bytes([_live_row('REG-X', inverter='X')]))


def test_csv_without_supplied_column_fails_closed():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(dual.CANONICAL_HEADER)
    w.writerow(_row('REG-Y'))
    with pytest.raises(dual.SourceSchemaError):
        dual.parse_open_data_csv(buf.getvalue().encode('utf-8-sig'))


def test_numeric_and_missing_marker_normalization_is_deterministic():
    assert dual.normalize_value('coolingCapacityKw', '6.0') == dual.normalize_value(
        'coolingCapacityKw', '6.00')
    assert dual.normalize_value('cspf', '4.8154') == dual.normalize_value('cspf', '4.81540')
    assert dual.normalize_value('refrigerant', '不適用') == ''
    assert dual.normalize_value('provider', '-') == ''
    assert dual.normalize_row(_row('R', refrigerant='不適用')) == dual.normalize_row(
        _row('R', refrigerant=''))


def test_compare_equal_ignores_numeric_formatting_and_whitespace():
    csv_rows = [_row('REG-1', coolingCapacityKw='4.970', provider=' 供應商 ')]
    pag_rows = [_row('REG-1', coolingCapacityKw='4.97', provider='供應商')]
    result = dual.compare_sources(csv_rows, pag_rows)
    assert result['equal'] is True
    assert result['counts']['csv']['registrationCount'] == 1


def test_compare_detects_governed_field_mismatch():
    csv_rows = [_row('REG-1', cspf='4.8154')]
    pag_rows = [_row('REG-1', cspf='4.0000')]
    result = dual.compare_sources(csv_rows, pag_rows)
    assert result['equal'] is False
    assert result['fieldMismatchCounts'] == {'cspf': 1}
    assert result['mismatchedKeys']['samples'] == ['REG-1']


def test_compare_detects_missing_keys_both_directions():
    csv_rows = [_row('REG-A'), _row('REG-B')]
    pag_rows = [_row('REG-B'), _row('REG-C')]
    result = dual.compare_sources(csv_rows, pag_rows)
    assert result['equal'] is False
    assert result['missingInCsv']['samples'] == ['REG-C']
    assert result['missingInPaginated']['samples'] == ['REG-A']


def test_compare_detects_duplicate_multiset_mismatch():
    csv_rows = [_row('REG-1'), _row('REG-1')]
    pag_rows = [_row('REG-1')]
    result = dual.compare_sources(csv_rows, pag_rows)
    assert result['equal'] is False
    assert result['counts']['csv']['registrationCount'] == 2
    assert result['counts']['paginated']['registrationCount'] == 1


# ---------------------------------------------------------------- source completeness


def test_verify_rejects_empty_or_incomplete_csv():
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=_paginated(_row('R')),
                                paginated_raw_records=[{'page': 1}],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=1,
                                raw_response=_resp(b''))
    assert exc.value.kind == 'csv_incomplete'


def test_verify_rejects_csv_below_min_rows():
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=_paginated(_row('R')),
                                paginated_raw_records=[{'page': 1}],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=5,
                                raw_response=_resp(_rows([_row('R')])))
    assert exc.value.kind == 'csv_incomplete'


def test_verify_rejects_missing_paginated_source_or_pages():
    payload = _rows([_row('R')])
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=[], paginated_raw_records=[],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=1,
                                raw_response=_resp(payload))
    assert exc.value.kind == 'paginated_incomplete'
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=[list(_row('R'))], paginated_raw_records=[],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=1,
                                raw_response=_resp(payload))
    assert exc.value.kind == 'paginated_incomplete'


def test_verify_rejects_unmappable_csv_schema():
    body = _rows([_row('R')]).replace('參考編號'.encode('utf-8'), 'UNKNOWN'.encode('utf-8'))
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=_paginated(_row('R')),
                                paginated_raw_records=[{'page': 1}],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=1,
                                raw_response=_resp(body))
    assert exc.value.kind == 'schema'


def test_verify_mismatch_raises_with_sanitized_diff():
    csv_rows = [_row('REG-1', cspf='9.9999')]
    pag_rows = [_row('REG-1', cspf='4.8154')]
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=pag_rows,
                                paginated_raw_records=[{'page': 1}],
                                retrieved_at='2026-09-28T00:00:00Z', min_rows=1,
                                raw_response=_resp(_rows(csv_rows)))
    assert exc.value.kind == 'mismatch'
    diff = exc.value.diff
    assert diff['comparison']['fieldMismatchCounts'] == {'cspf': 1}
    assert '<' not in json.dumps(diff), 'diff 唔可以有 raw HTML／bytes'


# ---------------------------------------------------------------- HTTP transport


class _FakeTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, *, headers=None, timeout=None, now=None, validator=None):
        self.calls.append({'url': url, 'headers': dict(headers or {}), 'timeout': timeout,
                           'validator': validator})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_fetch_csv_retries_transient_then_succeeds():
    transport = _FakeTransport([
        dual.DualSourceError('網絡錯誤', kind='network_error'),
        _resp(_rows([_row('R')])),
    ])
    resp = dual.fetch_csv_raw(transport=transport, max_attempts=3)
    assert resp.status == 200 and len(transport.calls) == 2
    assert transport.calls[0]['headers']['User-Agent'].startswith('aircon-compare')


def test_fetch_csv_403_or_429_no_immediate_retry():
    transport = _FakeTransport([
        dual.DualSourceError('HTTP 429', kind='http_error', diff={'status': 429}),
        _resp(_rows([_row('R')])),
    ])
    with pytest.raises(dual.DualSourceError):
        dual.fetch_csv_raw(transport=transport, max_attempts=3)
    assert len(transport.calls) == 1, '403／429 唔可以即刻 retry'


def test_fetch_csv_conditional_304_is_not_modified():
    transport = _FakeTransport([_resp(b'', status=304)])
    resp = dual.fetch_csv_raw(transport=transport, conditional={'etag': '"abc"'},
                              max_attempts=1)
    assert resp.notModified is True
    assert transport.calls[0]['headers']['If-None-Match'] == '"abc"'


# ---------------------------------------------------------------- fetch_emsd integration


def _fake_pages():
    def page(rows, header=False):
        head = ('<tr>' + ''.join(f'<th>{c}</th>' for c in HEADER) + '</tr>') if header else ''
        body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
        return f'<table>{head}{body}</table>'

    rows = [_row('REG-%d' % i, model='CHK%02dBE' % i) for i in range(3)]
    return [page(rows, True)], rows


def _wire(monkeypatch, pages, csv_body, resolution=None):
    def fake_fetch(p):
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch
    fetch.fetch_csv_source = lambda **kw: _resp(csv_body)
    # 離線：CKAN resolver 一律注入固定 catalog resolution（唔打真網絡）
    fetch.resolve_csv_source = lambda **kw: (resolution or {
        'schemaVersion': 1, 'mode': 'catalog',
        'datasetId': dual.CATALOG_DATASET_ID,
        'resourceId': 'test-resource-1', 'resourceName': 'Room Air Conditioners',
        'catalogApiUrl': dual.CATALOG_API_URL, 'datasetPageUrl': dual.CATALOG_DATASET_PAGE,
        'resolvedCsvUrl': dual.CSV_URL, 'resolvedAt': '2026-09-28T00:00:00Z'})


def test_integration_mismatch_fail_closed_preserves_csv_and_writes_diff(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    old = env / 'emsd_空調能源標籤.csv'
    old.write_bytes(b'OLD-CSV')
    pages, rows = _fake_pages()
    _wire(monkeypatch, pages, _rows([_row('REG-0', model='CHK00BE', cspf='9.9'), *rows[1:]]))
    report_path = tmp_path / 'diff.json'
    monkeypatch.setenv('AIRCON_EMSD_DIFF_REPORT', str(report_path))
    with pytest.raises(SystemExit) as exc:
        fetch.main()
    assert exc.value.code == 1
    assert old.read_bytes() == b'OLD-CSV', 'mismatch 唔可以覆寫生產 CSV'
    receipt = json.loads((env / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert receipt['success'] is False and receipt['dualSource']['equal'] is False
    assert receipt['dualSource']['errorKind'] == 'mismatch'
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['comparison']['fieldMismatchCounts'] == {'cspf': 1}
    assert b'<' not in report_path.read_bytes()


def test_integration_success_marks_dual_source_and_uses_csv_primary(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    pages, rows = _fake_pages()
    _wire(monkeypatch, pages, _rows(rows))
    assert fetch.main() is None
    receipt = json.loads((env / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert receipt['success'] is True
    assert receipt['sourceKind'] == dual.SOURCE_KIND_CSV
    assert receipt['sourceUrl'] == dual.CSV_URL
    assert receipt['dualSource']['equal'] is True
    assert set(receipt['dualSource']['sources']) == {
        dual.SOURCE_KIND_CSV, dual.SOURCE_KIND_PAGINATED}
    # CSV primary：寫出嘅 header 係 canonical 15 欄
    written = (env / 'emsd_空調能源標籤.csv').read_text(encoding='utf-8-sig')
    assert written.splitlines()[0].split(',')[1] == '型號'


def test_integration_raw_namespaces_distinct_for_both_sources(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    sink = tmp_path.parent / (tmp_path.name + '-raw-sink')
    monkeypatch.setenv('AIRCON_EMSD_RAW_SINK_DIR', str(sink))
    monkeypatch.setenv('AIRCON_EMSD_REQUIRE_RAW_SINK', '1')
    pages, rows = _fake_pages()
    _wire(monkeypatch, pages, _rows(rows))
    assert fetch.main() is None
    raw_receipt = json.loads((env / 'emsd_raw_receipt.json').read_text(encoding='utf-8'))
    kinds = {entry['sourceKind'] for entry in raw_receipt['sources']}
    assert kinds == {dual.SOURCE_KIND_CSV, dual.SOURCE_KIND_PAGINATED}
    dirs = sorted(p.name for p in sink.iterdir())
    assert any(dual.SOURCE_KIND_CSV in d for d in dirs)
    assert any(dual.SOURCE_KIND_PAGINATED in d for d in dirs)
    for d in dirs:
        manifest = json.loads((sink / d / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['sourceKind'] in kinds
    # 兩個 namespace 內容唔同（CSV bytes vs HTML pages）
    csv_files = list(sink.glob(f'*{dual.SOURCE_KIND_CSV}*/p01.html'))
    pag_files = list(sink.glob(f'*{dual.SOURCE_KIND_PAGINATED}*/p01.html'))
    assert csv_files and pag_files
    assert csv_files[0].read_bytes() != pag_files[0].read_bytes()


def test_raw_sink_source_kind_namespace_rejects_invalid():
    records = [fetch.raw_page_record(1, b'x')]
    with pytest.raises(prs.SinkError):
        prs.persist_local(records, '/tmp/never-used-aircon-sink', source_kind='BAD KIND')


# ---------------------------------------------------------------- CKAN resolver（2026-09-29）

_ALT_CSV = 'https://www.emsd.gov.hk/energylabel/files/meels_rac_2027.csv'
_ROOM_RESOURCE = {
    'id': '2944ffac-4bb3-4240-a5f8-d902d0531b20',
    'name': 'Room Air Conditioners',
    'description': 'Room Air Conditioners',
    'format': 'CSV',
    'url': dual.CSV_URL,
    'state': 'active',
    'created': '2025-12-30T16:33:52.852724',
}


def _catalog_body(resources, dataset_name=None, success=True, provider='hk-emsd',
                  include_provider=True):
    result = {'name': dataset_name or dual.CATALOG_DATASET_ID,
              'resources': resources}
    if include_provider:
        result['organization'] = {'id': '62c2c828-93f0-4182-828a-72a8850c1491',
                                  'name': provider,
                                  'title': 'Electrical and Mechanical Services Department'}
    return json.dumps({
        'help': 'https://data.gov.hk/en-data/api/3/action/help_show?name=package_show',
        'success': success,
        'result': result,
    }, ensure_ascii=False).encode('utf-8')


def _catalog_resp(body=None, status=200, headers=None, final_url=None):
    return dual.RawResponse(url=dual.CATALOG_API_URL, status=status,
                            body=body if body is not None else _catalog_body([_ROOM_RESOURCE]),
                            headers=headers or {'Content-Type': 'application/json'},
                            fetchedAt='2026-09-29T00:00:00Z',
                            finalUrl=final_url or dual.CATALOG_API_URL)


def test_catalog_resolver_selects_single_active_room_csv_without_uuid():
    transport = _FakeTransport([_catalog_resp()])
    res = dual.resolve_csv_resource(transport=transport)
    assert res['mode'] == 'catalog'
    assert res['datasetId'] == dual.CATALOG_DATASET_ID
    assert res['resolvedCsvUrl'] == dual.CSV_URL
    assert res['catalogApiUrl'] == dual.CATALOG_API_URL
    assert res['datasetPageUrl'] == dual.CATALOG_DATASET_PAGE
    assert res['resourceId'] == _ROOM_RESOURCE['id']
    assert res['resolvedAt'].endswith('Z')
    assert transport.calls[0]['url'] == dual.CATALOG_API_URL


def test_catalog_resolver_accepts_changed_but_allowlisted_url():
    changed = dict(_ROOM_RESOURCE, url=_ALT_CSV)
    res = dual.resolve_csv_resource(transport=_FakeTransport([_catalog_resp(
        _catalog_body([changed]))]))
    assert res['resolvedCsvUrl'] == _ALT_CSV, 'catalog 改 URL 但仍在 allowlist 就採用'


@pytest.mark.parametrize('resources', [
    [],
    [dict(_ROOM_RESOURCE, state='deleted')],
    [dict(_ROOM_RESOURCE, format='JSON')],
    [dict(_ROOM_RESOURCE, name='Washing Machines', description='Washing Machines')],
    [_ROOM_RESOURCE, dict(_ROOM_RESOURCE, id='second-uuid')],
])
def test_catalog_resolver_missing_or_ambiguous_fails_closed(resources):
    with pytest.raises(dual.CatalogAmbiguous):
        dual.resolve_csv_resource(transport=_FakeTransport([_catalog_resp(
            _catalog_body(resources))]))


def test_catalog_resolver_rejects_unknown_host_resource_url():
    bad = dict(_ROOM_RESOURCE, url='https://evil.example.com/meels_rac.csv')
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([_catalog_resp(
            _catalog_body([bad]))]))


def test_catalog_resolver_rejects_redirect_off_catalog_host():
    resp = _catalog_resp(final_url='https://evil.example.com/package_show')
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([resp]))


def test_catalog_resolver_rejects_dataset_identity_mismatch():
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([_catalog_resp(
            _catalog_body([_ROOM_RESOURCE], dataset_name='other-dataset'))]))


def test_catalog_resolver_html_masquerade_and_bad_json_fail_closed():
    html = _catalog_resp(body=b'<!DOCTYPE html><html>login</html>',
                         headers={'Content-Type': 'text/html'})
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([html]))
    bad = _catalog_resp(body=b'{not-json', headers={'Content-Type': 'application/json'})
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([bad]))
    not_ok = _catalog_resp(body=_catalog_body([_ROOM_RESOURCE], success=False))
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([not_ok]))


def test_catalog_resolver_transient_outage_allows_only_last_known_good_fallback():
    transport = _FakeTransport([
        dual.DualSourceError('網絡錯誤', kind='network_error'),
        dual.DualSourceError('HTTP 503', kind='http_error', diff={'status': 503}),
    ])
    with pytest.raises(dual.CatalogUnavailable):
        dual.resolve_csv_resource(transport=transport, max_attempts=2)
    fb = dual.last_known_good_resolution(reason='catalog_unavailable',
                                        now=1759104000.0)
    assert fb['mode'] == 'last-known-good-fallback'
    assert fb['resolvedCsvUrl'] == dual.CSV_URL, 'fallback 仍係批准 direct URL（唔用 cache）'
    assert fb['resourceId'] is None


def test_catalog_resolver_hard_4xx_is_invalid_not_fallbackable():
    transport = _FakeTransport([
        dual.DualSourceError('HTTP 403', kind='http_error', diff={'status': 403}),
    ])
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=transport, max_attempts=3)
    assert len(transport.calls) == 1, '403 唔應該再 retry／fallback'


def test_fetch_csv_raw_validates_request_and_redirect_urls():
    evil = dual.RawResponse(url='https://evil.example.com/meels_rac.csv', status=200,
                            body=_rows([_row('R')]), headers={},
                            fetchedAt='2026-09-29T00:00:00Z')
    with pytest.raises(dual.CatalogInvalid):
        dual.fetch_csv_raw(url='https://evil.example.com/meels_rac.csv',
                           transport=_FakeTransport([evil]))
    redirected = dual.RawResponse(url=dual.CSV_URL, status=200, body=_rows([_row('R')]),
                                  headers={}, fetchedAt='2026-09-29T00:00:00Z',
                                  finalUrl='https://evil.example.com/meels_rac.csv')
    with pytest.raises(dual.CatalogInvalid):
        dual.fetch_csv_raw(transport=_FakeTransport([redirected]))


def test_fetch_csv_raw_rejects_html_masquerade_and_empty_bytes():
    html = dual.RawResponse(url=dual.CSV_URL, status=200, body=b'<html>no csv</html>',
                            headers={'Content-Type': 'text/html'},
                            fetchedAt='2026-09-29T00:00:00Z')
    with pytest.raises(dual.DualSourceError) as exc:
        dual.fetch_csv_raw(transport=_FakeTransport([html]))
    assert exc.value.kind == 'content_type'
    empty = dual.RawResponse(url=dual.CSV_URL, status=200, body=b'   ',
                             headers={'Content-Type': 'text/csv'},
                             fetchedAt='2026-09-29T00:00:00Z')
    with pytest.raises(dual.DualSourceError) as exc:
        dual.fetch_csv_raw(transport=_FakeTransport([empty]))
    assert exc.value.kind == 'csv_incomplete'


def test_verify_rejects_304_cached_bytes_and_uses_resolved_catalog_summary():
    csv_rows = [_row('REG-1')]
    pag_rows = [_paginated(_row('REG-1'))[0]]
    resolution = {
        'schemaVersion': 1, 'mode': 'catalog', 'datasetId': dual.CATALOG_DATASET_ID,
        'resourceId': _ROOM_RESOURCE['id'], 'resourceName': 'Room Air Conditioners',
        'catalogApiUrl': dual.CATALOG_API_URL, 'datasetPageUrl': dual.CATALOG_DATASET_PAGE,
        'resolvedCsvUrl': _ALT_CSV, 'resolvedAt': '2026-09-29T00:00:00Z',
    }
    resp = dual.RawResponse(url=_ALT_CSV, status=200, body=_rows(csv_rows),
                            headers={}, fetchedAt='2026-09-29T00:00:00Z')
    out = dual.verify_dual_source(paginated_rows=pag_rows,
                                  paginated_raw_records=[{'page': 1}],
                                  retrieved_at='2026-09-29T00:00:00Z', min_rows=1,
                                  raw_response=resp, resolution=resolution)
    assert out['csv']['sourceUrl'] == _ALT_CSV
    assert out['csv']['resolvedUrl'] == _ALT_CSV
    assert out['csv']['catalog']['datasetId'] == dual.CATALOG_DATASET_ID
    not_modified = dual.RawResponse(url=_ALT_CSV, status=304, body=b'', headers={},
                                    fetchedAt='2026-09-29T00:00:00Z', notModified=True)
    with pytest.raises(dual.DualSourceError) as exc:
        dual.verify_dual_source(paginated_rows=pag_rows,
                                paginated_raw_records=[{'page': 1}],
                                retrieved_at='2026-09-29T00:00:00Z', min_rows=1,
                                raw_response=not_modified, resolution=resolution)
    assert exc.value.kind == 'csv_not_modified', '304 唔可以用 cached bytes'


def test_integration_catalog_outage_uses_last_known_good_fresh_bytes(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    pages, rows = _fake_pages()
    calls = {}

    def fake_fetch(p):
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch

    def resolver(**kw):
        raise dual.CatalogUnavailable('CKAN 下線', diff={'status': 503})

    fetch.resolve_csv_source = resolver

    def csv_source(*, url=None, **kw):
        calls['url'] = url
        return _resp(_rows(rows))

    fetch.fetch_csv_source = csv_source
    assert fetch.main() is None
    assert calls['url'] == dual.CSV_URL, 'fallback 只可以用批准 direct URL 重新抓 bytes'
    receipt = json.loads((env / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert receipt['success'] is True
    assert receipt['catalog']['mode'] == 'last-known-good-fallback'
    assert receipt['catalog']['resolvedCsvUrl'] == dual.CSV_URL


def test_integration_catalog_invalid_url_fails_closed_no_fallback(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    old = env / 'emsd_空調能源標籤.csv'
    old.write_bytes(b'OLD-CSV')
    pages, rows = _fake_pages()
    fetch.fetch_page = lambda p: pages[p - 1] if p - 1 < len(pages) else ''
    called = {'csv': False}

    def resolver(**kw):
        raise dual.CatalogInvalid('resource URL host 唔允許')

    def csv_source(**kw):
        called['csv'] = True
        return _resp(_rows(rows))

    fetch.resolve_csv_source = resolver
    fetch.fetch_csv_source = csv_source
    with pytest.raises(SystemExit) as exc:
        fetch.main()
    assert exc.value.code == 1
    assert called['csv'] is False, 'catalog 提供 invalid URL 時唔可以有 silent fallback'
    assert old.read_bytes() == b'OLD-CSV', 'fail-closed 保留上一生產 CSV'
    receipt = json.loads((env / 'emsd_receipt.json').read_text(encoding='utf-8'))
    assert receipt['success'] is False and receipt['dualSource']['equal'] is False
    assert receipt['dualSource']['errorKind'] == 'catalog_invalid'


# ---------------------------------------------------------------- redirect policy／provider（2026-09-29 返修）

import http.server  # noqa: E402
import threading  # noqa: E402


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/start':
            self.send_response(302)
            self.send_header('Location',
                             f'http://127.0.0.1:{self.server.server_port}/blocked')
            self.end_headers()
        elif self.path == '/blocked':
            self.server.blocked_hits += 1
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'BLOCKED-REACHED')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def redirect_server():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _RedirectHandler)
    server.blocked_hits = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_http_get_blocks_cross_origin_redirect_before_request(redirect_server):
    """CSV validator：redirect target 未批准 → 未跟過去之前已經 raise；blocked 零請求。"""
    port = redirect_server.server_port
    with pytest.raises(dual.DualSourceError) as exc:
        dual.http_get(f'http://127.0.0.1:{port}/start', timeout=5,
                      validator=dual.validate_official_csv_url)
    assert isinstance(exc.value, dual.CatalogInvalid)
    assert redirect_server.blocked_hits == 0, '未批准 redirect 目的地必須零請求'


def test_http_get_blocks_cross_origin_catalog_redirect_before_request(redirect_server):
    port = redirect_server.server_port
    with pytest.raises(dual.DualSourceError) as exc:
        dual.http_get(f'http://127.0.0.1:{port}/start', timeout=5,
                      validator=dual.validate_catalog_api_url)
    assert isinstance(exc.value, dual.CatalogInvalid)
    assert redirect_server.blocked_hits == 0, 'CKAN 未批准 redirect 目的地必須零請求'


def test_http_get_follows_redirect_when_validator_accepts(redirect_server):
    """同一 origin（validator 通過）嘅 redirect 仍然要正常跟，證明冇一刀切停用 redirect。"""
    port = redirect_server.server_port
    resp = dual.http_get(f'http://127.0.0.1:{port}/start', timeout=5,
                         validator=lambda _url: None)
    assert resp.body == b'BLOCKED-REACHED'
    assert redirect_server.blocked_hits == 1


def test_resolver_and_csv_fetch_pass_strict_validators_to_transport():
    cat = _FakeTransport([_catalog_resp()])
    dual.resolve_csv_resource(transport=cat, max_attempts=1)
    assert cat.calls[0]['validator'] is dual.validate_catalog_api_url
    csvt = _FakeTransport([_resp(_rows([_row('R')]))])
    dual.fetch_csv_raw(transport=csvt, max_attempts=1)
    assert csvt.calls[0]['validator'] is dual.validate_official_csv_url


def test_catalog_final_url_full_identity_required():
    wrong_path = _catalog_resp(
        final_url='https://data.gov.hk/en-data/api/3/action/other'
                  '?id=hk-emsd-emsd1-meels-listed-models')
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([wrong_path]), max_attempts=1)
    wrong_query = _catalog_resp(
        final_url='https://data.gov.hk/en-data/api/3/action/package_show?id=other')
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([wrong_query]), max_attempts=1)


def test_catalog_resolver_rejects_wrong_or_missing_or_non_dict_provider():
    wrong = _catalog_resp(_catalog_body([_ROOM_RESOURCE], provider='other-org'))
    with pytest.raises(dual.CatalogInvalid) as exc:
        dual.resolve_csv_resource(transport=_FakeTransport([wrong]), max_attempts=1)
    assert 'provider' in str(exc.value) or exc.value.diff.get('provider') == 'other-org'
    missing = _catalog_resp(_catalog_body([_ROOM_RESOURCE], include_provider=False))
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([missing]), max_attempts=1)
    payload = json.loads(_catalog_body([_ROOM_RESOURCE]).decode('utf-8'))
    payload['result']['organization'] = 'hk-emsd'
    bad_type = _catalog_resp(json.dumps(payload).encode('utf-8'))
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=_FakeTransport([bad_type]), max_attempts=1)


def test_catalog_injected_4xx_fails_closed_and_5xx_is_unavailable():
    t403 = _FakeTransport([_catalog_resp(status=403)])
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=t403, max_attempts=3)
    assert len(t403.calls) == 1, '403 唔可以 retry／fallback'
    t404 = _FakeTransport([_catalog_resp(status=404)])
    with pytest.raises(dual.CatalogInvalid):
        dual.resolve_csv_resource(transport=t404, max_attempts=3)
    assert len(t404.calls) == 1
    t503 = _FakeTransport([_catalog_resp(status=503), _catalog_resp(status=503)])
    with pytest.raises(dual.CatalogUnavailable):
        dual.resolve_csv_resource(transport=t503, max_attempts=2)
    assert len(t503.calls) == 2, '503 可以 bounded retry，最後當 unavailable'
