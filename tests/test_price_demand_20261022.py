# -*- coding: utf-8 -*-
"""Versioned new-model demand, transaction, concurrency and start policy."""
import base64
import json
import os
import subprocess
import sys
import time

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]

import batch_utils  # noqa: E402
import fetch_emsd  # noqa: E402
import price_demand  # noqa: E402
from price_demand_entry import run as entry_run  # noqa: E402

NOW = 1792600000


def _new_models(*items):
    return {'models': [{'brand': b, 'model': m, 'first_seen': '2026-10-01'}
                       for b, m in items]}


def test_canonical_aliases_and_duplicate_registrations_collapse():
    doc = price_demand.merge_new_models(price_demand.empty(NOW), _new_models(
        ('格力', 'GEH63ZE/GEH63ZC'),
        ('GREE', 'geh63ze/geh63zc'),
        ('GREE', 'GEH63ZE/GEH63ZC'),
        ('三菱重工', 'WRK07MIZH1'),
        ('MITSUBISHI HEAVY', 'WRK07MIZH1'),
    ), now=NOW)
    assert len(doc['demands']) == 2
    assert {d['key'] for d in doc['demands']} == {
        'GREE|GEH63ZEGEH63ZC', 'MITSUBISHIHEAVY|WRK07MIZH1'}
    assert len(doc['demands'][0].get('aliases', [])) >= 1


def test_clean_miss_processed_once_and_net_error_never_processed():
    doc = price_demand.merge_new_models(price_demand.empty(NOW), _new_models(
        ('格力', 'A1'), ('格力', 'B1')), now=NOW)
    sr = {'cycleId': '2026-10-10:1/7', 'stage': 1,
          'generatedAt': '2026-10-10T01:00:00Z',
          'outcomes': [
              {'model': 'A1', 'canonicalKey': 'GREE|A1',
               'outcome': 'clean_miss'},
              {'model': 'B1', 'canonicalKey': 'GREE|B1',
               'outcome': 'net_error'}]}
    doc2 = price_demand.record_outcomes(doc, sr, {'cycleId': sr['cycleId'], 'stage': 1},
                                        now=NOW)
    by = {d['key']: d for d in doc2['demands']}
    assert by['GREE|A1']['status'] == 'processed'
    assert len(by['GREE|A1']['evidence']) == 1
    assert by['GREE|B1']['status'] == 'pending'
    doc3 = price_demand.record_outcomes(doc2, sr, {'cycleId': sr['cycleId'], 'stage': 1},
                                        now=NOW + 100)
    by3 = {d['key']: d for d in doc3['demands']}
    assert by3['GREE|A1']['claimed' if False else 'evidence'] == by['GREE|A1']['evidence']
    assert by3['GREE|A1']['processedAt'] == by['GREE|A1']['processedAt']
    # A second real stage can append evidence but never removes prior attempts.
    sr2 = dict(sr, stage=2, cycleId='2026-10-10:2/7',
               generatedAt='2026-10-11T01:00:00Z')
    doc4 = price_demand.record_outcomes(doc3, sr2, {'cycleId': sr2['cycleId'], 'stage': 2},
                                        now=NOW + 86400)
    assert len({d['key']: d for d in doc4['demands']}['GREE|A1']['evidence']) == 2


def test_corrupt_demand_fails_closed(tmp_path):
    path = tmp_path / 'price_demand.json'
    path.write_text('{broken', encoding='utf-8')
    with pytest.raises(price_demand.DemandError):
        price_demand.load(path)
    # update never resets the corrupt file.
    with pytest.raises(price_demand.DemandError):
        price_demand.update(path, lambda d: d)
    assert path.read_text(encoding='utf-8') == '{broken'


def test_concurrent_registration_keeps_both_models(tmp_path):
    path = tmp_path / 'price_demand.json'
    script = (
        'import sys, json; sys.path[:0]=[%r,%r]; '
        'import price_demand; '
        'price_demand.register_new_models(%r, {"models":[{"brand":"格力","model":sys.argv[1],"first_seen":"2026-10-01"}]}, now=1792600000)'
        % (BASE, os.path.join(BASE, 'scripts'), str(path)))
    procs = [subprocess.Popen([sys.executable, '-c', script, m])
             for m in ('CONCURRENT-A', 'CONCURRENT-B')]
    assert [p.wait(timeout=30) for p in procs] == [0, 0]
    doc = price_demand.load(path)
    keys = {d['key'] for d in doc['demands']}
    assert {'GREE|CONCURRENTA', 'GREE|CONCURRENTB'} <= keys


def _plan(tmp_path):
    return {'new_models': {'updated': '2026-10-22', 'models': [
                {'brand': '格力', 'model': 'NEW-1', 'first_seen': '2026-10-22'}]},
            'queue': {'stage': 1, 'models': ['NEW-1']},
            'added': ['NEW-1'],
            'demand': price_demand.merge_new_models(
                price_demand.empty(NOW), _new_models(('格力', 'NEW-1')), now=NOW)}


def test_emsd_transaction_includes_demand_and_rolls_back(tmp_path, monkeypatch):
    csv = tmp_path / 'emsd_空調能源標籤.csv'
    csv.write_bytes(b'OLD-CSV')
    (tmp_path / 'new_models.json').write_text('{"updated":"old","models":[]}', encoding='utf-8')
    (tmp_path / 'update_queue.json').write_text('{"stage":0,"models":[]}', encoding='utf-8')
    demand = tmp_path / 'price_demand.json'
    demand.write_text(json.dumps(price_demand.empty(NOW)), encoding='utf-8')
    old = {p.name: p.read_bytes() for p in (csv, demand,
            tmp_path / 'new_models.json', tmp_path / 'update_queue.json')}
    plan = _plan(tmp_path)
    real_replace = os.replace
    state = {'failed': False}

    def fake_replace(src, dst):
        d = str(dst)
        if not state['failed'] and d.endswith('new_models.json') and str(src).endswith('.tmp'):
            state['failed'] = True
            raise OSError('inject after csv')
        return real_replace(src, dst)

    monkeypatch.setattr(fetch_emsd, 'BASE_DIR', str(tmp_path))
    monkeypatch.setattr(fetch_emsd, 'QUEUE_PATH', str(tmp_path / 'update_queue.json'))
    monkeypatch.setattr(fetch_emsd.os, 'replace', fake_replace)
    with pytest.raises(fetch_emsd.DatasetCommitError):
        fetch_emsd.commit_dataset(['品牌', '型號'], [['格力', 'NEW-1']], str(csv), plan)
    # New demand must roll back with the other three files.
    assert demand.read_bytes() == old['price_demand.json']
    assert (tmp_path / 'new_models.json').read_bytes() == old['new_models.json']
    assert (tmp_path / 'update_queue.json').read_bytes() == old['update_queue.json']


def _repo(tmp_path, meta, demand):
    (tmp_path / 'prices_meta.json').write_text(json.dumps(meta), encoding='utf-8')
    path = tmp_path / 'price_demand.json'
    path.write_text(json.dumps(demand), encoding='utf-8')
    return str(tmp_path / 'prices_meta.json'), str(path)


def test_start_gate_new_model_month_and_official_pending_independence(tmp_path):
    meta = {'last_price_month': '2026-09', 'price_batch_idx': 7}
    demand = price_demand.merge_new_models(price_demand.empty(NOW),
                                           _new_models(('格力', 'NEW-1')), now=NOW)
    mp, dp = _repo(tmp_path, meta, demand)
    out = entry_run(repo=str(tmp_path), meta_path=mp, demand_path=dp,
                    apply=False, now='2026-10-22T16:30:00Z', env={})
    assert out['ok'] and out['action'] == 'waiting-eligible'
    # Official queue is deliberately not part of the decision.
    (tmp_path / 'update_queue.json').write_text(
        json.dumps({'stage': 2, 'models': ['X']}), encoding='utf-8')
    out = entry_run(repo=str(tmp_path), meta_path=mp, demand_path=dp,
                    apply=True, now='2026-10-22T16:30:00Z', env={})
    assert out['ok'] and out['action'] == 'started'
    meta2 = batch_utils.load_meta(mp)
    assert meta2['last_price_month'] == '2026-10'
    assert meta2['price_batch_start'] == '2026-10-23'  # HKT date, not UTC 10/22
    # Same month + pending demand never reopens.
    out = entry_run(repo=str(tmp_path), meta_path=mp, demand_path=dp,
                    apply=True, now='2026-10-23T16:30:00Z', env={})
    assert out['ok'] and out['action'] == 'month-completed'
    # Next month without any new demand stays idle.
    empty = price_demand.empty(NOW)
    (tmp_path / 'price_demand.json').write_text(json.dumps(empty), encoding='utf-8')
    out = entry_run(repo=str(tmp_path), meta_path=mp, demand_path=dp,
                    apply=True, now='2026-11-22T16:30:00Z', env={})
    assert out['ok'] and out['action'] == 'no-demand'
    # No new model in a new month must not become a full monthly refresh.
    assert batch_utils.load_meta(mp)['price_batch_start'] == '2026-10-23'


def test_mixed_flags_fail_closed_even_with_demand(tmp_path):
    meta = {'last_price_month': '2026-09', 'price_batch_idx': 7}
    demand = price_demand.merge_new_models(price_demand.empty(NOW),
                                           _new_models(('格力', 'NEW-1')), now=NOW)
    mp, dp = _repo(tmp_path, meta, demand)
    out = entry_run(repo=str(tmp_path), meta_path=mp, demand_path=dp,
                    apply=True, now='2026-10-22T16:30:00Z',
                    env={'AIRCON_PRICE_CYCLE_ENABLED': '1'})
    assert out['ok'] is False and out['reason'] == 'mode-inconsistent'
    assert 'price_batch_start' not in batch_utils.load_meta(mp)
