#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
價錢過濾共用工具（BigGo / PricesAPI 一齊用，避免兩套規則走樣）
- 冷氣關鍵字 / 配件／服務排除
- 型號 boundary-aware 身份比對（拒絕較長前後綴、配件；支援標點／空格／全形）
- 標題標準化 / 價錢轉數字 / HKD 貨幣碼
- 價錢範圍格式化

價錢源範圍：呢套規則用於 BigGo 官方 JSON API 同 `fetch_pricesapi.py` 嘅代碼路徑；
目前網站 `generate_html.best_price` 只用 BigGo → Gemini → legacy Price.com 舊快照，
checkout 冇 `pricesapi_prices.json`（亦冇 workflow 使用 `fetch_pricesapi.py`）。
Price.com.hk 抓取早前已因 Cloudflare anti-bot 放棄，舊快照係歷史後備，唔會因此重啟。
"""
import re
import unicodedata


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

# 額外服務／零件排除（P0 身份返修新增；只加明顯唔屬於冷氣主機嘅字眼，
# 唔會加入「安裝」「保養」等可能出現喺合法主機標題嘅字）
ACC_EXTRA_RE = re.compile(
    r'維修|清洗|延長保養|去水|排水|雪種|冷媒|銅管|喉管|'
    r'\bservice\b|\binstallation\b|\binstall\b|\bhose\b|\bpipe\b|\bkit\b|'
    r'\baccessor\w*\b|\bmount(?:ing)?\b',
    re.I)

# 型號字元之間允許分隔：空白、標點、符號（NFKC 後）；CJK 等會被換成 sentinel，
# 唔可以被當成 model 內部間隔。
_MODEL_SEP = r'[^\x00A-Za-z0-9]*'
_OPAQUE = '\x00'
# model 同後面／前面以呢啲連接符再駁英數 → 視為較長型號，拒絕
_MODEL_BLOCK_SEP = frozenset('-_/.')
_ASCII_ALNUM_RE = re.compile(r'[A-Z0-9]')


def _is_separator(ch):
    """空白／標點／符號才可做 model 內部間隔；CJK 等字母唔可以。"""
    if ch.isspace():
        return True
    return unicodedata.category(ch)[0] in ('P', 'S', 'Z')


def _boundary_safe(text):
    """NFKC 後：ASCII 英數保留；空白／標點／符號保留；其餘（CJK 等）轉 sentinel。"""
    chars = []
    for ch in text:
        if ch.isascii() and ch.isalnum():
            chars.append(ch)
        elif _is_separator(ch):
            chars.append(ch)
        else:
            chars.append(_OPAQUE)
    return ''.join(chars)


def _norm_model_chars(s):
    """NFKC + 只留 A-Z0-9（同 crawl_utils.norm_model 等價；本模組唔 import 佢）。"""
    text = unicodedata.normalize('NFKC', str(s or '')).upper()
    return re.sub(r'[^A-Z0-9]', '', text)


def norm_title(s):
    """標題標準化：NFKC（全形→半形）+ 只留英數、轉大寫（精確型號比對用）"""
    text = unicodedata.normalize('NFKC', str(s or '')).upper()
    return re.sub(r'[^A-Z0-9]', '', text)


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


def model_in_title(title, model):
    """Boundary-aware 型號身份比對（fail-closed）。

    - model 嘅英數字之間可以用任意非英數分隔（`RC-N1219V`／`RC N1219V`／
      `RC／N1219V`／全形 `ＲＣ－Ｎ１２１９Ｖ` 都當同一型號）；
    - 前後緊貼英數字 → 拒絕（`RC-N1219VX`、`XRC-N1219V`、`RC-N12190V`）；
    - 前後係 `-`／`_`／`/`／`.` 連接符而再接英數字 → 當較長型號，拒絕
      （`RC-N1219V-PAC` 拒絕；`RC-N1219V 1匹`／`RC-N1219V（窗口機）` 允許，
      因空格／中文括號分隔唔算型號延續）；
    - model 少於 4 個英數字 → 拒絕（同 BigGo／PricesAPI 上游 `len(nm) < 4` 一致，
      fail-closed；直接呼叫者亦唔應放寬）；
    - 逐個 candidate match 檢查前後綴守則：標題可以同時有「較長變體」同一個獨立
      合法型號（例：`RC-N1219V-PAC / RC-N1219V 窗口冷氣機`），只要任何一個 candidate
      通過守則就 True；全部 candidate 都係較長變體 → False。
    """
    return _find_model_span(title, model) is not None


def _find_model_span(title, model):
    """回傳 (start, end, text)；text 係 NFKC＋大寫後可讀文字，冇合法 candidate 回 None。"""
    nm = _norm_model_chars(model)
    if len(nm) < 4:
        return None
    text = unicodedata.normalize('NFKC', str(title or '')).upper()
    if not text:
        return None
    safe = _boundary_safe(text)
    core = _MODEL_SEP.join(re.escape(ch) for ch in nm)
    pattern = re.compile(r'(?<![A-Z0-9])' + core + r'(?![A-Z0-9])')
    for match in pattern.finditer(safe):
        start, end = match.span()
        if start > 0 and safe[start - 1] in _MODEL_BLOCK_SEP \
                and start >= 2 and _ASCII_ALNUM_RE.fullmatch(safe[start - 2]):
            continue
        if end < len(safe) and safe[end] in _MODEL_BLOCK_SEP \
                and end + 1 < len(safe) and _ASCII_ALNUM_RE.fullmatch(safe[end + 1]):
            continue
        return start, end, text
    return None


def model_excerpt(title, model, limit=200):
    """回傳包含已匹配型號嘅 bounded excerpt（NFKC／大寫正規化）；否則 None。

    - excerpt 只證明 identity 匹配嘅文字基礎，唔代表價格正確；
    - 型號本身長過 `limit`、或搵唔到合法匹配 → None（唔會聲稱有 identity 證據）。
    """
    found = _find_model_span(title, model)
    if not found:
        return None
    start, end, text = found
    if end - start > limit:
        return None
    if len(text) <= limit:
        return text
    window_start = max(0, end - limit)
    return text[window_start:window_start + limit]


def is_ac_title(title, model_norm):
    """型號 boundary-aware 匹配 + 冷氣關鍵字 + 配件／服務排除（BigGo/PricesAPI 同一規則）"""
    t = (title or '').strip()
    if not t or not AC_RE.search(t):
        return False
    if ACC_RE.search(t) or ACC_EXTRA_RE.search(t):
        return False
    return model_in_title(t, model_norm)
