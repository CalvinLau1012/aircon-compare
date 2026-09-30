# -*- coding: utf-8 -*-
"""BigGo staged batch 語義回歸（P0；無外網、無 repo 寫入）。

- 網絡階段（smoke／batch／review）canonical 四檔 byte 不變；
- 三態 counters、outcome ledger、effects、pre/post state hash；
- 只有 completed 才會有完整 effects；partial／aborted 冇 apply；
- 403/429 冷卻唔再寫 local meta（由 coordinator 記錄）。
"""
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402


def _entry(price='$1,000起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=M1', 'updated': '2026-09-28'}


def _env(tmp_path, monkeypatch, todo, search, meta=None, *, blacklist=None,
         tracking=None, snapshot=None):
    state = {
        'prices_bytes': None,
        'meta_bytes': None,
        'tracking_bytes': None,
        'blacklist_bytes': None,
        'saved': [],
    }
    base_meta = {'price_batch_start': '2026-09-01', 'price_batch_idx': 0}
    base_meta.update(meta or {})
    snapshot = snapshot if snapshot is not None else {}
    tracking = tracking if tracking is not None else {}
    blacklist = blacklist if blacklist is not None else {}
    snapshot_path = str(tmp_path / 'biggo_prices.json')
    meta_path = str(tmp_path / 'prices_meta.json')
    tracking_path = str(tmp_path / 'model_status.json')
    blacklist_path = str(tmp_path / 'model_blacklist.json')
    with open(snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(base_meta, f, ensure_ascii=False, indent=2)
    with open(tracking_path, 'w', encoding='utf-8') as f:
        json.dump(tracking, f, ensure_ascii=False, indent=2)
    with open(blacklist_path, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'updated': '2026-09-30', 'models': blacklist},
                  f, ensure_ascii=False, indent=2)

    monkeypatch.setattr(fetch_biggo, 'OUT_PATH', snapshot_path)
    monkeypatch.setattr(fetch_biggo, 'load_meta', lambda: dict(base_meta))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: (todo, 0, len(todo)))
    monkeypatch.setattr(fetch_biggo, 'filter_active', lambda models, key_of=None: (todo, []))
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: list(todo))
    monkeypatch.setattr(fetch_biggo, 'load_brand_lookup', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'protected_models', lambda: set())
    monkeypatch.setattr(fetch_biggo, '_read_tracking',
                        lambda: (json.load(open(tracking_path, encoding='utf-8'))
                                 if os.path.exists(tracking_path) else {}))
    monkeypatch.setattr(fetch_biggo, 'load_blacklist',
                        lambda: (json.load(open(blacklist_path, encoding='utf-8')).get('models', {})
                                 if os.path.exists(blacklist_path) else {}))
    monkeypatch.setattr(fetch_biggo, '_search_tri_state', search)
    state['paths'] = {'snapshot': snapshot_path, 'meta': meta_path,
                      'tracking': tracking_path, 'blacklist': blacklist_path}
    return state


def _bytes(state):
    out = {}
    for key, path in state['paths'].items():
        with open(path, 'rb') as f:
            out[key] = f.read()
    return out


def test_partial_net_errors_staged_no_writes(tmp_path, monkeypatch):
    def search(model):
        if model == 'M1':
            return model, _entry(), True
        if model == 'M2':
            return model, None, True
        return model, None, False

    state = _env(tmp_path, monkeypatch, ['M1', 'M2', 'M3'], search)
    before = _bytes(state)
    payload = fetch_biggo.run_price_batch()
    assert payload['status'] == 'partial-net-errors'
    assert payload['counters'] == {'got': 1, 'cleanMiss': 1, 'netErrors': 1}
    assert payload['snapshot'] == {'M1': _entry()}, 'staged 真實報價保留在回傳 payload'
    assert 'effects' not in payload, 'partial 唔可以出 effects（禁止 apply）'
    assert _bytes(state) == before, 'canonical 四檔 byte 不變'


def test_complete_slice_returns_effects_and_is_derived_from_base(tmp_path, monkeypatch):
    def search(model):
        return (model, _entry(), True) if model == 'M1' else (model, None, True)

    base = {'OLD-1': _entry('$500起')}
    state = _env(tmp_path, monkeypatch, ['M1', 'M2'], search, snapshot=base)
    before = _bytes(state)
    payload = fetch_biggo.run_price_batch()
    assert payload['status'] == 'completed'
    assert payload['baseSnapshot'] == base
    assert payload['snapshot'] == {'OLD-1': _entry('$500起'), 'M1': _entry()}
    assert payload['counters'] == {'got': 1, 'cleanMiss': 1, 'netErrors': 0}
    # base + priced outcomes == snapshot（舊 key 保留）
    expected = dict(base, **{'M1': _entry()})
    assert payload['snapshot'] == expected
    assert payload['effects']['trackingUpserts'], 'clean miss 要有 tracking upsert'
    assert payload['preState']['trackingHash'] != payload['postState']['trackingHash']
    assert _bytes(state) == before, 'completed 網絡階段仍然零本地寫入'


def test_abort_on_40_consecutive_keeps_no_local_writes(tmp_path, monkeypatch):
    todo = [f'M{i}' for i in range(45)]

    def search(model):
        return model, None, False

    state = _env(tmp_path, monkeypatch, todo, search)
    before = _bytes(state)
    payload = fetch_biggo.run_price_batch()
    assert payload['status'] == 'aborted'
    assert payload.get('projectCooldown') is True
    assert payload['counters']['netErrors'] >= 40
    assert _bytes(state) == before


def test_not_active_and_cooldown_status_no_writes(tmp_path, monkeypatch):
    state = _env(tmp_path, monkeypatch, [], lambda m: (m, None, False))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: None)
    before = _bytes(state)
    assert fetch_biggo.run_price_batch()['status'] == 'not-active'
    assert _bytes(state) == before

    state2 = _env(tmp_path, monkeypatch, ['M1'], lambda m: (m, _entry(), True),
                  meta={'blocked_until': 2 ** 31})
    before2 = _bytes(state2)
    assert fetch_biggo.run_price_batch()['status'] == 'cooldown-skip'
    assert _bytes(state2) == before2


def test_outcomes_and_snapshot_identity(tmp_path, monkeypatch):
    def search(model):
        return (model, _entry(), True) if model == 'M1' else (model, None, True)

    state = _env(tmp_path, monkeypatch, ['M1', 'M2'], search)
    payload = fetch_biggo.run_price_batch()
    by_model = {o['model']: o for o in payload['outcomes']}
    assert by_model['M1']['outcome'] == 'priced'
    assert by_model['M1']['price'] == payload['snapshot']['M1']
    assert by_model['M2']['outcome'] == 'clean_miss'
    assert 'price' not in by_model['M2']


def test_force_batch_staged_no_writes(tmp_path, monkeypatch):
    def search(model):
        return model, _entry(), True

    state = _env(tmp_path, monkeypatch, ['M1', 'M2'], search)
    before = _bytes(state)
    payload = fetch_biggo.run_force_batch(None, smoke=False, exit_on_fail=False)
    assert payload['status'] == 'completed'
    assert payload['counters']['got'] == 2
    assert 'last_force_batch' in payload.get('metaFields', {})
    assert _bytes(state) == before


def test_blacklist_review_staged_no_writes(tmp_path, monkeypatch):
    """黑名單復核（查有價 → 復活）必須 staged：四檔零寫入，效果只在 payload。"""
    def search(model):
        return model, _entry(), True

    state = _env(tmp_path, monkeypatch, ['M1'], search,
                 blacklist={'RASONIC|ABC': {'status': 'auto_discontinued'}})
    before = _bytes(state)
    payload = fetch_biggo.run_price_batch()
    assert payload['status'] == 'completed'
    assert payload['blacklistReview']['reviewed'][0]['result'] == 'revived'
    assert payload['effects']['blacklistRemovals'] == ['RASONIC|ABC']
    assert 'ABC' in payload['snapshot']
    assert _bytes(state) == before, 'blacklist review 都唔可以喺網絡階段寫檔'
