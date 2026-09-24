# -*- coding: utf-8 -*-
"""BigGo smoke 候選與有界連線回歸（無外網；全部 mock）

- 候選集中管理、跨品牌、有本地證據（核心 29／受保護／有快照報價／非黑名單）
- smoke 有界：單次 attempt、每網絡階段 timeout=8s、唔等 60/90s 冷卻、錯誤後唔 sleep
- 首個有價即 True；只有明確 no-price 才試下一個候選；
  第一個 unreachable／例外立即 False，唔會試其餘候選
- 批次／正常查詢路徑仍用 `_api_search` 預設完整 retry（5 次）＋冷卻＋限速
- 例外唔可以洩漏 secret
- 安全門禁不變：run_smoke False → CI 跳過批次
"""
import json
import os
import sys
import urllib.error

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402
from crawl_utils import canonical_model_key, load_brand_lookup, norm_model  # noqa: E402
from fetch_biggo import SMOKE_CANDIDATES, run_smoke  # noqa: E402

SMOKE_KWARGS = {
    'max_attempts': 1,
    'timeout': fetch_biggo.SMOKE_TIMEOUT,
    'use_cooldown': False,
    'use_pace': False,
    'sleep_on_error': False,
}


def _key(model, brand_of):
    return canonical_model_key(brand_of.get(norm_model(model)) or 'UNKNOWN', model)


def _fake_search(calls):
    """記錄呼叫嘅 `_api_search` fake；回傳固定 (data, reachable)。"""

    def search(model, jitter=(0.2, 0.6), **kwargs):
        calls.append((model, kwargs))
        return {'list': []}, True

    return search


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


def test_smoke_timeout_design_is_bounded():
    assert fetch_biggo.SMOKE_TIMEOUT == 8, 'smoke 每網絡階段 timeout 應為約 8 秒'
    assert SMOKE_KWARGS == {
        'max_attempts': 1,
        'timeout': 8,
        'use_cooldown': False,
        'use_pace': False,
        'sleep_on_error': False,
    }


def test_smoke_probe_passes_single_bounded_attempt_params(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo, '_api_search', _fake_search(calls))
    monkeypatch.setattr(fetch_biggo, '_extract_price',
                        lambda data, model: {'price': '$2,500', 'merchants': 1})
    assert run_smoke() is True
    assert calls == [(SMOKE_CANDIDATES[0]['model'], SMOKE_KWARGS)], (
        'smoke 必須用單次 attempt／8 秒 timeout／無冷卻無限速參數，且首個有價即停')


def test_smoke_first_candidate_success_uses_one_call(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo, '_api_search', _fake_search(calls))
    monkeypatch.setattr(fetch_biggo, '_extract_price',
                        lambda data, model: {'price': '$2,500', 'merchants': 1})
    assert run_smoke() is True
    assert len(calls) == 1, '首個成功後唔應該再探測其餘候選'
    assert calls[0][0] == SMOKE_CANDIDATES[0]['model']


def test_smoke_falls_back_when_first_has_no_price(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo, '_api_search', _fake_search(calls))

    def fake_extract(data, model):
        return {'price': '$9,999', 'merchants': 1} if model == SMOKE_CANDIDATES[1]['model'] else None

    monkeypatch.setattr(fetch_biggo, '_extract_price', fake_extract)
    assert run_smoke() is True
    assert [m for m, _ in calls] == [SMOKE_CANDIDATES[0]['model'], SMOKE_CANDIDATES[1]['model']]
    assert len(calls) == 2, '第一個無價才 fallback；第二個成功即停'
    assert all(kw == SMOKE_KWARGS for _, kw in calls), '每個候選都要保持有界 smoke 參數'


def test_smoke_all_no_price_returns_false(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(fetch_biggo, '_api_search', _fake_search(calls))
    monkeypatch.setattr(fetch_biggo, '_extract_price', lambda data, model: None)
    assert run_smoke() is False
    assert len(calls) == len(SMOKE_CANDIDATES), '全部候選都要試過'
    out = capsys.readouterr().out
    assert 'no-price' in out or '無匹配報價' in out
    assert 'unreachable' not in out, 'API 正常時唔應該報 unreachable'


def test_smoke_unreachable_stops_without_trying_other_candidates(monkeypatch, capsys):
    calls = []

    def fake_search(model, jitter=(0.2, 0.6), **kwargs):
        calls.append((model, kwargs))
        return None, False

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    assert run_smoke() is False
    assert len(calls) == 1, '第一個 unreachable 要立即結束，唔可以試其餘候選'
    assert calls[0] == (SMOKE_CANDIDATES[0]['model'], SMOKE_KWARGS)
    out = capsys.readouterr().out
    assert 'unreachable' in out, '要如實報粗分類，唔可以當成個別型號無價'
    assert '未細分' in out, '要講明現行 _api_search 唔細分網絡／限流／認證'


def test_smoke_exception_stops_without_trying_other_candidates(monkeypatch, capsys):
    calls = []

    def boom(model, jitter=(0.2, 0.6), **kwargs):
        calls.append((model, kwargs))
        raise RuntimeError('boom')

    monkeypatch.setattr(fetch_biggo, '_api_search', boom)
    assert run_smoke() is False
    assert len(calls) == 1, '例外要立即結束，唔可以試其餘候選'
    out = capsys.readouterr().out
    assert 'RuntimeError' in out


def test_smoke_exception_does_not_leak_secret(monkeypatch, capsys):
    secret = 'SUPERSECRET-XYZ-123'
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', secret)

    def boom(model, jitter=(0.2, 0.6), **kwargs):
        raise RuntimeError(f'boom authorization Basic {secret}')

    monkeypatch.setattr(fetch_biggo, '_api_search', boom)
    assert run_smoke() is False
    captured = capsys.readouterr()
    assert secret not in (captured.out + captured.err), '例外內容唔可以連 secret 一齊輸出'


def test_smoke_accepts_explicit_candidate_list(monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo, '_api_search', _fake_search(calls))
    monkeypatch.setattr(fetch_biggo, '_extract_price', lambda data, model: None)
    assert run_smoke(['AAA-1', {'model': 'BBB-2', 'brand': 'X', 'reason': 'test'}]) is False
    assert [m for m, _ in calls] == ['AAA-1', 'BBB-2']


# ---------------------------------------------------------------- 有界連線（無 sleep 硬碰）

def test_api_search_smoke_mode_single_attempt_no_sleep_no_cooldown(monkeypatch):
    """smoke 模式：單次 urlopen、timeout=8、唔等冷卻／限速、錯誤後唔 sleep。"""
    observed = {'urlopen': 0, 'timeouts': [], 'sleeps': []}
    monkeypatch.delenv('BIGGO_CLIENT_ID', raising=False)
    monkeypatch.delenv('BIGGO_CLIENT_SECRET', raising=False)
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})

    def fake_urlopen(req, timeout=None):
        observed['urlopen'] += 1
        observed['timeouts'].append(timeout)
        raise urllib.error.HTTPError(getattr(req, 'full_url', 'https://x'), 429, 'rate',
                                     {'Retry-After': '60'}, None)

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(fetch_biggo.time, 'sleep', lambda s: observed['sleeps'].append(s))

    def forbidden(name):
        def fail():
            raise AssertionError(f'smoke 模式唔可以呼叫 {name}')
        return fail

    monkeypatch.setattr(fetch_biggo, '_wait_cooldown', forbidden('_wait_cooldown'))
    monkeypatch.setattr(fetch_biggo, '_wait_pace', forbidden('_wait_pace'))
    monkeypatch.setattr(fetch_biggo, '_global_cooldown',
                        lambda s: (_ for _ in ()).throw(AssertionError('smoke 唔可以設冷卻')))

    data, reachable = fetch_biggo._api_search(
        'RA-10RF', max_attempts=1, timeout=fetch_biggo.SMOKE_TIMEOUT,
        use_cooldown=False, use_pace=False, sleep_on_error=False)

    assert (data, reachable) == (None, False)
    assert observed == {'urlopen': 1, 'timeouts': [8], 'sleeps': []}


def test_get_access_token_timeout_is_forwarded(monkeypatch):
    monkeypatch.setenv('BIGGO_CLIENT_ID', 'cid')
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', 'csecret')
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})
    observed = {}

    class _Resp:
        def read(self):
            return b'{"access_token": "tok-123"}'

    def fake_urlopen(req, timeout=None):
        observed['timeout'] = timeout
        return _Resp()

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', fake_urlopen)
    assert fetch_biggo._get_access_token(timeout=fetch_biggo.SMOKE_TIMEOUT) == 'tok-123'
    assert observed['timeout'] == 8, 'smoke 嘅 token 階段都要用 8 秒 timeout'


def test_api_search_defaults_keep_full_retry_and_backoff(monkeypatch):
    """冇傳 smoke 參數時，批次語義不變：5 次 attempt + 原本 backoff。"""
    observed = {'urlopen': 0, 'sleeps': [], 'timeouts': []}
    monkeypatch.delenv('BIGGO_CLIENT_ID', raising=False)
    monkeypatch.delenv('BIGGO_CLIENT_SECRET', raising=False)
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})

    def fake_urlopen(req, timeout=None):
        observed['urlopen'] += 1
        observed['timeouts'].append(timeout)
        raise urllib.error.HTTPError('https://x', 500, 'boom', {}, None)

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(fetch_biggo.time, 'sleep', lambda s: observed['sleeps'].append(s))
    monkeypatch.setattr(fetch_biggo, '_wait_cooldown', lambda: None)
    monkeypatch.setattr(fetch_biggo, '_wait_pace', lambda: None)

    data, reachable = fetch_biggo._api_search('RA-10RF')

    assert (data, reachable) == (None, False)
    assert observed['urlopen'] == 5, '預設仍然係 5 次 retry'
    assert observed['sleeps'] == [5, 10, 15, 20, 25], '預設 backoff 次序不變'
    assert observed['timeouts'] == [20] * 5, '預設 network timeout 仍係 20 秒'


def test_api_search_defaults_429_uses_retry_after_cooldown(monkeypatch):
    observed = {'urlopen': 0}
    cooldowns = []
    monkeypatch.delenv('BIGGO_CLIENT_ID', raising=False)
    monkeypatch.delenv('BIGGO_CLIENT_SECRET', raising=False)
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})

    def fake_urlopen(req, timeout=None):
        observed['urlopen'] += 1
        raise urllib.error.HTTPError('https://x', 429, 'rate',
                                     {'Retry-After': '75'}, None)

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', fake_urlopen)
    monkeypatch.setattr(fetch_biggo, '_global_cooldown', lambda s: cooldowns.append(s))
    monkeypatch.setattr(fetch_biggo.time, 'sleep', lambda s: None)
    monkeypatch.setattr(fetch_biggo, '_wait_cooldown', lambda: None)
    monkeypatch.setattr(fetch_biggo, '_wait_pace', lambda: None)

    data, reachable = fetch_biggo._api_search('RA-10RF')

    assert (data, reachable) == (None, False)
    assert observed['urlopen'] == 5
    assert cooldowns == [75] * 5, '預設 429 仍然尊重 Retry-After 並全局冷卻'


def test_batch_tri_state_still_uses_default_search(monkeypatch):
    """批次／正常查詢行 `_search_tri_state` → `_api_search(model)`，唔會混入 smoke 參數。"""
    calls = []

    def fake_search(model, jitter=(0.2, 0.6), **kwargs):
        calls.append(kwargs)
        return {'list': []}, True

    monkeypatch.setattr(fetch_biggo, '_api_search', fake_search)
    model, result, ok = fetch_biggo._search_tri_state('RA-10RF')
    assert calls == [{}], '批次路徑必須用預設完整語義（無 smoke kwargs）'
    assert (model, result, ok) == ('RA-10RF', None, True)
