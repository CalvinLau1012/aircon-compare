# -*- coding: utf-8 -*-
"""核心純函數單元測試（不碰網絡）
- norm_model / load_models（crawl_utils）
- kw_to_hp / normalize_brand / best_price（generate_html）
- _num_price（fetch_biggo）
- SPECS_OVERRIDE 重複 key 回歸測試（P0）
"""
import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import crawl_utils
import fetch_biggo
import generate_html

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
    biggo = {'RA-10RF': {'price': '$100'}}
    gemini = {'RA-10RF': {'price': '$200'}}
    prices = {'RA-10RF': {'price': '$300'}}
    # BigGo > Gemini > Price 舊快照
    assert generate_html.best_price('RA-10RF', biggo, gemini, prices) == '$100'
    assert generate_html.best_price('RA-10RF', {}, gemini, prices) == '$200'
    assert generate_html.best_price('RA-10RF', {}, {}, prices) == '$300'
    assert generate_html.best_price('RA-10RF', {}, {}, {}) is None


# ---------- fetch_biggo ----------

def test_num_price():
    assert fetch_biggo._num_price('1234') == 1234
    assert fetch_biggo._num_price(2999.9) == 3000
    assert fetch_biggo._num_price('abc') is None
    assert fetch_biggo._num_price(0) is None
    assert fetch_biggo._num_price(-5) is None


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
