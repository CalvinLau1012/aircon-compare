# -*- coding: utf-8 -*-
"""P0 型號身份 boundary matcher 離線回歸（BigGo／PricesAPI 共用規則）。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import price_utils  # noqa: E402
from crawl_utils import norm_model  # noqa: E402


def test_norm_title_matches_crawl_utils_norm_model_for_ascii():
    for value in ('RC-N1219V', 'rc n1219 v', 'CHK12BE'):
        assert price_utils.norm_title(value) == norm_model(value)
    # 全形標題：norm_title 用 NFKC 轉半形（crawl_utils.norm_model 冇 NFKC）
    assert price_utils.norm_title('ＲＣ－Ｎ１２１９Ｖ') == 'RCN1219V'


def test_model_in_title_exact_and_punctuation_forms():
    model = 'RC-N1219V'
    positives = [
        'RC-N1219V',
        'rc-n1219v',
        'RC N1219V',
        'RC／N1219V',
        'RC.N1219V',
        'RC-N1219-V',
        'ＲＣ－Ｎ１２１９Ｖ',
        '樂信牌 Rasonic RC-N1219V 窗口冷氣機',
        '（RC-N1219V）',
        '型號：RC-N1219V 1匹 窗口機',
        'RC-N1219V（窗口式冷氣機）',
    ]
    for title in positives:
        assert price_utils.model_in_title(title, model), title


def test_model_in_title_rejects_attached_suffix_and_prefix():
    model = 'RC-N1219V'
    negatives = [
        'RC-N1219VX',
        'RC-N1219VX 窗口冷氣機',
        'RC-N12190V',
        'XRC-N1219V',
        'RC-N1219VX-1',
        'RCN1219',
        'RC-N1219',
    ]
    for title in negatives:
        assert not price_utils.model_in_title(title, model), title


def test_model_in_title_rejects_separator_delimited_variant_suffix():
    """保守 fail-closed：`-PAC` 等分隔變體當較長型號拒絕（會有 false negative）。"""
    assert not price_utils.model_in_title('RC-N1219V-PAC', 'RC-N1219V')
    assert not price_utils.model_in_title('RC-N1219V_1', 'RC-N1219V')
    assert not price_utils.model_in_title('RC-N1219V/2020', 'RC-N1219V')
    # 空格／中文分隔唔算型號延續
    assert price_utils.model_in_title('RC-N1219V 1匹', 'RC-N1219V')
    assert price_utils.model_in_title('RC-N1219V窗口機', 'RC-N1219V')


def test_model_in_title_short_model_fail_closed():
    assert not price_utils.model_in_title('12', '12')
    assert not price_utils.model_in_title('123', '123')
    assert price_utils.model_in_title('1234 冷氣機', '1234')
    assert not price_utils.model_in_title('型號 123', '')


def test_model_in_title_checks_all_candidate_matches():
    """標題同時有較長變體同一個獨立合法型號：任何一個 candidate 通過守則就 True。"""
    assert price_utils.model_in_title('RC-N1219V-PAC / RC-N1219V 窗口冷氣機', 'RC-N1219V')
    assert price_utils.model_in_title('RC-N1219V-PAC 窗口機 · RC N1219V 1匹', 'RC-N1219V')
    assert not price_utils.model_in_title('RC-N1219V-PAC 窗口冷氣機', 'RC-N1219V')
    assert not price_utils.model_in_title(
        'RC-N1219V-PAC / RC-N1219VX 窗口冷氣機', 'RC-N1219V')


def test_model_in_title_rejects_cjk_between_model_chars():
    """內部間隔只可以是空白／標點／符號；CJK 字母唔可以當分隔。"""
    assert not price_utils.model_in_title('R冷氣C-N1219V 窗口機', 'RC-N1219V')
    assert not price_utils.model_in_title('RC窗N1219V 窗口機', 'RC-N1219V')
    assert not price_utils.model_in_title('R-C-冷氣-N-1-2-1-9-V 窗口機', 'RC-N1219V')
    # 合法標點／空格（含全形）仍然通過
    assert price_utils.model_in_title('RC－N1219V', 'RC-N1219V')
    assert price_utils.model_in_title('RC／N1219V', 'RC-N1219V')
    assert price_utils.model_in_title('RC　N1219V', 'RC-N1219V')


def test_model_excerpt_contains_model_for_long_prefix():
    title = 'X' * 300 + ' RA-10RF 窗口冷氣機'
    excerpt = price_utils.model_excerpt(title, 'RA-10RF', limit=200)
    assert excerpt is not None and len(excerpt) <= 200
    assert price_utils.model_in_title(excerpt, 'RA-10RF')
    assert price_utils.model_excerpt('RA-10RF 窗口機', 'RA-10RF') == 'RA-10RF 窗口機'
    assert price_utils.model_excerpt('無關標題', 'RA-10RF') is None


def test_model_excerpt_returns_none_when_model_longer_than_limit():
    model = 'A' + 'B' * 210
    title = model + ' 窗口冷氣機'
    assert price_utils.model_in_title(title, model)
    assert price_utils.model_excerpt(title, model, limit=200) is None


def test_is_ac_title_requires_ac_keyword():
    assert price_utils.is_ac_title('HITACHI RA-10RF Air Conditioner', 'RA10RF')
    assert price_utils.is_ac_title('RA-10RF 窗口式冷氣機', 'RA10RF')
    assert price_utils.is_ac_title('RA-10RF 變頻冷暖空調', 'RA10RF')
    assert price_utils.is_ac_title('RA-10RF air-con', 'RA10RF')
    assert not price_utils.is_ac_title('RA-10RF 相機袋', 'RA10RF')


def test_is_ac_title_rejects_accessories_and_services():
    negatives = [
        'RA-10RF 遙控器',
        'RA-10RF remote control',
        'RA-10RF 濾網',
        'RA-10RF air filter',
        'RA-10RF 安裝支架',
        'RA-10RF bracket cover',
        'RA-10RF 維修服務',
        'RA-10RF installation service',
        'RA-10RF 雪種補充',
        'RA-10RF 銅管',
        'RA-10RF 去水喉',
        'RA-10RF spare parts',
    ]
    for title in negatives:
        assert not price_utils.is_ac_title(title, 'RA10RF'), title


def test_is_ac_title_still_accepts_legitimate_warranty_wording():
    # 「原廠保養」係主機標題常見字，唔應該被當服務配件排除
    assert price_utils.is_ac_title('RA-10RF 窗口機 原廠保養', 'RA10RF')


def test_is_ac_title_uses_boundary_identity():
    assert price_utils.is_ac_title('樂信 RC-N1219V 窗口冷氣機', 'RC-N1219V')
    assert not price_utils.is_ac_title('樂信 RC-N1219VX 窗口冷氣機', 'RC-N1219V')
    assert not price_utils.is_ac_title('樂信 RC-N1219VX 冷氣遙控器', 'RC-N1219V')
