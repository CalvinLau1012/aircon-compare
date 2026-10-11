# -*- coding: utf-8 -*-
"""共享 `biggo_smoke_result.validate_smoke_result` 契約測試（完全離線）。

重點：
- 嚴格結構化 smoke contract：缺欄／型別錯／矛盾 flags／未知 reason 一律 invalid；
- 合法 `priced`／`no-price` 分別要求 priceAvailable True／False；
- 每日 stage runner 保留嘅 `_validate_smoke_result` 係共享模組嘅兼容 adapter
  （runtime monkeypatch 共享函式可以觀察到），唔係另一份實作。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_smoke_result as shared  # noqa: E402
import biggo_stage_runner as runner  # noqa: E402

VALID_NO_PRICE = {'reason': 'no-price', 'ok': True, 'reachable': True,
                  'responseValid': True, 'priceAvailable': False, 'token': 1, 'search': 1}
VALID_PRICED = {**VALID_NO_PRICE, 'reason': 'priced', 'priceAvailable': True}
VALID_BLOCK = {**VALID_NO_PRICE, 'reason': 'rate-limit', 'ok': False,
               'reachable': False, 'responseValid': False, 'priceAvailable': False}


def test_shared_validator_accepts_only_consistent_results():
    assert shared.validate_smoke_result(dict(VALID_NO_PRICE))[:2] == (True, 'no-price')
    assert shared.validate_smoke_result(dict(VALID_PRICED))[:2] == (True, 'priced')
    for reason in ('auth', 'rate-limit', 'transport', 'invalid-response',
                   'blocked-test-mode', 'blocked-limit', 'aborted', 'no-candidates'):
        raw = {**VALID_BLOCK, 'reason': reason}
        assert shared.validate_smoke_result(raw)[:2] == (False, reason), reason
    assert shared.validate_smoke_result(True)[:2] == (True, 'priced')
    assert shared.validate_smoke_result(False)[:2] == (False, 'transport')


def test_shared_validator_rejects_malformed_and_contradictory():
    invalid = [
        None, 'true', 1, [],
        {'ok': True},                                        # 缺欄
        {**VALID_NO_PRICE, 'ok': False},                     # no-price 但 ok False
        {**VALID_NO_PRICE, 'reachable': 'yes'},              # 型別錯
        {**VALID_NO_PRICE, 'token': True},                   # bool 冒充計數
        {**VALID_NO_PRICE, 'search': -1},                    # 負數
        {**VALID_NO_PRICE, 'reason': 'mystery'},             # 未知 reason
        {**VALID_PRICED, 'priceAvailable': False},           # priced 但冇價
        {**VALID_NO_PRICE, 'priceAvailable': True},          # no-price 但有價
        {**VALID_BLOCK, 'ok': True},                         # block 但 ok True
        {**VALID_BLOCK, 'responseValid': True},              # block 但 responseValid
        {**VALID_BLOCK, 'priceAvailable': True},             # block 但有價
    ]
    for raw in invalid:
        assert shared.validate_smoke_result(raw)[:2] == \
            (False, 'smoke-invalid-result'), raw


def test_strict_option_rejects_legacy_bool_while_daily_default_keeps_compat():
    for raw in (True, False):
        strict = shared.validate_smoke_result(raw, allow_legacy_bool=False)
        assert strict[:2] == (False, 'smoke-invalid-result'), raw
        assert strict[2] == {}
    # 每日 runner 兼容路徑不變：bool adapter 照舊。
    assert shared.validate_smoke_result(True)[:2] == (True, 'priced')
    assert shared.validate_smoke_result(False)[:2] == (False, 'transport')
    assert runner._validate_smoke_result(True)[:2] == (True, 'priced')
    assert runner._validate_smoke_result(False)[:2] == (False, 'transport')
    # Strict 模式仍然接受合法 dict 契約。
    assert shared.validate_smoke_result(dict(VALID_NO_PRICE),
                                        allow_legacy_bool=False)[:2] == (True, 'no-price')
    assert shared.validate_smoke_result(dict(VALID_PRICED),
                                        allow_legacy_bool=False)[:2] == (True, 'priced')


def test_stage_runner_adapter_delegates_to_shared_module(monkeypatch):
    calls = []

    def fake(raw):
        calls.append(raw)
        return False, 'smoke-invalid-result', raw

    monkeypatch.setattr(shared, 'validate_smoke_result', fake)
    payload = {'ok': True}
    assert runner._validate_smoke_result(payload) == (False, 'smoke-invalid-result', payload)
    assert calls == [payload], 'runner adapter 必須 runtime 呼叫共享實作'


def test_stage_runner_adapter_matches_shared_results_for_matrix():
    for raw in (True, False, None, dict(VALID_NO_PRICE), dict(VALID_PRICED),
                dict(VALID_BLOCK), {'ok': True}):
        assert runner._validate_smoke_result(raw) == shared.validate_smoke_result(raw)


def test_stage_runner_no_longer_has_duplicate_contract_body():
    src = open(os.path.join(BASE, 'scripts', 'biggo_stage_runner.py'),
               encoding='utf-8').read()
    assert 'biggo_smoke_result' in src, 'runner 要 import 共享模組'
    assert "'price-non-finite'" not in src and "'list-missing-or-not-list'" not in src, \
        'payload schema 驗證仍喺 fetch_biggo；runner 唔應該有第二份 smoke contract body'
