# -*- coding: utf-8 -*-
"""BigGo 新抓取身份證據（matchedTitle／nindex）離線回歸 ＋ coordinator schema 兼容。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import biggo_coordinator as coord  # noqa: E402
import fetch_biggo  # noqa: E402
import price_utils  # noqa: E402


def _entry(price='$2,500起'):
    return {'price': price, 'merchants': 1,
            'url': 'https://biggo.hk/s/?q=RA-10RF', 'updated': '2026-09-28'}


def test_extract_price_keeps_cheapest_match_evidence():
    data = {'list': [
        {'title': 'RA-10RF 窗口式冷氣機', 'price': 3000, 'nindex': 'hk_shop2'},
        {'title': 'RA-10RF 窗口機 變頻', 'price': 2500, 'nindex': 'hk_shop1'},
        {'title': 'RA-10RF 冷氣機', 'price': 100, 'nindex': 'us_bid_aliexpress'},
        {'title': 'RA-10RF 遙控器', 'price': 50, 'nindex': 'hk_shop3'},
    ]}
    result = fetch_biggo._extract_price(data, 'RA-10RF')
    assert result['price'] == '$2,500-3,000'
    assert result['merchants'] == 2
    assert result['matchedTitle'] == 'RA-10RF 窗口機 變頻'
    assert result['nindex'] == 'hk_shop1'


def test_extract_price_rejects_attached_longer_model():
    data = {'list': [
        {'title': 'RA-10RFX 窗口式冷氣機', 'price': 50, 'nindex': 'hk_shop'},
    ]}
    assert fetch_biggo._extract_price(data, 'RA-10RF') is None


def test_extract_price_evidence_fields_are_bounded():
    long_title = 'RA-10RF ' + '窗' * 500 + '冷氣機'
    data = {'list': [{'title': long_title, 'price': 2500, 'nindex': 'hk_' + 'x' * 100}]}
    result = fetch_biggo._extract_price(data, 'RA-10RF')
    assert len(result['matchedTitle']) == 200
    assert len(result['nindex']) == 64
    assert '\x00' not in result['matchedTitle'], 'excerpt 唔可以有内部 sentinel'
    assert price_utils.model_in_title(result['matchedTitle'], 'RA-10RF')


def test_extract_price_evidence_keeps_model_for_long_prefix():
    title = '商戶 ' * 100 + ' RA-10RF 窗口冷氣機'
    assert title.index('RA-10RF') > 200
    data = {'list': [{'title': title, 'price': 2500, 'nindex': 'hk_shop'}]}
    result = fetch_biggo._extract_price(data, 'RA-10RF')
    assert len(result['matchedTitle']) <= 200
    assert price_utils.model_in_title(result['matchedTitle'], 'RA-10RF')


def test_extract_price_evidence_omitted_for_oversized_model():
    model = 'A' + 'B' * 210
    data = {'list': [{'title': model + ' 窗口冷氣機', 'price': 2500,
                      'nindex': 'hk_shop'}]}
    result = fetch_biggo._extract_price(data, model)
    assert result is not None
    assert 'matchedTitle' not in result, '型號長過 excerpt limit → 不可聲稱有 identity 證據'
    assert result['nindex'] == 'hk_shop'


def test_validate_price_snapshot_accepts_evidence_extras():
    entry = _entry()
    entry['matchedTitle'] = 'RA-10RF 窗口機'
    entry['nindex'] = 'hk_shop'
    coord.validate_price_snapshot({'RA-10RF': entry})


def test_validate_price_snapshot_accepts_legacy_without_evidence():
    coord.validate_price_snapshot({'RA-10RF': _entry()})


def test_validate_stage_result_accepts_evidence_bearing_outcomes():
    base = {'OLD-1': _entry('$1,000起')}
    priced = _entry()
    priced['matchedTitle'] = 'RA-10RF 窗口機'
    priced['nindex'] = 'hk_shop'
    new = dict(base, **{'RA-10RF': priced})
    stage_result = {
        'schemaVersion': coord.STAGE_RESULT_SCHEMA_VERSION,
        'cycleId': '2026-10-01:1/7', 'stage': 1, 'mode': 'price-batch',
        'generatedAt': '2026-10-01T00:00:00Z',
        'counters': {'got': 1, 'cleanMiss': 0, 'netErrors': 0},
        'requestAttempts': {'token': 1, 'search': 2},
        'responseStatusCounts': {'200': 2},
        'outcomes': [{'model': 'RA-10RF', 'canonicalKey': 'HITACHI|RA10RF',
                      'outcome': 'priced', 'price': priced}],
        'blacklistReview': {'quotaIndex': 0, 'reviewed': []},
        'effects': {'trackingUpserts': {}, 'trackingRemovals': [],
                    'blacklistUpserts': {}, 'blacklistRemovals': []},
        'preState': {'trackingHash': 'sha256:' + '1' * 64,
                     'blacklistHash': 'sha256:' + '2' * 64},
        'postState': {'trackingHash': 'sha256:' + '1' * 64,
                      'blacklistHash': 'sha256:' + '2' * 64},
    }
    from biggo_canonical import sha256_json
    stage_result['effectsHash'] = sha256_json(stage_result['effects'])
    coord.validate_stage_result(stage_result, base, new,
                                cycle_id='2026-10-01:1/7', stage=1, mode='price-batch')
