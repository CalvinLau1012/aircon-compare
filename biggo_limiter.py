#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BigGo 每請求硬上限 limiter（P0；thread-safe）。

每個真正 outbound BigGo HTTP request 之前必須先 `reserve(kind)`：
  - kind = 'search'：本地 cap = 1 smoke + 2 × 本 stage 處理型號數（由 runner 計出）；
  - kind = 'token'：每 stage 最多 1 次 token 請求；
  - `provider_cap`（如有 factual provider quota，80%）：對 token+search 合計請求數再收緊；
  - 到 cap 即 sticky `reached=True` 並 raise `BigGoLimitExceeded`；
    caller 必須當成網絡錯誤（partial／fail-closed），唔可以當 clean miss。

本模組無 I/O、無網絡、無秘密。
"""
from __future__ import annotations

import threading


class BigGoLimitExceeded(RuntimeError):
    """已到 per-request hard cap；唔可以再向 BigGo 發請求。"""


class RequestLimiter:
    def __init__(self, *, search_cap, token_cap=1, provider_cap=None):
        for name, value in (('search_cap', search_cap), ('token_cap', token_cap)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'{name} 必須係非負整數（got {value!r}）')
        if provider_cap is not None and (isinstance(provider_cap, bool)
                                         or not isinstance(provider_cap, int)
                                         or provider_cap < 0):
            raise ValueError(f'provider_cap 必須係非負整數或 None（got {provider_cap!r}）')
        self._lock = threading.Lock()
        self._search_cap = search_cap
        self._token_cap = token_cap
        self._provider_cap = provider_cap
        self._search_used = 0
        self._token_used = 0
        self.reached = False

    def reserve(self, kind):
        """為一個即將發出的 request 預留額度；滿額即 raise（sticky reached）。"""
        if kind not in ('token', 'search'):
            raise ValueError(f'未知 request kind：{kind!r}')
        with self._lock:
            used = self._token_used if kind == 'token' else self._search_used
            cap = self._token_cap if kind == 'token' else self._search_cap
            total = self._search_used + self._token_used
            if used >= cap or (self._provider_cap is not None
                               and total >= self._provider_cap):
                self.reached = True
                raise BigGoLimitExceeded(
                    f'BigGo {kind} hard cap 已到（{used}/{cap}，total {total}）')
            if kind == 'token':
                self._token_used += 1
            else:
                self._search_used += 1
            return {'kind': kind, 'used': used + 1, 'cap': cap}

    def snapshot(self):
        with self._lock:
            return {
                'searchUsed': self._search_used,
                'searchCap': self._search_cap,
                'tokenUsed': self._token_used,
                'tokenCap': self._token_cap,
                'providerCap': self._provider_cap,
                'reached': self.reached,
            }
