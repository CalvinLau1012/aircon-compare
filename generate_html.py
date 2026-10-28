#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「網頁式互動對比報告」→ 空調對比報告.html
- Self-contained（內嵌 CSS/JS，可轉發 email/WhatsApp）
- 響應式（手機 / 平板 / 桌面）
- 互動比較器（勾選型號 → 對比面板）
"""
import json
import os
import csv
import calendar
import time
import markdown
import re

BASE = os.path.dirname(os.path.abspath(__file__))

from models_data import MODELS, VERSION
import report_common
import frontend_assets
from crawl_utils import (canonical_model_key, load_registrations, ENERGY_LEVELS,
                         load_energy_distributions)

# ============================================================
# 型號資料庫（整合報告 + EMSD 官方）
# 欄位: brand, model, hp, btu, type, energy, wifi, price, kwh,
#       cspf, kw, gas, noise, size, weight, warranty, note
# ============================================================

# 2026-08-15 規格覆蓋（豐澤產品頁 + Price.com.hk og 規格，雙源交叉確認）
# 2026-08-15 追加：品牌官網/官方商舖核實（三源確認）
# size 統一 H×W×D；豐澤係 W×H×D（已轉換）；單源數據喺 note 標註
SPECS_OVERRIDE = {
    # ---- CANOPUS（豐澤）----
    'TA-09EOG': {'size': '350×451×675', 'warranty': '4/5年'},
    'TA-12EOG': {'size': '380×600×560', 'warranty': '4/5年'},
    'TA-18EOG': {'size': '428×660×680', 'warranty': '4/5年'},
    # ---- TOSOT（官方商舖確認尺寸/淨重/遙控 + 零售商交叉核實保養）----
    'W09R5A': {'size': '350×450×580', 'weight': '32kg', 'remote': '✅', 'warranty': '2年全機/5年壓縮機'},
    'W12R5A': {'size': '375×560×668', 'weight': '39kg', 'remote': '✅', 'warranty': '2年全機/5年壓縮機'},
    'W18R5A': {'size': '428×660×700', 'warranty': '2年全機/5年壓縮機'},
    'W24R5A': {'size': '428×660×770', 'warranty': '2年全機/5年壓縮機'},
    # ---- Carrier BE（豐澤）----
    'CHK09BE': {'size': '350×451×675', 'weight': '37.1kg', 'warranty': '4/5年'},
    'CHK12BE': {'size': '380×600×560', 'weight': '40.3kg', 'warranty': '4/5年'},
    'CHK18BE': {'size': '428×660×680', 'weight': '57.3kg', 'warranty': '4/5年'},
    # ---- Midea CR8C（官方商舖確認 600×560×380 闊深高、37.8kg、39個月）----
    'MW-09CR8C': {'size': '350×451×675'},
    'MW-12CR8C': {'size': '380×600×560', 'weight': '37.8kg', 'warranty': '39個月'},
    # ---- HITACHI（官網三源確認：470×345×640、38kg、3/5年）----
    'RA-10RF': {'size': '345×470×640', 'weight': '34kg', 'warranty': '3/5年'},
    # ---- Rasonic XG（官方商舖確認 32.5kg/39kg）----
    'RC-XG9': {'size': '350×450×580', 'weight': '32.5kg'},
    'RC-XG12': {'size': '375×560×668', 'weight': '39kg'},
    # ---- FUJI（豐澤確認「可遙控」）----
    'RFR18FNTN': {'size': '428×660×705', 'remote': '✅'},
    # ---- COMFEE（官網 feelcomfee 精確尺寸/液晶遙控/IoT，見下「品牌官網逐型號核實」組）----
    # ---- Carrier EAVXP（世紀開利官網確認「淨冷遙控型」）----
    'CHK09EAVXP': {'size': '350×450×675', 'remote': '✅'},
    'CHK12EAVXP': {'size': '350×450×675', 'remote': '✅'},
    'CHK18EAVX': {'size': '428×660×780', 'remote': '✅'},
    # ---- Midea CRF8B（官方商舖確認 Wi-Fi 遙控變頻淨冷）----
    'MW-09CRF8B': {'size': '350×451×675', 'remote': '✅'},
    # ---- Gree（官方商舖 + 零售商交叉核實保養）----
    'GWF09P': {'size': '350×450×640', 'remote': '✅', 'warranty': '3年全機/壓縮機永久'},
    'GWF12DB': {'size': '375×560×708', 'weight': '39kg', 'warranty': '3年全機/壓縮機永久', 'remote': '✅'},
    # ---- General（Price og）----
    'AMWB12NID': {'size': '375×560×708'},
    # ---- Panasonic（官網確認：90AA 29kg、5年壓縮機；120AA 47kg）----
    'CW-HU90AA': {'size': '346×450×640', 'weight': '29kg', 'warranty': '3/5年'},
    'CW-HU120AA': {'size': '400×600×710', 'weight': '47kg'},
    # ---- Rasonic TS18（官方商舖確認 R32 Wi-Fi 變頻淨冷無線遙控）----
    'RC-TS18UV': {'size': '428×660×780', 'remote': '✅'},
    # ==== 2026-08-15 品牌官網逐型號核實（official_specs.json）====
    # Panasonic 官網（panasonic.hk 產品頁：尺寸H×W×D/淨重）
    'CW-HU70AA': {'size': '346×450×640', 'weight': '29kg'},
    'CW-HU180AA': {'size': '428×660×800', 'weight': '60kg'},
    'CW-HU240AA': {'size': '428×660×800', 'weight': '64kg'},
    'CW-SU70AA': {'size': '346×450×640', 'weight': '29kg'},
    'CW-SU90AA': {'size': '346×450×640', 'weight': '29kg'},
    'CW-SU120AA': {'size': '346×560×655', 'weight': '39kg'},
    'CW-SU180AA': {'size': '428×660×800', 'weight': '60kg'},
    'CW-SU240AA': {'size': '428×660×800', 'weight': '64kg'},
    'CW-SUL70BA': {'size': '346×450×640', 'weight': '28kg'},
    'CW-SUL90BA': {'size': '346×450×640', 'weight': '28kg'},
    'CW-SUL120BA': {'size': '346×560×655', 'weight': '39kg'},
    'CW-SUL180BA': {'size': '428×660×800', 'weight': '60kg'},
    'CW-SUL240BA': {'size': '428×660×800', 'weight': '64kg'},
    'CW-N721JA': {'size': '346×450×590', 'weight': '30kg'},
    'CW-N921JA': {'size': '346×450×640', 'weight': '32kg'},
    'CW-N1221VA': {'size': '346×560×655', 'weight': '36kg'},
    'CW-N1821EA': {'size': '428×660×800', 'weight': '59kg'},
    # HITACHI 官網（hitachi-homeappliances.com.hk：尺寸H×W×D/淨重）
    'RA-08RF': {'size': '345×470×640', 'weight': '32kg'},
    'RA-13RF': {'size': '375×560×709', 'weight': '46kg'},
    'RAW-XH07CA': {'size': '350×450×640', 'weight': '31kg'},
    'RAW-XH07CDK': {'size': '350×450×640', 'weight': '29kg'},
    'RAW-XH10CA': {'size': '350×450×640', 'weight': '31kg'},
    'RAW-XH10CDK': {'size': '350×450×640', 'weight': '29kg'},
    'RAW-XH13CA': {'size': '375×560×708', 'weight': '39kg'},
    'RAW-XH13CDK': {'size': '375×560×708', 'weight': '39kg'},
    'RAW-XH18CA': {'size': '428×660×800', 'weight': '53kg'},
    'RAW-XH18CDK': {'size': '428×660×800', 'weight': '53kg'},
    'RAW-XH24CA': {'size': '428×660×800', 'weight': '54.5kg'},
    'RAW-XH24CDK': {'size': '428×660×800', 'weight': '54.5kg'},
    'RAW-ZH07CCK': {'size': '345×470×640', 'weight': '29kg'},
    'RAW-ZH10CCK': {'size': '345×470×640', 'weight': '29kg'},
    'RAW-ZH13CCK': {'size': '375×560×709', 'weight': '38kg'},
    # COMFEE 官網（feelcomfee.com/hk：尺寸H×W×D/淨重）
    'CWF-07CRFN8-AD5': {'size': '350×450.6×675', 'weight': '31.9kg', 'remote': '✅'},
    'CWF-09CRFN8-AD5': {'size': '350×450.6×675', 'weight': '31.9kg', 'remote': '✅'},
    'CWF-12CRFN8-AD5': {'size': '350×450.6×675', 'weight': '33.3kg', 'remote': '✅'},
    'CWF-18CRFN8-AD5': {'size': '428×660×780', 'weight': '52.3kg', 'remote': '✅'},
    'CFW-07FF-M': {'size': '350×450.6×675', 'weight': '31.3kg'},
    'CFW-09FF-M': {'size': '350×450.6×675', 'weight': '34.7kg'},
    'CFW-12FF-M': {'size': '380×600×560', 'weight': '37.5kg'},
    'CFW-18FF-M': {'size': '428×660×680', 'weight': '53.1kg'},
}


def load_official():
    """載入品牌官網核實數據（多個 JSON 逐欄位合併，互不覆蓋）"""
    out = {}
    for fname in ('official_specs.json', 'rasonic_official.json', 'pana_official.json', 'midea_official.json', 'shew_official.json', 'general_official.json', 'carrier_official.json', 'mecan_official.json'):
        p = os.path.join(BASE, fname)
        if not os.path.exists(p):
            continue
        with open(p, encoding='utf-8') as f:
            data = json.load(f)
        for k, v in data.items():
            if k in out:
                out[k].update(v)   # 保留已載入欄位（如官方價），只補充新欄位
            else:
                out[k] = v
    # 型號變體映射（官網寫法 vs EMSD 寫法）
    variants = {'MW12CM8C': 'MW-12CM8C'}
    for k, v in variants.items():
        if k in out and v not in out:
            out[v] = out[k]
    return out


OFFICIAL = load_official()


def apply_official(m):
    """將官網核實數據合併入單個型號 dict"""
    of = OFFICIAL.get(m.get('model'))
    if not of:
        return
    if of.get('size'):
        m['size'] = of['size'].replace('x', '×').replace('*', '').strip()
    if of.get('weight'):
        m['weight'] = of['weight'].replace('公斤', 'kg').strip()
    if of.get('warranty'):
        m['warranty'] = of['warranty'].strip()
    if of.get('energy') and m.get('energy') in ('待查', '', None):
        m['energy'] = of['energy']
    if of.get('gas') and not m.get('gas'):
        m['gas'] = of['gas']
    if of.get('btu') and m.get('btu') in ('待查', '', None):
        m['btu'] = of['btu']
    if of.get('price') and str(of.get('price')).startswith('HK$'):
        try:
            v = float(str(of['price'])[3:].replace(',', ''))
            m['price'] = f"${v:,.0f}"
            m['price_official'] = True
        except ValueError:
            pass
    if of.get('wifi') in (True, '✅'):
        m['wifi'] = '✅'
    if of.get('remote') in (True, '✅'):
        m['remote'] = '✅'
    elif of.get('remote') is False:
        m['remote'] = '➖'
    if of.get('heat') is True:
        m['mode'] = '冷暖'
    elif of.get('mode') in ('淨冷', '冷暖'):
        m['mode'] = of['mode']
    if of.get('mode') == '淨冷' and not of.get('heat'):
        m['mode'] = '淨冷'


def apply_specs_override():
    """按逐型號資料覆蓋 MODELS；缺失規格保持待查"""
    for m in MODELS:
        # 已知核心規格明確保存在 models_data；新增型號缺值不推定規格。
        m.setdefault('mount', '待查')
        m.setdefault('mode', '待查')
        # 官網／逐型號核實資料可補充未知欄位。
        m.setdefault('remote', '待查')
        ov = SPECS_OVERRIDE.get(m['model'])
        if ov:
            m.update(ov)
        apply_official(m)


COMPARE_FIELDS = [
    ('hp', '匹數'), ('mount', '機型'), ('btu', '製冷量 (BTU)'), ('mode', '淨冷/冷暖'),
    ('type', '壓縮機'), ('energy', '能源級別'), ('wifi', 'WiFi'), ('remote', '遙控'),
    ('price', '參考價'), ('kwh', '年耗電 (kWh)'), ('cspf', 'CSPF'),
    ('kw', '製冷量 (kW)'), ('gas', '雪種'), ('noise', '噪音'), ('size', '尺寸 (mm)'),
    ('weight', '重量'), ('warranty', '保養 (全機/壓縮機)'), ('note', '特點'),
]


def md_to_html(md_text):
    html = markdown.markdown(expand_dynamic_sections(md_text),
                             extensions=['tables', 'fenced_code', 'sane_lists'])
    return html


# md 內動態區塊標記（build 時由實際資料展開；唔可以手填數字）
ENERGY_DIST_MARKER = '<!-- AIRCON:DYNAMIC:ENERGY_DISTRIBUTION -->'


def core_energy_counts():
    """核心 29 型號嘅能源級別計數（來源：models_data.MODELS）"""
    counts = {lv: 0 for lv in ENERGY_LEVELS}
    for m in MODELS:
        lv = str(m.get('energy') or '').strip()
        counts[lv] = counts.get(lv, 0) + 1
    return counts


def energy_distribution_markdown(csv_path=None):
    """全量能源級別分佈（build-time 動態生成）：1–5 固定次序，0 都顯示。

    三欄語意分明：核心 29（本報告精選）／全量 canonical model（按 BRAND|NORM 去重）／
    EMSD registration（逐筆登記）。全部由實際快照計出，避免靜態漂移。
    `csv_path` 可指定快照來源（部署後重建用線上經 hash 驗證嘅 CSV）；預設 repo CSV。
    """
    if csv_path is None:
        reg, canon = load_energy_distributions()
    else:
        reg, canon = load_energy_distributions(csv_path)
    core = core_energy_counts()
    tpl = '| {lv} | {c:,} | {m:,} | {r:,} |'
    rows = [
        '',
        f'全量 {sum(reg.values()):,} 筆登記／{sum(canon.values()):,} 個 canonical model',
        '',
        '> 📊 **全量 EMSD 能源級別分佈（建置時由實際快照動態生成）**：'
        '核心清單為本報告精選型號；canonical model 按 `BRAND|NORM` 去重（同 `load_models`）；'
        'registration 為 EMSD 逐筆登記（同 `load_registrations`）。兩者語意唔同，唔可以互換或相加。',
        '',
        f'| 能源級別 | 核心 {len(MODELS)} 型號 | 全量 canonical model | EMSD registration |',
        '| --- | ---: | ---: | ---: |',
    ]
    for lv in ENERGY_LEVELS:
        rows.append(tpl.format(lv=lv, c=core.get(lv, 0), m=canon.get(lv, 0), r=reg.get(lv, 0)))
    other = sum(v for k, v in core.items() if k not in ENERGY_LEVELS)
    other_m = sum(v for k, v in canon.items() if k not in ENERGY_LEVELS)
    other_r = sum(v for k, v in reg.items() if k not in ENERGY_LEVELS)
    if other or other_m or other_r:
        rows.append(tpl.format(lv='其他／待查', c=other, m=other_m, r=other_r))
    rows.append(tpl.format(lv='**合計**', c=sum(core.values()),
                           m=sum(canon.values()), r=sum(reg.values())))
    rows.append('')
    return '\n'.join(rows)


def expand_dynamic_sections(md_text, csv_path=None):
    """展開 md 內動態區塊（現時：能源分佈）；生成物唔應該再有 marker。

    `csv_path` 只影響動態區塊嘅資料來源；預設行為（None）完全不變。
    """
    return md_text.replace(ENERGY_DIST_MARKER, energy_distribution_markdown(csv_path))


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def kw_to_hp(kw):
    """製冷量 kW → 匹數（約算）"""
    try:
        k = float(kw)
    except (ValueError, TypeError):
        return ''
    if k < 2.3:
        return '3/4匹'
    if k < 3.2:
        return '1匹'
    if k < 4.4:
        return '1.5匹'
    if k < 6.1:
        return '2匹'
    return '2.5匹+'


# EMSD CSV 品牌名 → 統一格式（同核心 29 型號一致，避免品牌選項分裂）
BRAND_MAP = {
    '開利': 'Carrier 開利',
    '肯特': 'CANOPUS 肯特',
    '樂信牌': 'Rasonic 樂信',
    '格力': 'Gree 格力',
    '大松': 'TOSOT 大松',
    'Panasonic': 'Panasonic 樂聲',
    '美的': 'Midea 美的',
    '日立牌': 'HITACHI 日立',
    '珍寶': 'General 珍寶',
    "Comfee'": 'COMFEE',
    '富士電機': 'FUJI 富士',
    '東芝': 'TOSHIBA 東芝',
    '三菱重工': 'MITSUBISHI HEAVY 三菱重工',
    '三菱電機': 'MITSUBISHI ELECTRIC 三菱電機',
    '三星': 'SAMSUNG 三星',
    '大金': 'DAIKIN 大金',
    '惠而浦': 'WHIRLPOOL 惠而浦',
    '伊萊克斯': 'ELECTROLUX 伊萊克斯',
    '聲寶': 'SHARP 聲寶',
    '約克': 'YORK 約克',
    '海爾': 'HAIER 海爾',
    '奧克斯': 'AUX 奧克斯',
    '麥克維爾': 'McQUAY 麥克維爾',
    'Trane': 'TRANE',
    '韓國現代': 'HYUNDAI 現代',
    'PHILIPS': 'PHILIPS 飛利浦',
    '豐澤牌': 'FORTRESS 豐澤牌',
    'LG': 'LG',
    'TCL': 'TCL',
    'White-Westinghouse': 'WHITE-WESTINGHOUSE',
}


def normalize_brand(b):
    """統一品牌名稱"""
    return BRAND_MAP.get(b.strip(), b.strip())


def load_prices():
    """載入 Price.com.hk 價格庫（prices.json：型號 → {price, pid}）"""
    p = os.path.join(BASE, 'prices.json')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_biggo():
    """載入 BigGo 香港格價快照（biggo_prices.json：型號 → {price, merchants}）"""
    p = os.path.join(BASE, 'biggo_prices.json')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_gemini():
    """載入 Gemini AI 搜索價（gemini_prices.json：型號 → {price, source}）"""
    p = os.path.join(BASE, 'gemini_prices.json')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_blacklist():
    """載入淘汰黑名單 → (canonical_keys, legacy_norm_keys)

    - canonical_keys：`BRAND|NORM` 格式（遷移後嘅正式 key）
    - legacy_norm_keys：冇 `|` 嘅舊格式 key 嘅 norm（過渡兼容，遷移報告見 docs/）
    """
    p = os.path.join(BASE, 'model_blacklist.json')
    if not os.path.exists(p):
        return set(), set()
    with open(p, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get('models', data)
    if isinstance(data, dict):
        keys = list(data.keys())
    elif isinstance(data, list):
        keys = [str(x) for x in data]
    else:
        keys = []
    canonical = set()
    legacy = set()
    for k in keys:
        k = str(k)
        if '|' in k:
            canonical.add(k)
        else:
            legacy.add(norm_model(k))
    return canonical, legacy


def assign_status(item, blacklist):
    """依治理規則標註狀態：停售（黑名單，canonical 比對）→ 官方價 → 有價 → 無價

    blacklist 係 load_blacklist() 嘅 (canonical, legacy_norm) tuple。
    """
    from crawl_utils import canonical_model_key
    mk = norm_model(item.get('model') or '')
    canonical, legacy = blacklist
    key = canonical_model_key(item.get('brand'), item.get('model')) if mk else ''
    if mk and (key in canonical or mk in legacy):
        item['status'] = '停售'
    elif item.get('price_official'):
        item['status'] = '官方價'
    elif item.get('price'):
        item['status'] = '有價'
    else:
        item['status'] = '無價'
    return item


def best_price(model, *sources):
    """價錢優先級：BigGo 實抓 > PricesAPI 核心後備 > Gemini AI 搜 > Price 舊快照
    支援兩種簽名：
      best_price(model, pricesapi, biggo, gemini, prices)   # 4 源（新版）
      best_price(model, biggo, gemini, prices)             # 3 源（舊版兼容）
    """
    if len(sources) == 4:
        pricesapi, biggo, gemini, prices = sources
    elif len(sources) == 3:
        pricesapi, biggo, gemini, prices = None, sources[0], sources[1], sources[2]
    else:
        return None
    for src in (biggo, pricesapi, gemini, prices):
        v = (src or {}).get(model) or {}
        if v.get('price'):
            return v['price']
    return None


def load_specs_emsd():
    """載入 EMSD 型號規格（specs_emsd.json：Price og 規格）"""
    p = os.path.join(BASE, 'specs_emsd.json')
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _hkt_text(value):
    """Compatibility adapter: strict UTC Z (including fractional) -> HKT text."""
    return report_common.utc_to_hkt(value)


def format_status(meta_dict, version):
    """Compatibility adapter for the canonical pure report formatter."""
    return report_common.format_status(meta_dict, version)


def script_safe_json(obj):
    """Compatibility adapter for the canonical script-safe JSON helper."""
    return report_common.script_safe_json(obj)


def load_new_models():
    """讀取新機偵測記錄（new_models.json）"""
    p = os.path.join(BASE, 'new_models.json')
    try:
        with open(p, encoding='utf-8') as f:
            rec = json.load(f)
        return rec.get('models', [])
    except Exception:
        return []


def new_models_hint():
    """生成新上市型號提示行（最近 30 日）"""
    models = load_new_models()
    if not models:
        return ''
    cutoff = time.time() - 30 * 86400
    recent = []
    for m in models:
        try:
            ts = time.mktime(time.strptime(m.get('first_seen', ''), '%Y-%m-%d'))
        except Exception:
            ts = 0
        if ts >= cutoff:
            recent.append(m)
    if not recent:
        return ''
    # 顯示最近 8 個（新發現排前）
    recent.sort(key=lambda m: m.get('first_seen', ''), reverse=True)
    items = recent[:8]
    parts = [f"{m['brand']} {m['model']}（{m.get('energy', '?')}）" for m in items]
    more = f' 等 {len(recent)} 個' if len(recent) > len(items) else ''
    return ('<div class="chint" style="border-left-color:#2EA06E">'
            f'🆕 最近新上市：{" · ".join(parts)}{more}'
            '（新機偵測自 EMSD 官方資料庫，每日 00:30 自動偵測；GitHub Actions 排程屬預定觸發；高負載可延遲或丟棄，非準時保證）</div>')



def official_pending_hint():
    """顯示正常 enrichment 待補；這不是故障，也不冒充已核實或未上市。"""
    p = os.path.join(BASE, 'official_batch_status.json')
    try:
        with open(p, encoding='utf-8') as f:
            st = json.load(f)
    except (OSError, ValueError):
        return ''
    if not isinstance(st, dict) or st.get('pendingCoverage') is not True:
        return ''
    missing = st.get('missingModels')
    if not isinstance(missing, list):
        missing = []
    script_pending = st.get('scriptCoveragePending')
    if not isinstance(script_pending, list):
        script_pending = []
    count = len(missing) or len(st.get('missingCanonicalModels') or []) or len(script_pending)
    sample = '、'.join(str(m) for m in missing[:5]) if missing else \
        ('、'.join(str(m) for m in script_pending[:5]) if script_pending else '見狀態檔')
    more = f' 等 {count} 個' if count > 5 else ''
    return ('<div class="chint" style="border-left-color:var(--primary);color:var(--text)">'
            'ℹ️ 官網規格待補：EMSD 已登記型號的部分補充規格尚未取得，'
            f'核實隊列原樣保留（{sample}{more}）；不當故障，亦不推論供應情況。</div>')

def load_emsd_models():
    """讀取 EMSD 官方 CSV，轉為比較器數據（核心 29 型號去重）"""
    csv_path = os.path.join(BASE, 'emsd_空調能源標籤.csv')
    prices = load_prices()
    biggo = load_biggo()
    gemini = load_gemini()
    specs = load_specs_emsd()
    blacklist = load_blacklist()
    rows = list(csv.reader(open(csv_path, encoding='utf-8-sig')))[1:]
    rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']
    core_keys = set(norm_model(m['model']) for m in MODELS)
    out = []
    seen = set()
    for r in rows:
        brand, model = normalize_brand(r[0]), r[1].strip()
        mk = norm_model(model)
        seen_key = canonical_model_key(brand, model)
        if not mk or mk in core_keys or seen_key in seen:
            continue
        seen.add(seen_key)
        try:
            kw = float(r[6])
        except (ValueError, TypeError):
            kw = 0.0
        btu = f"{kw*3412:,.0f}" if kw else '待查'
        pinfo = prices.get(model) or {}
        price = best_price(model, biggo, gemini, prices)
        pid = pinfo.get('pid') or None
        sinfo = specs.get(model) or {}
        size = sinfo.get('size') or ''
        func = sinfo.get('func') or ''
        wifi = '✅' if re.search(r'Wi-?Fi|智能|Smart', func, re.I) else ''
        mount = sinfo.get('mount') or ''
        if mount not in ('窗口式', '掛牆分體式', '窗口分體式', '座地/移動式', '多聯式', '天花式', '分體式', '流動式'):
            mount = ''  # 非空調類別（撞名產品）唔採用
        mode = sinfo.get('mode') or ''
        if mode not in ('淨冷', '冷暖'):
            mode = ''
        weight = sinfo.get('weight') or ''
        warranty = sinfo.get('warranty') or ''
        remote = '✅' if sinfo.get('remote') else ''  # 零售商規格有遙控才填
        item = {
            'brand': brand, 'model': model,
            'hp': kw_to_hp(kw), 'mount': mount, 'btu': btu, 'mode': mode,
            'type': '變頻' if '是' in str(r[14]) else '定頻',
            'energy': (r[4] + '級') if str(r[4]).strip().isdigit() else str(r[4]),
            'wifi': wifi, 'remote': remote, 'price': price, 'pid': pid,
            'kwh': r[5], 'cspf': r[7], 'kw': r[6], 'gas': r[8],
            'noise': '', 'size': size, 'weight': weight, 'warranty': warranty,
            'note': 'EMSD 官方登記 · Price 實價', 'ref': r[2], 'provider': r[13],
        }
        # 官網核實數據覆蓋
        apply_official(item)
        if item.get('price_official'):
            item['note'] = 'EMSD 官方登記 · 官網價'
        assign_status(item, blacklist)
        out.append(item)
    priced = sum(1 for m in out if m['price'])
    sized = sum(1 for m in out if m['size'])
    print(f'EMSD 型號 {len(out)} 個 · {priced} 個有價 · {sized} 個有尺寸')
    return out


def build_html():
    with open(os.path.join(BASE, '空調對比報告.md'), encoding='utf-8') as f:
        md_text = f.read()
    # 報告內文「當前狀態」數字：建置時由實際資料同步（治理 F-11：唔可以硬編；
    # test_dynamic_counts 驗證生成物）。只針對帶上下文嘅句式，避免改到歷史更新日誌。
    emsd_models = load_emsd_models()
    total_models = len(MODELS) + len(emsd_models)
    emsd_registrations = len(load_registrations())
    md_text = re.sub(r'全量資料庫[ \t]*[\d,]+[ \t]*型號',
                     f'全量資料庫 {total_models:,} 型號', md_text)
    md_text = re.sub(r'[\d,]+[ \t]*筆登記[ \t]*·[ \t]*[\d,]+[ \t]*型號',
                     f'{emsd_registrations:,} 筆登記 · {total_models:,} 型號', md_text)
    md_text = re.sub(r'全量[ \t]*[\d,]+[ \t]*筆登記／[\d,]+[ \t]*個 canonical model',
                     f'全量 {emsd_registrations:,} 筆登記／{total_models:,} 個 canonical model', md_text)
    md_text = re.sub(r'[\d,]+[ \t]*筆登記（[\d,]+[ \t]*個型號）',
                     f'{emsd_registrations:,} 筆登記（{total_models:,} 個型號）', md_text)
    md_text = re.sub(r'全量[ \t]*[\d,]+[ \t]*筆登記逐個核實',
                     f'全量 {emsd_registrations:,} 筆登記逐個核實', md_text)
    content_html = md_to_html(md_text)
    # 部署資訊由瀏覽器 runtime fetch metadata.json 顯示（治理文檔 §7.2.7）；
    # build 只寫初始骨架；載入失敗顯示「暫不可用」（JS 處理）
    date_status = '📅 EMSD 資料日期暫不可用 · 部署資訊暫不可用'
    foot_status = ('🔄 每日 00:30 偵測新機（EMSD 官方資料庫；GitHub Actions 排程屬預定觸發；高負載可延遲或丟棄，非準時保證）· 有新機先分批核實更新 · '
                   'EMSD 資料日期暫不可用 · 部署時間為封包時間（HKT 顯示）· 價錢為快照僅供參考')
    new_hint = new_models_hint()
    pending_hint = official_pending_hint()

    # 套用雙源確認規格 + 填核心型號 Price 產品 ID（做價格連結）
    apply_specs_override()
    prices = load_prices()
    biggo = load_biggo()
    gemini = load_gemini()
    blacklist = load_blacklist()
    for m in MODELS:
        pinfo = prices.get(m['model'])
        if isinstance(pinfo, dict) and pinfo.get('pid'):
            m['pid'] = pinfo['pid']
        else:
            m['pid'] = None
        # 價錢優先級：BigGo 實抓 > Gemini AI 搜 > Price 舊快照
        bp = best_price(m['model'], biggo, gemini, prices)
        if bp:
            m['price'] = bp
        assign_status(m, blacklist)

    models_json = script_safe_json(MODELS)
    emsd_json = script_safe_json(emsd_models)
    fields_json = script_safe_json(COMPARE_FIELDS)
    # 動態計數（治理 F-11）：total_models / emsd_registrations 已喺上面計出

    # 皮膚資源（Blue Fantasy 壁紙 + whale-girl 吉祥物；檔案唔喺就留空，唔整死生成）
    import base64 as _b64
    mascot_img = ''
    _mascot_path = os.path.join(BASE, 'whale_girl.webp')
    if os.path.exists(_mascot_path):
        with open(_mascot_path, 'rb') as _f:
            mascot_img = 'data:image/webp;base64,' + _b64.b64encode(_f.read()).decode('ascii')
    blue_fantasy_art = ''
    _skin_art_path = os.path.join(BASE, 'blue_fantasy_art.txt')
    if os.path.exists(_skin_art_path):
        with open(_skin_art_path, encoding='ascii') as _f:
            blue_fantasy_art = _f.read().strip()

    html = HTML_TEMPLATE.replace('__CONTENT__', content_html) \
                        .replace('__MODELS_JSON__', models_json) \
                        .replace('__EMSD_JSON__', emsd_json) \
                        .replace('__FIELDS_JSON__', fields_json) \
                        .replace('__DATE_STATUS__', date_status) \
                        .replace('__FOOT_STATUS__', foot_status) \
                        .replace('__NEW_HINT__', new_hint) \
                        .replace('__PENDING_HINT__', pending_hint) \
                        .replace('__CORE_COUNT__', str(len(MODELS))) \
                        .replace('__FIELD_COUNT__', str(len(COMPARE_FIELDS))) \
                        .replace('__TOTAL_MODELS__', f'{total_models:,}') \
                        .replace('__EMSD_REGISTRATIONS__', f'{emsd_registrations:,}') \
                        .replace('__MASCOT_IMG__', mascot_img) \
                        .replace('__BLUE_FANTASY_ART__', blue_fantasy_art)
    out = os.path.join(BASE, '空調對比報告.html')
    write_html_output(out, html)
    try:
        size_kb = os.path.getsize(out) / 1024
    except OSError:
        # 測試／自訂 writer 可能只捕獲 html 而唔寫檔；唔可以因此失敗。
        size_kb = len(html.encode('utf-8')) / 1024
    print('已生成：', out, f'（{size_kb:.0f} KB）· 型號總數 {len(MODELS) + len(emsd_models)}')


def write_html_output(path, html):
    """以 LF 寫出 HTML（Windows 預設 CRLF，會令本地生成結果同 CI/已入庫 index.html 唔一致）"""
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(html)


HTML_TEMPLATE = frontend_assets.load_template()


if __name__ == '__main__':
    build_html()
