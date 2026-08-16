#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
價錢過濾共用工具（BigGo / PricesAPI 一齊用，避免兩套規則走樣）
- 冷氣關鍵字 / 配件排除
- 標題標準化 / 價錢轉數字 / HKD 貨幣碼
- 價錢範圍格式化
"""
import re


# 冷氣相關關鍵字（排除 LoRa/RF 模組、相機配件等撞名產品）
# 涵蓋「窗口機 / 分體機 / 流動式 / 淨冷 / 變頻」等唔含「冷氣/空調」嘅同義表述
AC_RE = re.compile(
    r'冷氣|空調|air\s*-?\s*con(ditioner)?|窗口機|窗口式|分體機|分體式|流動機|流動式|'
    r'淨冷|制冷|冷暖|定頻|變頻|匹',
    re.I)

# 配件/服務排除（遙控器、濾網、支架、防塵罩等）
ACC_RE = re.compile(
    r'遙控|濾網|過濾|配件|說明書|支架|擋板|防塵|罩|remote|filter|parts?|cover|bracket',
    re.I)


def norm_title(s):
    """標題標準化：只留英數、轉大寫（精確型號比對用）"""
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def num_price(p):
    """價錢轉 int；非數值/非正數回 None"""
    try:
        v = int(round(float(p)))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def currency_code(v):
    """貨幣碼標準化（PricesAPI 用；只保留 A-Z）"""
    return re.sub(r'[^A-Z]', '', str(v or '')).upper()


def format_price_range(prices):
    """價錢範圍文字：$2,500-3,680 / $2,500起"""
    if not prices:
        return ''
    lo, hi = min(prices), max(prices)
    return f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起'


def is_ac_title(title, model_norm):
    """型號精確匹配 + 冷氣關鍵字 + 配件排除（BigGo/PricesAPI 同一規則）"""
    t = (title or '').strip()
    if not t or model_norm not in norm_title(t):
        return False
    if not AC_RE.search(t):
        return False
    return not ACC_RE.search(t)
