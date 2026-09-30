# -*- coding: utf-8 -*-
"""BigGo worker 生命周期回歸（P0 第三輪）：fetch 返回後冇 worker 存活、
limiter/abort context 清除後 0 outbound、abort 可截斷 cooldown/pace/retry、
未開始 futures 被 cancel、token 只取一次。全離線（fake urlopen／Event）。
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_biggo  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data


def _payload(model):
    return json.dumps({'list': [{
        'title': f'窗口機 {model} 變頻冷氣',
        'price': '1200', 'nindex': 'hk_shop'}]}).encode('utf-8')


def _model_from(req):
    url = getattr(req, 'full_url', str(req))
    return urllib.parse.unquote(url.split('/search/', 1)[1].split('/product', 1)[0])


def _env(tmp_path, monkeypatch, todo):
    base_meta = {'price_batch_start': '2026-09-01', 'price_batch_idx': 0}
    paths = {
        'snapshot': str(tmp_path / 'biggo_prices.json'),
        'meta': str(tmp_path / 'prices_meta.json'),
        'tracking': str(tmp_path / 'model_status.json'),
        'blacklist': str(tmp_path / 'model_blacklist.json'),
    }
    for path, obj in ((paths['snapshot'], {}), (paths['meta'], base_meta),
                      (paths['tracking'], {}),
                      (paths['blacklist'], {'version': 1, 'updated': '2026-09-30',
                                            'models': {}})):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
    monkeypatch.setattr(fetch_biggo, 'OUT_PATH', paths['snapshot'])
    monkeypatch.setattr(fetch_biggo, 'load_meta', lambda: dict(base_meta))
    monkeypatch.setattr(fetch_biggo, 'get_batch_todo', lambda models, m: (todo, 0, len(todo)))
    monkeypatch.setattr(fetch_biggo, 'filter_active', lambda models, key_of=None: (todo, []))
    monkeypatch.setattr(fetch_biggo, 'load_models', lambda: list(todo))
    monkeypatch.setattr(fetch_biggo, 'load_brand_lookup', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'protected_models', lambda: set())
    monkeypatch.setattr(fetch_biggo, '_read_tracking', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'load_blacklist', lambda: {})
    monkeypatch.setattr(fetch_biggo, 'MIN_PACE', 0.0)
    fetch_biggo.set_request_limiter(None)
    fetch_biggo.set_abort_check(None)


def test_run_price_batch_waits_for_running_workers(tmp_path, monkeypatch):
    started = threading.Event()
    stop = threading.Event()
    active = {'n': 0}
    lock = threading.Lock()

    def slow_search(model):
        with lock:
            active['n'] += 1
        started.set()
        stop.wait(5)
        with lock:
            active['n'] -= 1
        return model, None, False

    monkeypatch.setattr(fetch_biggo, '_search_tri_state', slow_search)
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2'])
    outcome = {}

    def call():
        outcome['payload'] = fetch_biggo.run_price_batch(
            should_abort=lambda: stop.is_set())

    t = threading.Thread(target=call)
    t.start()
    assert started.wait(3), 'worker 應該已啟動'
    stop.set()
    t.join(10)
    assert not t.is_alive(), 'run_price_batch 未返回'
    assert active['n'] == 0, 'run 返回後唔可以有 worker 存活'
    assert outcome['payload']['status'] == 'aborted'


def test_pending_futures_cancelled_on_abort(tmp_path, monkeypatch):
    started = []
    lock = threading.Lock()
    release = threading.Event()

    def slow_search(model):
        with lock:
            started.append(model)
        release.wait(5)
        return model, None, False

    monkeypatch.setattr(fetch_biggo, '_search_tri_state', slow_search)
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2', 'MODEL3', 'MODEL4', 'MODEL5'])
    abort = threading.Event()
    t = threading.Thread(target=lambda: fetch_biggo.run_price_batch(
        should_abort=lambda: abort.is_set()))
    t.start()
    for _ in range(200):
        if len(started) >= 2:
            break
        time.sleep(0.01)
    abort.set()
    release.set()
    t.join(10)
    assert not t.is_alive()
    assert len(started) == 2, f'未開始 futures 必須 cancel，實啟動 {len(started)}'


def test_no_outbound_after_abort_and_context_clear(tmp_path, monkeypatch):
    calls = []
    first_started = threading.Event()
    release = threading.Event()

    def urlopen(req, timeout=None):
        calls.append(_model_from(req))
        if len(calls) == 1:
            first_started.set()
            release.wait(5)
        return _Resp(_payload(_model_from(req)))

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', urlopen)
    _env(tmp_path, monkeypatch, ['MODEL1', 'MODEL2', 'MODEL3'])
    abort = threading.Event()
    t = threading.Thread(target=lambda: fetch_biggo.run_price_batch(
        should_abort=lambda: abort.is_set()))
    t.start()
    assert first_started.wait(3)
    abort.set()
    release.set()
    t.join(10)
    assert not t.is_alive()
    count_after_return = len(calls)
    time.sleep(0.3)
    assert len(calls) == count_after_return, '返回後／context 清除後唔可以再有 outbound'
    assert count_after_return <= 3


def test_abort_interrupts_retry_sleep_no_second_request(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.URLError('simulated network down')

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', urlopen)
    abort = threading.Event()

    def setter():
        time.sleep(0.1)
        abort.set()

    fetch_biggo.set_abort_check(abort.is_set)
    th = threading.Thread(target=setter)
    th.start()
    t0 = time.monotonic()
    try:
        data, reachable = fetch_biggo._api_search(
            'MODEL1', use_cooldown=False, use_pace=False)
    finally:
        fetch_biggo.set_abort_check(None)
        th.join()
    elapsed = time.monotonic() - t0
    assert reachable is False
    assert calls == [1], 'abort 應該喺 retry sleep 截斷，唔會第二次 outbound'
    assert elapsed < 2, f'retry sleep 應該被 abort 截斷，實際 {elapsed:.1f}s'


def test_abort_interrupts_cooldown_wait(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(1)
        return _Resp(_payload('MODEL1'))

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(fetch_biggo, '_COOLDOWN_UNTIL', time.time() + 30)
    monkeypatch.setattr(fetch_biggo, '_NEXT_SLOT', 0.0)
    abort = threading.Event()

    def setter():
        time.sleep(0.1)
        abort.set()

    fetch_biggo.set_abort_check(abort.is_set)
    th = threading.Thread(target=setter)
    th.start()
    t0 = time.monotonic()
    try:
        fetch_biggo._api_search('MODEL1', max_attempts=1)
    finally:
        fetch_biggo.set_abort_check(None)
        th.join()
    elapsed = time.monotonic() - t0
    assert elapsed < 2, f'abort 應該即刻截斷 cooldown，實際 {elapsed:.1f}s'
    assert calls == [], 'abort 後唔可以 outbound'


def test_abort_interrupts_pace_wait(monkeypatch):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(1)
        return _Resp(_payload('MODEL1'))

    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(fetch_biggo, '_COOLDOWN_UNTIL', 0.0)
    monkeypatch.setattr(fetch_biggo, '_NEXT_SLOT', time.time() + 30)
    abort = threading.Event()

    def setter():
        time.sleep(0.1)
        abort.set()

    fetch_biggo.set_abort_check(abort.is_set)
    th = threading.Thread(target=setter)
    th.start()
    t0 = time.monotonic()
    try:
        fetch_biggo._api_search('MODEL1', max_attempts=1)
    finally:
        fetch_biggo.set_abort_check(None)
        th.join()
    assert time.monotonic() - t0 < 2, 'abort 應該即刻截斷 pace 等待'
    assert calls == []


def test_token_fetched_once_under_threads(monkeypatch):
    auth_calls = []
    search_calls = []

    def urlopen(req, timeout=None):
        url = getattr(req, 'full_url', str(req))
        if url.startswith(fetch_biggo.AUTH_URL):
            auth_calls.append(1)
            time.sleep(0.15)  # 放大 race window
            return _Resp(json.dumps({'access_token': 'tok-1'}).encode())
        search_calls.append(_model_from(req))
        return _Resp(_payload(_model_from(req)))

    monkeypatch.setenv('BIGGO_CLIENT_ID', 'cid')
    monkeypatch.setenv('BIGGO_CLIENT_SECRET', 'csec')
    monkeypatch.setattr(fetch_biggo.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(fetch_biggo, '_TOKEN', {'value': None, 'expires': 0.0})
    monkeypatch.setattr(fetch_biggo, '_COOLDOWN_UNTIL', 0.0)
    monkeypatch.setattr(fetch_biggo, '_NEXT_SLOT', 0.0)
    fetch_biggo.set_request_limiter(None)
    fetch_biggo.set_abort_check(None)
    results = []

    def worker(model):
        results.append(fetch_biggo._api_search(
            model, max_attempts=1, use_cooldown=False, use_pace=False,
            sleep_on_error=False))

    threads = [threading.Thread(target=worker, args=(m,))
               for m in ('MODEL1', 'MODEL2')]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(auth_calls) == 1, f'token 只可以取一次，實際 {len(auth_calls)}'
    assert len(search_calls) == 2
    assert all(reachable for _, reachable in results)
