# -*- coding: utf-8 -*-
"""BigGo per-request hard cap limiter（P0）＋ fetch 層 cap/abort 回歸；完全離線。"""
import json
import os
import sys
import threading
import time
import urllib.parse

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402
from biggo_limiter import BigGoLimitExceeded, RequestLimiter  # noqa: E402


def _entry(price='$1,000起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=M1', 'updated': '2026-09-28'}


class _Resp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _payload(model):
    return json.dumps({'list': [{
        'title': f'窗口機 {model} 變頻冷氣',
        'price': '1200', 'nindex': 'hk_shop'}]}).encode('utf-8')


def _fake_urlopen(counter):
    def urlopen(req, timeout=None):
        url = getattr(req, 'full_url', str(req))
        model = urllib.parse.unquote(url.split('/search/', 1)[1].split('/product', 1)[0])
        counter.append(model)
        return _Resp(_payload(model))
    return urlopen


def _env(tmp_path, monkeypatch, todo, *, blacklist=None, meta=None):
    base_meta = {'price_batch_start': '2026-09-01', 'price_batch_idx': 0}
    base_meta.update(meta or {})
    blacklist = blacklist if blacklist is not None else {}
    paths = {
        'snapshot': str(tmp_path / 'biggo_prices.json'),
        'meta': str(tmp_path / 'prices_meta.json'),
        'tracking': str(tmp_path / 'model_status.json'),
        'blacklist': str(tmp_path / 'model_blacklist.json'),
    }
    for path, payload in ((paths['snapshot'], {}), (paths['meta'], base_meta),
                          (paths['tracking'], {}),
                          (paths['blacklist'], {'version': 1, 'updated': '2026-09-30',
                                                'models': blacklist})):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    monkeypatch.setattr(fetch_biggo, 'OUT_PATH', paths['snapshot'])
    monkeypatch.setattr(fetch_biggo, 'load_meta', lambda: dict(base_meta))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: (todo, 0, len(todo)))
    monkeypatch.setattr(fetch_biggo, 'filter_active', lambda models, key_of=None: (todo, []))
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: list(todo))
    monkeypatch.setattr(fetch_biggo, 'load_brand_lookup', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'protected_models', lambda: set())
    monkeypatch.setattr(fetch_biggo, '_read_tracking', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'load_blacklist', lambda: dict(blacklist))
    monkeypatch.setattr(fetch_biggo, 'MIN_PACE', 0.0)
    return paths


# ---------------------------------------------------------------- limiter 單元


def test_limiter_per_kind_caps_and_sticky_reached():
    lim = RequestLimiter(search_cap=2, token_cap=1)
    lim.reserve('search')
    lim.reserve('search')
    with pytest.raises(BigGoLimitExceeded):
        lim.reserve('search')
    assert lim.reached is True
    lim.reserve('token')
    with pytest.raises(BigGoLimitExceeded):
        lim.reserve('token')
    snap = lim.snapshot()
    assert snap['searchUsed'] == 2 and snap['tokenUsed'] == 1 and snap['reached'] is True


def test_limiter_provider_cap_counts_total_requests():
    lim = RequestLimiter(search_cap=10, token_cap=5, provider_cap=2)
    lim.reserve('search')
    lim.reserve('token')
    with pytest.raises(BigGoLimitExceeded):
        lim.reserve('search')
    assert lim.snapshot()['providerCap'] == 2


def test_limiter_thread_safe_exact_cap():
    lim = RequestLimiter(search_cap=5, token_cap=5)
    outcomes = []
    lock = threading.Lock()

    def worker():
        try:
            lim.reserve('search')
            with lock:
                outcomes.append(1)
        except BigGoLimitExceeded:
            with lock:
                outcomes.append(0)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(outcomes) == 5, f'精確 5 個成功，實得 {sum(outcomes)}'


# ---------------------------------------------------------------- fetch 整合


def test_batch_stops_at_search_cap_and_is_partial_not_clean_miss(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', _fake_urlopen(calls))
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2', 'MODEL3'])
    fetch_biggo.set_request_limiter(RequestLimiter(search_cap=2, token_cap=1))
    try:
        payload = fetch_biggo.run_price_batch()
    finally:
        fetch_biggo.set_request_limiter(None)
    assert len(calls) == 2, 'cap 到即唔可以再發請求'
    assert payload['status'] == 'partial-net-errors'
    assert payload['counters']['netErrors'] == 1
    assert payload['counters']['cleanMiss'] == 0, 'cap refusal 唔可以當 clean miss'


def test_batch_within_cap_completes(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', _fake_urlopen(calls))
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2'])
    fetch_biggo.set_request_limiter(RequestLimiter(search_cap=4, token_cap=1))
    try:
        payload = fetch_biggo.run_price_batch()
    finally:
        fetch_biggo.set_request_limiter(None)
    assert payload['status'] == 'completed'
    assert len(calls) == 2 and payload['counters']['got'] == 2


def test_review_requests_count_into_cap(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', _fake_urlopen(calls))
    blacklist = {f'BRAND|REV{i}': {'status': 'auto_discontinued'} for i in range(3)}
    _env(tmp_path, monkeypatch, ['MODEL1'], blacklist=blacklist)
    # cap 恰好 = 2×batch + 2×review = 8 → 全部可查（batch 1 + review 3）
    fetch_biggo.set_request_limiter(RequestLimiter(search_cap=8, token_cap=1))
    try:
        payload = fetch_biggo.run_price_batch()
    finally:
        fetch_biggo.set_request_limiter(None)
    assert payload['status'] == 'completed'
    assert len(calls) == 4, f'batch 1 + review 3 = 4，實得 {len(calls)}'

    # cap 少過 review 所需 → review limitReached → fail-closed partial
    calls.clear()
    _env(tmp_path, monkeypatch, ['MODEL1'], blacklist=blacklist)
    fetch_biggo.set_request_limiter(RequestLimiter(search_cap=3, token_cap=1))
    try:
        payload = fetch_biggo.run_price_batch()
    finally:
        fetch_biggo.set_request_limiter(None)
    assert payload['status'] == 'review-limit-reached'
    assert len(calls) == 3


def test_abort_check_stops_queued_requests(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', _fake_urlopen(calls))
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2', 'MODEL3'])
    fetch_biggo.set_request_limiter(RequestLimiter(search_cap=10, token_cap=1))
    fetch_biggo.set_abort_check(lambda: len(calls) >= 1)
    try:
        payload = fetch_biggo.run_price_batch(should_abort=lambda: len(calls) >= 1)
    finally:
        fetch_biggo.set_request_limiter(None)
        fetch_biggo.set_abort_check(None)
    assert len(calls) == 1, 'abort 後唔可以再向網絡發請求'
    assert payload['status'] in ('aborted', 'partial-net-errors')


def test_review_loop_stops_new_queries_after_abort(monkeypatch, tmp_path):
    calls = []
    lock = threading.Lock()

    def slow_search(model):
        with lock:
            calls.append(model)
        time.sleep(0.05)
        return model, _entry(), True

    monkeypatch.setattr(fetch_biggo, '_search_tri_state', slow_search)
    tracking = {}
    blacklist = {f'BRAND|REV{i}': {'status': 'auto_discontinued'} for i in range(5)}
    review_todo = sorted(blacklist)
    out = fetch_biggo._run_blacklist_review(
        0, {}, tracking=tracking, blacklist=blacklist, brand_lookup={},
        review_todo=review_todo, should_abort=lambda: len(calls) >= 1)
    assert out['aborted'] is True
    assert len(out['reviewed']) < 5, 'abort 後唔應該處理晒全部 review'
