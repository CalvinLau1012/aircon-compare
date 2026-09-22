# -*- coding: utf-8 -*-
"""validate_data 契約負向回歸（唔以真實 count 代替契約）

用隔離 fixture（1700 行最小合約資料）測：型別、行形狀、數值、能源級別、
sentinel、空檔等都要阻斷；合法 fixture 要通過。
"""
import csv
import importlib.util
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location('validate_data_mod', os.path.join(BASE, 'validate_data.py'))
vd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vd)

HEADER = ['品牌', '型號', '參考編號', '年份', '能源效益級別(製冷)',
          '每年耗電量(製冷)(千瓦小時)', '製冷量(千瓦)', 'CSPF', '製冷劑',
          '能源效益級別(供暖)', '每年耗電量(供暖)(千瓦小時)', '供暖量(千瓦)',
          'HSPF', '資料提供者', '變頻']


def row(i):
    return [f'品牌{i % 5}', f'M{i:04d}', f'REF{i}', '2025', str(i % 5 + 1),
            str(100 + i % 50), '2.5', '3.5', 'R32', '不適用', '不適用', '不適用',
            '不適用', '供應商', '是' if i % 2 else '否']


def make_fixture(tmp_path, rows=1700):
    with open(tmp_path / 'emsd_空調能源標籤.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(HEADER)
        for i in range(rows):
            w.writerow(row(i))
    (tmp_path / 'prices.json').write_text(json.dumps(
        {f'M{i:04d}': {'price': '$1,000', 'pid': str(i)} for i in range(rows)}), encoding='utf-8')
    (tmp_path / 'specs_emsd.json').write_text(json.dumps(
        {f'M{i:04d}': {'size': '100x200x300'} for i in range(1700)}), encoding='utf-8')
    for name in ('specs.json', 'official_specs.json', 'rasonic_official.json',
                 'shew_official.json', 'pana_official.json', 'carrier_official.json',
                 'general_official.json', 'midea_official.json'):
        (tmp_path / name).write_text(json.dumps({'M1': {'size': 'x'}}), encoding='utf-8')


def test_valid_fixture_passes(tmp_path):
    make_fixture(tmp_path)
    assert vd.validate(str(tmp_path)) == []


def _errors_after_mutate(tmp_path, mutate):
    make_fixture(tmp_path)
    mutate()
    return vd.validate(str(tmp_path))


def _rewrite_csv(tmp_path, transform):
    p = tmp_path / 'emsd_空調能源標籤.csv'
    with open(p, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    rows = transform(rows)
    with open(p, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerows(rows)


def test_non_numeric_core_field_blocked(tmp_path):
    def mutate():
        _rewrite_csv(tmp_path, lambda rows: [[('abc' if i == 1 and j == 5 else c)
                                              for j, c in enumerate(r)] for i, r in enumerate(rows)])
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('數值欄' in e for e in errs), errs


def test_bad_energy_level_blocked(tmp_path):
    def mutate():
        _rewrite_csv(tmp_path, lambda rows: [[('9' if i == 1 and j == 4 else c)
                                              for j, c in enumerate(r)] for i, r in enumerate(rows)])
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('能源級別' in e for e in errs), errs


def test_bad_frequency_blocked(tmp_path):
    def mutate():
        _rewrite_csv(tmp_path, lambda rows: [[('X' if i == 1 and j == 14 else c)
                                              for j, c in enumerate(r)] for i, r in enumerate(rows)])
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('變頻欄' in e for e in errs), errs


def test_wrong_row_length_blocked(tmp_path):
    def mutate():
        _rewrite_csv(tmp_path, lambda rows: [r if i != 1 else r[:14] for i, r in enumerate(rows)])
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('15 欄' in e for e in errs), errs


def test_missing_brand_model_blocked(tmp_path):
    def mutate():
        _rewrite_csv(tmp_path, lambda rows: [[(('' if j == 1 else c) if i == 1 else c)
                                              for j, c in enumerate(r)] for i, r in enumerate(rows)])
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('缺品牌' in e for e in errs), errs


def test_prices_top_level_type_blocked(tmp_path):
    def mutate():
        (tmp_path / 'prices.json').write_text('[]', encoding='utf-8')
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('prices.json 頂層' in e for e in errs), errs


def test_prices_entry_type_blocked(tmp_path):
    def mutate():
        (tmp_path / 'prices.json').write_text('{"M1": "oops"}', encoding='utf-8')
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('prices.json entry' in e for e in errs), errs


def test_specs_emsd_entry_type_blocked(tmp_path):
    def mutate():
        (tmp_path / 'specs_emsd.json').write_text('{"M1": [1,2]}', encoding='utf-8')
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('specs_emsd.json entry' in e for e in errs), errs


def test_official_entry_type_blocked(tmp_path):
    def mutate():
        (tmp_path / 'specs.json').write_text('{"M1": 1}', encoding='utf-8')
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('specs.json entry' in e for e in errs), errs


def test_empty_official_blocked(tmp_path):
    def mutate():
        (tmp_path / 'specs.json').write_text('{}', encoding='utf-8')
    errs = _errors_after_mutate(tmp_path, mutate)
    assert any('為空' in e for e in errs), errs
