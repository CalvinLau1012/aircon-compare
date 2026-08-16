# -*- coding: utf-8 -*-
"""核心純函數單元測試（不碰網絡）
- norm_model / load_models（crawl_utils）
- kw_to_hp / normalize_brand / best_price（generate_html）
- _num_price / extract_prices（fetch_pricesapi）
- SPECS_OVERRIDE 重複 key 回歸測試（P0）
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawl_utils
import fetch_biggo
import fetch_prices
import fetch_pricesapi
import generate_html
import price_utils

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- crawl_utils ----------

def test_norm_model():
    assert crawl_utils.norm_model('RA-10RF') == 'RA10RF'
    assert crawl_utils.norm_model('cw-hu90aa') == 'CWHU90AA'
    assert crawl_utils.norm_model('  rc-xg9  ') == 'RCXG9'
    assert crawl_utils.norm_model('') == ''
    assert crawl_utils.norm_model('CWF-09CRFN8-AD5') == 'CWF09CRFN8AD5'


def test_load_models_dedup():
    models = crawl_utils.load_models()
    assert models, 'EMSD CSV 型號清單唔應該係空'
    keys = [crawl_utils.norm_model(m) for m in models]
    assert len(keys) == len(set(keys)), 'load_models 去重失效'


def test_load_save_json_roundtrip(tmp_path):
    p = tmp_path / 'x.json'
    crawl_utils.save_json(p, {'a': '中文'})
    assert crawl_utils.load_json(p) == {'a': '中文'}
    assert crawl_utils.load_json(tmp_path / 'missing.json', {'fallback': 1}) == {'fallback': 1}


def test_html_to_text():
    html = '<html><style>.x{}</style><script>var x;</script><p>Hello <b>World</b></p></html>'
    assert crawl_utils.html_to_text(html) == 'Hello World'


def test_batch_helpers():
    models = [f'M{i}' for i in range(40)]
    meta = {'price_batch_start': '2026-08-16', 'price_batch_idx': 1}
    todo, idx, total = fetch_prices.get_batch_todo(models, meta, days=7, limit=29)
    assert idx == 1 and total == 29
    assert todo == models[5:10]
    meta2 = {'price_batch_start': '2026-08-16', 'price_batch_idx': 6}
    assert fetch_prices.advance_batch(meta2, days=7, today='2026-08-16') is True
    assert 'price_batch_start' not in meta2 and meta2['last_full'] == '2026-08-16'


# ---------- generate_html ----------

def test_kw_to_hp():
    assert generate_html.kw_to_hp(2.29) == '3/4匹'
    assert generate_html.kw_to_hp(2.3) == '1匹'
    assert generate_html.kw_to_hp(2.7) == '1匹'
    assert generate_html.kw_to_hp(3.5) == '1.5匹'
    assert generate_html.kw_to_hp(5.3) == '2匹'
    assert generate_html.kw_to_hp(6.5) == '2.5匹+'
    assert generate_html.kw_to_hp('bad') == ''


def test_normalize_brand():
    assert generate_html.normalize_brand('日立牌') == 'HITACHI 日立'
    assert generate_html.normalize_brand('開利') == 'Carrier 開利'
    assert generate_html.normalize_brand('樂信牌') == 'Rasonic 樂信'
    assert generate_html.normalize_brand('未知品牌') == '未知品牌'


def test_best_price_priority():
    pricesapi = {'RA-10RF': {'price': '$100'}}
    biggo = {'RA-10RF': {'price': '$200'}}
    gemini = {'RA-10RF': {'price': '$300'}}
    prices = {'RA-10RF': {'price': '$400'}}
    # BigGo 主力 > PricesAPI 核心驗收/後備 > Gemini > Price 舊快照
    assert generate_html.best_price('RA-10RF', pricesapi, biggo, gemini, prices) == '$200'
    assert generate_html.best_price('RA-10RF', pricesapi, {}, gemini, prices) == '$100'
    assert generate_html.best_price('RA-10RF', {}, {}, gemini, prices) == '$300'
    assert generate_html.best_price('RA-10RF', {}, {}, {}, prices) == '$400'
    assert generate_html.best_price('RA-10RF', {}, {}, {}, {}) is None
    # 兼容舊 3 參數簽名（BigGo, Gemini, Price）
    assert generate_html.best_price('RA-10RF', biggo, gemini, prices) == '$200'


# ---------- price_utils / fetch_pricesapi ----------

def test_price_utils_shared():
    assert price_utils.num_price('1234') == 1234
    assert price_utils.num_price('abc') is None
    assert price_utils.format_price_range([2500, 3680]) == '$2,500-3,680'
    assert price_utils.format_price_range([2500]) == '$2,500起'
    assert price_utils.is_ac_title('HITACHI RA-10RF Air Conditioner', 'RA10RF')
    assert not price_utils.is_ac_title('RA-10RF 遙控器', 'RA10RF')
    # 兩個 fetch 工具都係同一套規則
    assert fetch_biggo._num_price is price_utils.num_price
    assert fetch_pricesapi._num_price is price_utils.num_price


def test_num_price():
    assert fetch_pricesapi._num_price('1234') == 1234
    assert fetch_pricesapi._num_price(2999.9) == 3000
    assert fetch_pricesapi._num_price('abc') is None
    assert fetch_pricesapi._num_price(0) is None
    assert fetch_pricesapi._num_price(-5) is None


def test_extract_prices_filters_and_dedupes():
    """PricesAPI response 要精確型號 + 冷氣關鍵字 + 排除配件 + 只收 HKD + 商戶去重"""
    sample = {
        'data': {'products': [
            {
                'title': 'HITACHI RA-10RF 窗口式冷氣機',
                'currency': 'HKD',
                'source': 'HKTVmall',
                'offers': [
                    {'seller': 'HKTVmall', 'price': 2500, 'currency': 'HKD', 'url': 'https://example.com/1'},
                    {'seller': 'YOHO', 'price': 2790, 'currency': 'HKD', 'url': 'https://example.com/2'},
                    {'seller': 'YOHO', 'price': 2790, 'currency': 'HKD', 'url': 'https://example.com/2'},
                    {'seller': 'US Store', 'price': 100, 'currency': 'USD', 'url': 'https://example.com/3'},
                    {'seller': '', 'price': 2600, 'currency': 'HKD', 'url': 'https://example.com/4'},
                ],
            },
            {
                'title': 'RA-10RF 遙控器（配件）',
                'currency': 'HKD',
                'source': 'Parts',
                'offers': [{'seller': 'Parts', 'price': 80, 'currency': 'HKD', 'url': 'https://example.com/parts'}],
            },
            {
                'title': 'RA-10RF LoRa RF 模組',
                'currency': 'HKD',
                'source': 'Electronics',
                'offers': [{'seller': 'Electronics', 'price': 88, 'currency': 'HKD', 'url': 'https://example.com/rf'}],
            },
        ]},
    }
    out = fetch_pricesapi.extract_prices(sample, 'RA-10RF')
    assert out['price'] == '$2,500-2,790'
    assert out['merchants'] == 2
    assert out['url'] == 'https://example.com/1'
    assert out['source'] == 'PricesAPI'


def test_extract_prices_uses_candidate_when_offers_degraded():
    sample = {
        'data': {'products': [
            {'title': 'RA-10RF 冷氣機', 'currency': 'HKD', 'source': 'HKTVmall',
             'price': 2500, 'offers': []},
        ]},
    }
    out = fetch_pricesapi.extract_prices(sample, 'RA-10RF')
    assert out['price'] == '$2,500起'
    assert out['merchants'] == 1


def test_extract_prices_no_match():
    assert fetch_pricesapi.extract_prices(
        {'data': {'products': [{'title': 'RC-XG9 冷氣機', 'currency': 'HKD',
                                'source': 'Shop', 'price': 100}]}},
        'RA-10RF') is False
    assert fetch_pricesapi.extract_prices(None, 'RA-10RF') is None


def test_extract_prices_english_air_conditioner_title():
    out = fetch_pricesapi.extract_prices(
        {'data': {'products': [
            {'title': 'HITACHI RA-10RF Air Conditioner', 'currency': 'HKD',
             'source': 'HKTVmall', 'price': 2500, 'offers': []},
        ]}},
        'RA-10RF')
    assert out['price'] == '$2,500起'


# ---------- P0 回歸 ----------

def test_specs_override_source_has_no_duplicate_keys():
    """source 層驗證：SPECS_OVERRIDE 字面量唔可以有重複 key（P0 bug 源頭）"""
    src = open(os.path.join(ROOT, 'generate_html.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', '') == 'SPECS_OVERRIDE':
            keys = [k.value for k in node.value.keys]
            assert len(keys) == len(set(keys)), 'SPECS_OVERRIDE 有重複 key！'


def test_specs_override_kept_merged_data():
    """語意層驗證：之前被重複 key 覆蓋丟失嘅欄位要喺度"""
    ov = generate_html.SPECS_OVERRIDE
    for model, must_have in [
        ('W09R5A', ('size', 'weight', 'remote', 'warranty')),
        ('W12R5A', ('size', 'weight', 'remote', 'warranty')),
        ('W18R5A', ('size', 'warranty')),
        ('W24R5A', ('size', 'warranty')),
        ('GWF09P', ('size', 'remote', 'warranty')),
        ('GWF12DB', ('size', 'weight', 'remote', 'warranty')),
    ]:
        v = ov[model]
        for k in must_have:
            assert v.get(k), f'{model} 缺少欄位 {k}（P0 回歸失敗）'
