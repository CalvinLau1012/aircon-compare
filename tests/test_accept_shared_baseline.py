# -*- coding: utf-8 -*-
"""Offline contract checks for the explicit accepted-shared-baseline tool."""
import importlib.util
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'accept_shared_baseline', os.path.join(BASE, 'scripts', 'accept_shared_baseline.py'))
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def test_date_analysis_separates_metadata_from_amount_and_old_dates():
    old = {'A': {'price': '$1', 'merchants': 1, 'updated': '2026-09-01'},
           'B': {'price': '$2', 'merchants': 2, 'updated': '2026-09-01'},
           'C': {'price': '$3', 'merchants': 3, 'updated': '2026-09-01'}}
    new = {'A': {'price': '$1', 'merchants': 1, 'updated': '2026-10-01'},
           'B': {'price': '$9', 'merchants': 2, 'updated': '2026-10-01'},
           'C': {'price': '$3', 'merchants': 3, 'updated': '2026-08-01'}}
    a = b._date_analysis(old, new)
    assert a['counts'] == {'onlyPriceMerchantSame': 2, 'amountOrMerchantChanged': 1,
                           'olderDate': 1, 'newerDate': 2, 'sameDate': 0}
    rows = {r['key']: r for r in a['rows']}
    assert rows['A']['class'] == 'only-price-merchant-same'
    assert rows['B']['class'] == 'amount-or-merchant-changed'
    assert rows['C']['dateRelation'] == 'older'


def test_public_only_is_named_and_archived_not_active():
    old = {'biggo_prices.json': {'A': 1, 'KEEP': 1},
           'model_status.json': {'A|X': {'misses': 1}, 'KEEP|X': {'misses': 1}},
           'model_blacklist.json': {'models': {'A|X': {'status': 'x'}}}}
    new = {'biggo_prices.json': {'KEEP': 1},
           'model_status.json': {'KEEP|X': {'misses': 1}},
           'model_blacklist.json': {'models': {}}}
    out = b._public_only(old, new)
    assert out['biggo_prices.json'][0]['key'] == 'A'
    assert out['model_status.json'][0]['key'] == 'A|X'
    assert out['model_blacklist.json'][0]['key'] == 'A|X'
    assert all('archived' in r['reason'] for rows in out.values() for r in rows)


def test_new_timestamp_rejects_date_only():
    with pytest.raises(b.BaselineError):
        b._utc('2026-10-17', 'adoptedAt')
    assert b._utc('2026-10-17T18:30:00Z', 'adoptedAt') == '2026-10-17T18:30:00Z'


def test_tampered_receipt_identity_is_rejected():
    with pytest.raises(b.BaselineError):
        b._validate_receipt({'schemaVersion': 1, 'kind': b.KIND, 'receiptId': 'bad'})
