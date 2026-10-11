#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared local BigGo smoke-candidate selection（唔連網、唔靠 cached price）。

用途
----
每日 coordinator stage runner 同（下一輪）獨立人手診斷 workflow 共用一個細小、
確定性的候選選擇器：由當前本地 eligible dataset，用既有 canonical model identity、
`filter_active` 同黑名單規則揀出**一個**候選。唔需要任何 cached price，亦唔會用
`fetch_biggo.SMOKE_CANDIDATES` 固定清單。

`select_candidate()` 只做本機讀取（models／brand lookup／blacklist），唔會呼叫
coordinator 或 BigGo。回傳 `(None, info)` 代表本地 eligible set 為空：呼叫方應
明確 skip（零 provider 請求），唔可以當成 API 失敗或者自行推進任何進度。
"""
from crawl_utils import canonical_model_key, load_brand_lookup, norm_model
from model_lifecycle import filter_active, load_blacklist


def select_candidate(models=None, brand_lookup=None, blacklist=None):
    """由當前 eligible records 確定性揀一個候選；回 ``(selection, info)``。

    語義（同 r25f-ds87 診斷嘅 `select_candidate` 一致）：
    - `models` 預設用 `fetch_biggo.load_models()`（當前本地 dataset）；
    - active eligibility 用既有 `filter_active`＋canonical key；
    - 完全唔依賴 `biggo_prices.json`／任何 cached price；
    - 排序 `(canonicalKey, str(model))`，同名 canonical key 只留一個；
    - 冇 eligible 候選 → `(None, {'reason': 'no-active-eligible-model', ...})`。

    `selection` 係 `{'model', 'brand', 'canonicalKey', 'reason'}`；`reason` 只供
    審計，唔構成 API 結論。
    """
    if models is None:
        import fetch_biggo  # lazy：避免 import cycle，沿用 fetch 的 load_models
        models = fetch_biggo.load_models()
    models = list(models)
    brand = load_brand_lookup() if brand_lookup is None else brand_lookup
    black = set(load_blacklist()) if blacklist is None else set(blacklist)

    def key_of(m):
        return canonical_model_key(brand.get(norm_model(m)) or 'UNKNOWN', m)

    active, skipped = filter_active(models, key_of=key_of)
    eligible = sorted({m for m in active if key_of(m) not in black},
                      key=lambda m: (key_of(m), str(m)))
    if not eligible:
        return None, {'reason': 'no-active-eligible-model',
                      'active': len(active), 'blacklisted': len(skipped)}
    model = eligible[0]
    return ({'model': model,
             'brand': brand.get(norm_model(model)) or 'UNKNOWN',
             'canonicalKey': key_of(model),
             'reason': 'deterministic first active non-blacklisted model ordered by '
                       'canonical key; no cached-price requirement; no static list'},
            {'active': len(active), 'blacklisted': len(skipped),
             'eligible': len(eligible)})
