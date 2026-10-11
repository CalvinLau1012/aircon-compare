# -*- coding: utf-8 -*-
"""`biggo_candidate.select_candidate()` 離線測試（無網絡、無 provider）。

覆蓋：由當前 eligible dataset 確定性揀一個候選；fixture dataset 改變即結果改變；
canonical blacklist／active eligibility 排除；空 eligible = 明確 no-model；
唔靠 cached price；每日 runner 用共享 selector 而唔用 legacy 固定清單。
"""
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import biggo_candidate as candidate_mod  # noqa: E402
import fetch_biggo  # noqa: E402
import model_lifecycle  # noqa: E402

BRAND = {'AAA1': 'BRAND', 'BBB2': 'BRAND', 'ZZZ9': 'BRAND'}


def _patch_blacklist(monkeypatch, mapping):
    """令 filter_active 同 selector 第二重 blacklist 都用同一個注入值。"""
    monkeypatch.setattr(model_lifecycle, 'load_blacklist', lambda: dict(mapping))
    monkeypatch.setattr(candidate_mod, 'load_blacklist', lambda: dict(mapping))


def test_selection_changes_when_fixture_dataset_changes(monkeypatch):
    _patch_blacklist(monkeypatch, {})
    first, info = candidate_mod.select_candidate(
        models=['AAA-1', 'BBB-2'], brand_lookup=BRAND, blacklist=set())
    second, _ = candidate_mod.select_candidate(
        models=['BBB-2'], brand_lookup=BRAND, blacklist=set())
    assert first['model'] == 'AAA-1'
    assert second['model'] == 'BBB-2'
    assert first['canonicalKey'] == 'BRAND|AAA1'
    assert info == {'active': 2, 'blacklisted': 0, 'eligible': 2}


def test_selection_is_deterministic_by_canonical_key(monkeypatch):
    _patch_blacklist(monkeypatch, {})
    forward, _ = candidate_mod.select_candidate(
        models=['BBB-2', 'AAA-1'], brand_lookup=BRAND, blacklist=set())
    reverse, _ = candidate_mod.select_candidate(
        models=['AAA-1', 'BBB-2'], brand_lookup=BRAND, blacklist=set())
    assert forward['model'] == reverse['model'] == 'AAA-1'
    # 同一 canonical key 的兩個寫法只會留一個
    deduped, info = candidate_mod.select_candidate(
        models=['AAA-1', 'AAA-1'], brand_lookup=BRAND, blacklist=set())
    assert deduped['model'] == 'AAA-1'
    assert info['eligible'] == 1


def test_canonical_blacklist_excludes_candidates(monkeypatch):
    black_key = candidate_mod.canonical_model_key('BRAND', 'AAA-1')
    _patch_blacklist(monkeypatch, {black_key: {'reason': 'stub'}})
    selection, info = candidate_mod.select_candidate(
        models=['AAA-1', 'BBB-2'], brand_lookup=BRAND, blacklist={black_key})
    assert selection['model'] == 'BBB-2'
    assert info == {'active': 1, 'blacklisted': 1, 'eligible': 1}


def test_all_blacklisted_returns_explicit_no_model(monkeypatch):
    black = {candidate_mod.canonical_model_key('BRAND', m) for m in BRAND}
    _patch_blacklist(monkeypatch, {k: {'reason': 'stub'} for k in black})
    selection, info = candidate_mod.select_candidate(
        models=list(BRAND), brand_lookup=BRAND, blacklist=black)
    assert selection is None
    assert info['reason'] == 'no-active-eligible-model'
    assert info['active'] == 0 and info['blacklisted'] == len(BRAND)


def test_empty_dataset_returns_no_model_with_zero_side_effects(monkeypatch):
    _patch_blacklist(monkeypatch, {})
    selection, info = candidate_mod.select_candidate(
        models=[], brand_lookup=BRAND, blacklist=set())
    assert selection is None
    assert info == {'reason': 'no-active-eligible-model',
                    'active': 0, 'blacklisted': 0}


def test_default_models_come_from_fetch_biggo_load_models(monkeypatch):
    _patch_blacklist(monkeypatch, {})
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: ['ZZZ-9'])
    selection, _ = candidate_mod.select_candidate(
        brand_lookup=BRAND, blacklist=set())
    assert selection['model'] == 'ZZZ-9'
    assert selection['brand'] == 'BRAND'


def test_selection_has_no_cached_price_requirement(monkeypatch):
    _patch_blacklist(monkeypatch, {})
    selection, _ = candidate_mod.select_candidate(
        models=['AAA-1'], brand_lookup=BRAND, blacklist=set())
    assert 'no cached-price requirement' in selection['reason']
    assert 'no static list' in selection['reason']


def test_selection_makes_no_network_calls(monkeypatch):
    _patch_blacklist(monkeypatch, {})

    def forbidden(*args, **kwargs):
        raise AssertionError('selector 唔可以有任何網絡呼叫')

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', forbidden)
    selection, _ = candidate_mod.select_candidate(
        models=['AAA-1'], brand_lookup=BRAND, blacklist=set())
    assert selection['model'] == 'AAA-1'


def test_daily_runner_uses_shared_selector_not_legacy_static_list():
    path = os.path.join(BASE, 'scripts', 'biggo_stage_runner.py')
    with open(path, encoding='utf-8') as f:
        src = f.read()
    assert 'candidate_mod.select_candidate(' in src, 'runner 要用共享 selector'
    assert 'SMOKE_CANDIDATES' not in src, '每日路徑唔可以再用 legacy 固定清單'


def test_selector_rejects_nothing_silently_for_malformed_models(monkeypatch):
    """本地 selection failure（malformed dataset）要 raise 畀 runner 阻斷，唔可以當成功。"""
    _patch_blacklist(monkeypatch, {})
    with pytest.raises(Exception):
        candidate_mod.select_candidate(models=object(), brand_lookup=BRAND,
                                       blacklist=set())
