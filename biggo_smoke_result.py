#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo structured smoke result 契約（共享、細小、無 I/O）。

每日 stage runner 同手動 bounded diagnostic（`scripts/biggo_smoke_diagnostic.py`）
共用**同一個**嚴格結果驗證器：合法 `no-price`（API 健康但冇匹配價）唔可以同
`auth`／`rate-limit`／`transport`／`invalid-response` 混淆；缺欄、型別錯、彼此
矛盾嘅 flags（例如 `ok=True` 但 `reason=rate-limit`）一律當 invalid，
consumer 唔可以靠 dict 真值通過。

呢個模組係從 `scripts/biggo_stage_runner.py` 抽出嘅原契約實作；runner 保留
`_validate_smoke_result()` 相容 adapter 指向呢度（行為不變）。
"""
from __future__ import annotations

SMOKE_REASONS_OK = ('priced',)
SMOKE_REASONS_NO_PRICE = ('no-price',)
SMOKE_REASONS_BLOCK = ('auth', 'rate-limit', 'transport', 'invalid-response',
                       'blocked-test-mode', 'blocked-limit', 'aborted', 'no-candidates',
                       'smoke-invalid-result')

REQUIRED_FIELDS = ('reason', 'ok', 'reachable', 'responseValid', 'priceAvailable',
                   'token', 'search')


def validate_smoke_result(raw):
    """嚴格 smoke 結果契約；回 ``(ok, reason, info)``。

    - bool 係明確 legacy adapter（True→priced、False→transport）；
    - 非 dict／缺欄／型別錯／未知 reason／矛盾 flags → ``(False, 'smoke-invalid-result', raw)``；
    - 合法成功：`priced`（priceAvailable=True）或 `no-price`（priceAvailable=False）；
    - block 類一律要求 `ok=False`、`priceAvailable=False`、`responseValid=False`。
    """
    if isinstance(raw, bool):  # 明確 legacy adapter
        return raw, ('priced' if raw else 'transport'), {'legacy': True}
    if not isinstance(raw, dict):
        return False, 'smoke-invalid-result', {}
    if any(k not in raw for k in REQUIRED_FIELDS):
        return False, 'smoke-invalid-result', raw
    if not isinstance(raw['ok'], bool) or not isinstance(raw['reachable'], bool) \
            or not isinstance(raw['responseValid'], bool) \
            or not isinstance(raw['priceAvailable'], bool):
        return False, 'smoke-invalid-result', raw
    for k in ('token', 'search'):
        if isinstance(raw[k], bool) or not isinstance(raw[k], int) or raw[k] < 0:
            return False, 'smoke-invalid-result', raw
    if raw['reason'] not in SMOKE_REASONS_OK + SMOKE_REASONS_NO_PRICE + SMOKE_REASONS_BLOCK:
        return False, 'smoke-invalid-result', raw
    if raw['reason'] in SMOKE_REASONS_OK + SMOKE_REASONS_NO_PRICE:
        if not (raw['ok'] and raw['reachable'] and raw['responseValid']):
            return False, 'smoke-invalid-result', raw
        if raw['reason'] == 'priced' and not raw['priceAvailable']:
            return False, 'smoke-invalid-result', raw
        if raw['reason'] == 'no-price' and raw['priceAvailable']:
            return False, 'smoke-invalid-result', raw
    else:
        if raw['ok'] or raw['priceAvailable']:
            return False, 'smoke-invalid-result', raw
        if raw['responseValid']:
            return False, 'smoke-invalid-result', raw
    return raw['ok'], raw['reason'], raw
