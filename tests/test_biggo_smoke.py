# -*- coding: utf-8 -*-
"""BigGo smoke 候選回歸（第 2 部分）

- 候選集中管理、跨品牌、有本地證據（核心 29／受保護／有快照報價／非黑名單）
- 依次探測：首個成功只用一次 API；第一個 no-price 才 fallback；全部失敗回 False
- 三態訊息分開：no-price vs unreachable（現行 _api_search 唔細分，如實報限制）
- 例外唔可以洩漏 secret
- 安全門禁不變：run_smoke False → CI 跳過批次
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402
from crawl_utils import canonical_model_key, load_brand_lookup, norm_model  # noqa: E402
from fetch_biggo import SMOKE_CANDIDATES, run_smoke  # noqa: E402


def _key(model, brand_of):
    return canonical_model_key(brand_of.get(norm_model(model)) or 'UNKNOWN', model)


def test_smoke_candidates_have_local_evidence_and_cross_brand():
    assert 2 <= len(SMOKE_CANDIDATES) <= 4, 'smoke 候選數量要 2–4 個（避免無謂 API 用量）'
    assert all(isinstance(c, dict) and c.get('model') and c.get('reason') for c in SMOKE_CANDIDATES)
    brands = {c.get('brand', '').split()[0] for c in SMOKE_CANDIDATES}
    assert len(brands) >= 2, f'smoke 候選要跨至少兩個品牌：{brands}'

    with open(os.path.join(BASE, 'biggo_prices.json'), encoding='utf-8') as f:
        prices = json.load(f)
    with open(os.path.join(BASE, 'model_blacklist.json'), encoding='utf-8') as f:
        black = json.load(f)['models']
    protected = fetch_biggo.protected_models()
    brand_of = load_brand_lookup()
    for c in SMOKE_CANDIDATES:
        model = c['model']
        snap = prices.get(model)
        assert isinstance(snap, dict) and str(snap.get('price', '')).startswith('$'), (
            f'{model} 本地快照冇可靠報價，唔應該做 smoke 候選')
        key = _key(model, brand_of)
        assert key in protected, f'{model}（{key}）唔係受保護型號'
        assert key not in black, f'{model}（{key}）已入停售黑名單'


def test_extract_price_filters_non_hk_and_picks_range():
    data = {'list': [
        {'title': 'RA-10RF 窗口式冷氣機', 'price': 2500, 'nindex': 'hk_shop'},
        {'title': 'RA-10RF 窗口式冷氣機', 'price': 3000, 'nindex': 'hk_shop2'},
        {'title': 'RA-10RF 冷氣機', 'price': 100, 'nindex': 'us_bid_aliexpress'},
        {'title': 'RA-10RF 遙控器', 'price': 50, 'nindex': 'hk_shop3'},
    ]}
    r = fetch_biggo._extract_price(data, 'RA-10RF')
    assert r and r['price'] == '$2,500-3,000' and r['merchants'] == 2
    assert fetch_biggo._extract_price({'list': []}, 'RA-10RF') is None


def test_smoke_first_candidate_success_uses_one_call(monkeypatch):
    calls = []

    def fake_search(model, jitter=(0.2, 0.6)):
        calls.append(model)
        return {'list': []}, True

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    monkeypatch.setattr(fetch_biggo, '_extract_price',
                        lambda data, model: {'price': '$2,500', 'merchants': 1})
    assert run_smoke() is True
    assert calls == [SMOKE_CANDIDATES[0]['model']], '首個成功後唔應該再探測其餘候選'


def test_smoke_falls_back_when_first_has_no_price(monkeypatch):
    calls = []

    def fake_search(model, jitter=(0.2, 0.6)):
        calls.append(model)
        return {'list': []}, True

    def fake_extract(data, model):
        return {'price': '$9,999', 'merchants': 1} if model == SMOKE_CANDIDATES[1]['model'] else None

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    monkeypatch.setattr(fetch_biggo, '_extract_price', fake_extract)
    assert run_smoke() is True
    assert calls == [SMOKE_CANDIDATES[0]['model'], SMOKE_CANDIDATES[1]['model']]
    assert len(calls) == 2, '第一個無價才 fallback；第二個成功即停'


def test_smoke_all_no_price_returns_false(monkeypatch, capsys):
    calls = []

    def fake_search(model, jitter=(0.2, 0.6)):
        calls.append(model)
        return {'list': []}, True

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    monkeypatch.setattr(fetch_biggo, '_extract_price', lambda data, model: None)
    assert run_smoke() is False
    assert len(calls) == len(SMOKE_CANDIDATES), '全部候選都要試過'
    out = capsys.readouterr().out
    assert 'no-price' in out or '無匹配報價' in out
    assert 'unreachable' not in out, 'API 正常時唔應該報 unreachable'


def test_smoke_all_unreachable_returns_false_and_reports_bucket(monkeypatch, capsys):
    monkeypatch.setattr(fetch_biggo, '_api_search', lambda model, jitter=(0.2, 0.6): (None, False))
    assert run_smoke() is False
    out = capsys.readouterr().out
    assert 'unreachable' in out, '要如實報粗分類，唔可以當成個別型號無價'
    assert '未細分' in out, '要講明現行 _api_search 唔細分網絡／限流／認證'


def test_smoke_exception_does_not_leak_secret(monkeypatch, capsys):
    secret = 'SUPERSECRET-XYZ-123'
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', secret)

    def boom(model, jitter=(0.2, 0.6)):
        raise RuntimeError(f'boom authorization Basic {secret}')

    monkeypatch.setattr(fetch_biggo, '_api_search', boom)
    assert run_smoke() is False
    captured = capsys.readouterr()
    assert secret not in (captured.out + captured.err), '例外內容唔可以連 secret 一齊輸出'


def test_smoke_accepts_explicit_candidate_list(monkeypatch):
    calls = []

    def fake_search(model, jitter=(0.2, 0.6)):
        calls.append(model)
        return {}, True

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    monkeypatch.setattr(fetch_biggo, '_extract_price', lambda data, model: None)
    assert run_smoke(['AAA-1', {'model': 'BBB-2', 'brand': 'X', 'reason': 'test'}]) is False
    assert calls == ['AAA-1', 'BBB-2']
