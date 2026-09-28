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


def _rows(csv_rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(HEADER)
    w.writerows(csv_rows)
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
              'Heating Capacity (kW)', 'HSPF', 'Data Provider']
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
    w.writerow([values[field_of[c]] for c in custom])
    parsed = dual.parse_open_data_csv(buf.getvalue().encode('utf-8-sig'))
    assert parsed['rowCount'] == 1
    assert list(parsed['rows'][0]) == list(_row('REG-1'))


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

    def __call__(self, url, *, headers=None, timeout=None, now=None):
        self.calls.append({'url': url, 'headers': dict(headers or {}), 'timeout': timeout})
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


def _wire(monkeypatch, pages, csv_body):
    def fake_fetch(p):
        return pages[p - 1] if p - 1 < len(pages) else ''

    fetch.fetch_page = fake_fetch
    fetch.fetch_csv_source = lambda **kw: _resp(csv_body)


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
