# -*- coding: utf-8 -*-
"""BigGo 價錢批次三態／推進語義回歸（無外網；全部 fixture）

- 任何 net_err → 批次 idx 唔推進（可重試未完成批次），網絡錯誤唔會當 clean miss
- 完整零錯誤才 advance_batch + 黑名單復核
- 40 連續失敗 → abort + cooldown，唔推進
- partial 成功嘅真實報價保留，但唔聲稱「全保留原樣」
"""
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402


def _env(tmp_path, monkeypatch, todo, search, meta=None):
    calls = {'advance': 0, 'record': [], 'review': 0, 'cooldown': 0, 'saved': []}
    base_meta = {'price_batch_start': '2026-09-01', 'price_batch_idx': 0}
    base_meta.update(meta or {})
    monkeypatch.setattr(fetch_biggo, 'OUT_PATH', str(tmp_path / 'biggo_prices.json'))
    monkeypatch.setattr(fetch_biggo, 'load_meta', lambda: dict(base_meta))
    monkeypatch.setattr(fetch_biggo, 'save_meta', lambda m: calls['saved'].append(dict(m)))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: (todo, 0, len(todo)))
    monkeypatch.setattr(fetch_biggo, 'filter_active', lambda models, key_of=None: (todo, []))
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: list(todo))
    monkeypatch.setattr(fetch_biggo, 'load_brand_lookup', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'protected_models', lambda: set())
    monkeypatch.setattr(fetch_biggo, 'record_results',
                        lambda rec, **kw: calls['record'].append(rec))
    monkeypatch.setattr(fetch_biggo, 'review_blacklist_batch',
                        lambda idx: calls.__setitem__('review', calls['review'] + 1))
    monkeypatch.setattr(fetch_biggo, 'advance_batch',
                        lambda m: calls.__setitem__('advance', calls['advance'] + 1) or True)
    monkeypatch.setattr(fetch_biggo, 'set_cooldown',
                        lambda: calls.__setitem__('cooldown', calls['cooldown'] + 1))
    monkeypatch.setattr(fetch_biggo, '_search_tri_state', search)
    return calls


def test_partial_net_errors_do_not_advance(tmp_path, monkeypatch):
    def search(model):
        if model == 'M1':
            return model, {'price': '$1,000'}, True
        if model == 'M2':
            return model, None, True
        return model, None, False

    calls = _env(tmp_path, monkeypatch, ['M1', 'M2', 'M3'], search)
    st = fetch_biggo.run_price_batch()
    assert st['status'] == 'partial-net-errors'
    assert st['netErrors'] == 1 and st['advanced'] is False
    assert calls['advance'] == 0, '有網絡錯誤唔可以推進批次'
    assert calls['review'] == 0, '唔完整批次唔會跑黑名單復核'
    assert calls['saved'] and calls['saved'][-1]['last_batch_status'] == 'partial-net-errors'
    data = json.load(open(tmp_path / 'biggo_prices.json', encoding='utf-8'))
    assert 'M1' in data, '真實報價要保留'
    assert 'M3' not in data, '網絡錯誤唔可以寫入做價／miss'
    rec = [m for m, _ in calls['record'][0]] if calls['record'] else []
    assert set(rec) == {'M1', 'M2'} and 'M3' not in rec, 'net_err 唔可以入 clean miss'


def test_complete_slice_advances_and_reviews(tmp_path, monkeypatch):
    def search(model):
        return (model, {'price': '$1,000'}, True) if model == 'M1' else (model, None, True)

    calls = _env(tmp_path, monkeypatch, ['M1', 'M2'], search)
    st = fetch_biggo.run_price_batch()
    assert st['status'] == 'completed' and st['advanced'] is True
    assert calls['advance'] == 1
    assert calls['review'] == 1
    assert calls['saved'][-1]['last_batch_status'] == 'completed'
    data = json.load(open(tmp_path / 'biggo_prices.json', encoding='utf-8'))
    assert set(data) == {'M1'}


def test_abort_on_40_consecutive_keeps_index_and_cools_down(tmp_path, monkeypatch):
    todo = [f'M{i}' for i in range(45)]

    def search(model):
        return model, None, False

    calls = _env(tmp_path, monkeypatch, todo, search)
    st = fetch_biggo.run_price_batch()
    assert st['status'] == 'aborted' and st['advanced'] is False
    assert calls['cooldown'] == 1
    assert calls['advance'] == 0
    assert calls['saved'][-1]['last_batch_status'] == 'aborted'
    assert calls['saved'][-1]['last_batch_net_errors'] >= 40


def test_not_active_and_cooldown_status(tmp_path, monkeypatch):
    calls = _env(tmp_path, monkeypatch, [], lambda m: (m, None, False))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: None)
    assert fetch_biggo.run_price_batch()['status'] == 'not-active'
    assert calls['advance'] == 0

    calls2 = _env(tmp_path, monkeypatch, ['M1'], lambda m: (m, {'price': '$1'}, True),
                  meta={'blocked_until': 2 ** 31})
    assert fetch_biggo.run_price_batch()['status'] == 'cooldown-skip'
    assert calls2['advance'] == 0
