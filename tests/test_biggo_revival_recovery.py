# -*- coding: utf-8 -*-
"""Canonical revival and safe review-evidence retention regressions."""
import copy
import json
import os
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [BASE, os.path.join(BASE, 'scripts')]
import fetch_biggo as fetch
import biggo_stage_runner as runner


def _price(model='CHK18FBVX'):
    return {'price': '$4,499起', 'merchants': 1,
            'url': f'https://biggo.hk/s/?q={model}', 'updated': '2026-10-20'}


def test_canonical_revival_is_priced_with_exact_raw_mapping():
    p = _price()
    staged = fetch._completed_payload(
        idx=4, base={}, results={'CHK18FBVX': p}, got=[], clean_miss=[], net_err=[],
        revived=['CARRIER|CHK18FBVX'],
        review_evidence=[{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
                          'result': 'revived', 'price': p}],
        pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
        brand_lookup={'CHK18FBVX': 'CARRIER'})
    assert staged['outcomes'] == [{
        'model': 'CHK18FBVX', 'canonicalKey': 'CARRIER|CHK18FBVX',
        'outcome': 'priced', 'price': p}]
    assert staged['counters'] == {'got': 1, 'cleanMiss': 0, 'netErrors': 0}


@pytest.mark.parametrize('mutator', [
    lambda ev: [],
    lambda ev: [dict(ev[0], price={'price': '$1起', 'merchants': 1,
                                    'url': 'https://biggo.hk/s/?q=X', 'updated': '2026-10-20'})],
    lambda ev: [dict(ev[0], model='OTHER')],
])
def test_revival_missing_or_conflicting_evidence_is_rejected(mutator):
    p = _price()
    ev = [{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
           'result': 'revived', 'price': p}]
    with pytest.raises(ValueError):
        fetch._completed_payload(
            idx=4, base={}, results={'CHK18FBVX': p}, got=[], clean_miss=[], net_err=[],
            revived=['CARRIER|CHK18FBVX'], review_evidence=mutator(ev),
            pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
            brand_lookup={'CHK18FBVX': 'CARRIER'})


def test_revived_cannot_collide_with_a_priced_model():
    p = _price()
    with pytest.raises(ValueError):
        fetch._completed_payload(
            idx=4, base={}, results={'CHK18FBVX': p}, got=['CHK18FBVX'],
            clean_miss=[], net_err=[], revived=['CARRIER|CHK18FBVX'],
            review_evidence=[{'key': 'CARRIER|CHK18FBVX', 'model': 'CHK18FBVX',
                              'result': 'revived', 'price': p}],
            pre_tracking={}, pre_black={}, post_tracking={}, post_black={},
            brand_lookup={'CHK18FBVX': 'CARRIER'})


def test_review_evidence_retains_full_safe_fields(tmp_path):
    staged = {'status': 'completed', 'idx': 4, 'baseSnapshot': {},
              'snapshot': {'M': {'price': '$100起'}}, 'outcomes': [],
              'counters': {'got': 0, 'cleanMiss': 0, 'netErrors': 0},
              'blacklistReview': {'quotaIndex': 4, 'reviewed': []},
              'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                          'blacklistUpserts': {}, 'blacklistRemovals': []},
              'preState': {'trackingHash': 'sha256:' + 'a' * 64,
                           'blacklistHash': 'sha256:' + 'b' * 64},
              'postState': {'trackingHash': 'sha256:' + 'c' * 64,
                            'blacklistHash': 'sha256:' + 'd' * 64},
              'metaFields': {'price_batch_idx': 5},
              'BIGGO_CLIENT_SECRET': 'MUST-NOT-BE-RETAINED'}
    before = copy.deepcopy(staged)
    runner._write_review_evidence(str(tmp_path / 'status.json'), staged,
                                  stage_result={'safe': True})
    got = json.loads((tmp_path / 'biggo-review/stage-snapshot.json').read_text())
    for key in ('idx', 'baseSnapshot', 'blacklistReview', 'effects',
                'preState', 'postState', 'metaFields', 'stageResult'):
        assert got.get(key) == staged.get(key, {'safe': True})
    assert 'BIGGO_CLIENT_SECRET' not in json.dumps(got)
    assert staged == before
