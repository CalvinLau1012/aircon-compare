# -*- coding: utf-8 -*-
"""Per-key reconciliation receipt is read-only and never auto-merges."""
import importlib.util
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
spec = importlib.util.spec_from_file_location(
    'plan_price_reconciliation',
    os.path.join(BASE, 'scripts', 'plan_price_reconciliation.py'))
ppr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ppr)


def _write(d, prices, status, black):
    os.makedirs(d, exist_ok=True)
    for name, obj in (('biggo_prices.json', prices), ('model_status.json', status),
                      ('model_blacklist.json', {'models': black})):
        with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f)


def test_per_key_receipt_has_every_class_and_never_merges(tmp_path):
    active, other = tmp_path / 'active', tmp_path / 'other'
    _write(active, {'A': {'price': '$1'}, 'B': {'price': '$2'}},
           {'A|X': {'misses': 1}, 'C|Y': {'misses': 2}},
           {'A|X': {'status': 'auto_discontinued'}})
    _write(other, {'A': {'price': '$1'}, 'D': {'price': '$3'}},
           {'A|X': {'misses': 2}, 'E|Z': {'misses': 1}}, {})
    before = {n: open(os.path.join(active, n), 'rb').read() for n in ppr.FILES}
    receipt = ppr.build(str(active), str(other), active_role='server')
    assert receipt['safeToAutoMerge'] is False
    pc = receipt['files']['biggo_prices.json']['counts']
    assert pc == {'same': 1, 'activeOnly': 1, 'otherOnly': 1, 'valueChanged': 0}
    assert receipt['files']['model_status.json']['counts']['valueChanged'] == 1
    assert receipt['files']['model_blacklist.json']['counts']['activeOnly'] == 1
    assert all(v['action'] in ('none', 'keep-active; never delete by other side',
                               'keep-active; require verified bundle for any change',
                               'review-only; do not auto-add; use verified bundle')
               for f in receipt['files'].values() for v in f['keys'].values())
    for n, data in before.items():
        assert open(os.path.join(active, n), 'rb').read() == data
