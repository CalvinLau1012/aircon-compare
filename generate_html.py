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
    for fname in ('official_specs.json', 'rasonic_official.json', 'pana_official.json', 'midea_official.json', 'shew_official.json', 'general_official.json', 'carrier_official.json'):
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
    """用雙源確認嘅規格覆蓋 MODELS；預設機型/淨冷/遙控"""
    for m in MODELS:
        # 核心 29 型號全部係窗口式 + 淨冷型
        m.setdefault('mount', '窗口式')
        m.setdefault('mode', '淨冷')
        # 遙控：TA-12EOG 已驗證無遙控，其餘都有（遙控已非硬性篩選條件，僅作標註）
        m.setdefault('remote', '➖' if m['model'] == 'TA-12EOG' else '✅')
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
    html = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'sane_lists'])
    return html


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


def format_status(meta_dict, version):
    """純函數：metadata dict → (hero 狀態行, 頁腳狀態行)

    治理要求（AIRCON_COMPARE_GOVERNANCE.md §7.2）：
    - Last Deploy 讀 metadata.json.deployTime（UTC）並以 HKT 顯示
    - Last Update 讀 metadata.json.datasetDate，唔用靜態舊值
    - 載入失敗顯示「暫不可用」，唔回退硬編舊值

    注意：Web 版由 index.html 內 JS 於 runtime fetch metadata.json（同呢套規則）；
    本函數供測試同未來 PDF 生成共用（PDF 同 Web 必須用同一套 metadata）。
    """
    deploy_hkt = None
    dataset_date = None
    if meta_dict:
        dt = meta_dict.get('deployTime')
        if dt:
            try:
                t = time.strptime(dt, '%Y-%m-%dT%H:%M:%SZ')
                epoch = calendar.timegm(t) + 8 * 3600  # UTC → HKT
                deploy_hkt = time.strftime('%Y-%m-%d %H:%M', time.gmtime(epoch))
            except (ValueError, OverflowError):
                deploy_hkt = None
        dataset_date = meta_dict.get('datasetDate')
        v = meta_dict.get('version') or version
    else:
        v = version
    date_text = f'📅 資料日期 {dataset_date}' if dataset_date else '📅 資料日期暫不可用'
    deploy_text = f'✅ 最後部署 {deploy_hkt} HKT' if deploy_hkt else '🔄 每日偵測新機 · 有機先更新'
    line1 = f'{date_text} · v{v} · {deploy_text}'
    line2 = ('🔄 每日 00:30 偵測新機（EMSD 官方資料庫）· 有新機先分批核實更新 · '
             + (f'資料日期 {dataset_date}' if dataset_date else '資料日期暫不可用')
             + ' · 價錢為快照僅供參考')
    return line1, line2


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
            '（新機偵測自 EMSD 官方資料庫，每日 00:30 自動偵測）</div>')


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
        if not mk or mk in core_keys or mk in seen:
            continue
        seen.add(mk)
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
    content_html = md_to_html(md_text)
    # 部署資訊由瀏覽器 runtime fetch metadata.json 顯示（治理文檔 §7.2.7）；
    # build 只寫初始骨架；載入失敗顯示「暫不可用」（JS 處理）
    date_status = '📅 資料日期暫不可用 · 部署資訊暫不可用'
    foot_status = ('🔄 每日 00:30 偵測新機（EMSD 官方資料庫）· 有新機先分批核實更新 · '
                   '資料日期暫不可用 · 價錢為快照僅供參考')
    new_hint = new_models_hint()

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

    emsd_models = load_emsd_models()
    models_json = json.dumps(MODELS, ensure_ascii=False)
    emsd_json = json.dumps(emsd_models, ensure_ascii=False, separators=(',', ':'))
    fields_json = json.dumps(COMPARE_FIELDS, ensure_ascii=False)

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
                        .replace('__VERSION__', VERSION) \
                        .replace('__MASCOT_IMG__', mascot_img) \
                        .replace('__BLUE_FANTASY_ART__', blue_fantasy_art)
    out = os.path.join(BASE, '空調對比報告.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('已生成：', out, f'（{os.path.getsize(out)/1024:.0f} KB）· 型號總數 {len(MODELS) + len(emsd_models)}')


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>香港空調對比報告</title>
<meta property="og:title" content="香港空調對比報告 · 1,854 型號 · EMSD + 官網核實">
<meta property="og:description" content="香港市場 1,854 個空調型號全面對比：窗口式 / 分體式 / 流動式，EMSD 官方能源數據 + 8 品牌官網 220 型號核實，18 項屬性互動比較器。">
<meta property="og:type" content="website">
<meta name="description" content="香港空調對比報告：1,854 型號 · EMSD 官方能源標籤全量核實 · 8 品牌官網核實 · 互動比較器">
<link rel="icon" href="__MASCOT_IMG__">
<style>
:root{
  --primary:#4a5fa8; --primary2:#647ebf; --accent:#c08a33;
  --bg:#e8ecf5; --text:#1d2539; --muted:#5b6989;
  --line:#c7ccda; --alt:#eef1fb; --warn:#c00000; --ok:#2e8e52;
  --surface:rgba(255,255,255,.78); --surface-strong:rgba(247,248,251,.88);
  --hover:#E8F1F9; --code-bg:#E8F1F9; --checked-bg:#FBF6EC;
  --blockquote-bg:rgba(238,241,251,.72);
  --best-bg:#F7ECD8; --best-fg:#8A6A2F; --on-accent:#1d2539;
}
@media (prefers-color-scheme: dark){
  :root{
    --primary:#7f96d2; --primary2:#8ba3dc; --accent:#d9a94f;
    --bg:#101624; --text:#dbe2f2; --muted:#909dbb;
    --line:#3b4257; --alt:#1d2539; --warn:#e66b6b; --ok:#5cb877;
    --surface:rgba(30,36,56,.82); --surface-strong:rgba(38,48,79,.90);
    --hover:#26334f; --code-bg:#26304d; --checked-bg:#252d40;
    --blockquote-bg:rgba(38,48,79,.72);
    --best-bg:#3a3222; --best-fg:#f0d48a; --on-accent:#101624;
  }
}
*{box-sizing:border-box; margin:0; padding:0;}
html{scroll-padding-top:64px;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif;
  color:var(--text); line-height:1.7;
  background-color:var(--bg);
  background-image:linear-gradient(180deg, rgba(232,236,245,.86), rgba(232,236,245,.82)), url("__BLUE_FANTASY_ART__");
  background-size:cover; background-position:center top; background-attachment:fixed;}
@media (prefers-color-scheme: dark){
  body{background-image:linear-gradient(180deg, rgba(16,22,36,.82), rgba(16,22,36,.88)), url("__BLUE_FANTASY_ART__");}
}
.wrap{max-width:1080px; margin:0 auto; padding:0 16px;}

/* ===== Hero 封面 ===== */
.hero{background:linear-gradient(160deg,#222b4d 0%,#3c4e92 45%,#4a5fa8 100%); color:#fff;
  text-align:center; padding:64px 20px 92px; position:relative; overflow:hidden;}
/* 吉祥物預設隱藏：只有超大屏（CSS 寬 ≥1600px）先顯示，
   確保任何重疊情況下文字都保持可讀 */
.hero .mascot{display:none;}
@media (min-width:1600px){
  .hero .mascot{display:block; position:absolute; right:4%; bottom:46px;
    max-height:300px; max-width:32%; z-index:1;
    border-radius:16px; box-shadow:0 12px 32px rgba(6,14,38,.45);
    transform:rotate(2deg); pointer-events:none; opacity:.96;
    filter:drop-shadow(0 0 18px rgba(142,165,218,.35));}
}
/* 文字層置頂：即使同吉祥物重疊，文字仍然可讀 */
.hero .wrap{position:relative; z-index:2;}
.hero::after{content:""; position:absolute; bottom:-1px; left:0; right:0; height:56px;
  background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1440 60' preserveAspectRatio='none'%3E%3Cpath fill='%23E8ECF5' d='M0,30 C240,58 480,6 720,28 C960,50 1200,10 1440,30 L1440,60 L0,60 Z'/%3E%3C/svg%3E") no-repeat bottom/100% 100%;}
.bubble{position:absolute; font-size:22px; opacity:.5; pointer-events:none;
  animation:float 6s ease-in-out infinite;}
@keyframes float{0%,100%{transform:translateY(0);} 50%{transform:translateY(-16px);}}
.hero h1{font-size:2.2em; letter-spacing:1px; margin-bottom:8px;}
.hero .sub{color:#ffffff; font-size:1.15em; margin-bottom:28px; text-shadow:0 1px 3px rgba(15,20,35,.55);}
.stats{display:flex; justify-content:center; gap:40px; flex-wrap:wrap; margin-bottom:26px;}
.stats .n{font-size:2.1em; font-weight:700; color:var(--accent); text-shadow:0 1px 3px rgba(15,20,35,.55);}
.stats .l{font-size:.85em; opacity:.95; text-shadow:0 1px 3px rgba(15,20,35,.6);}
.hero .src{font-size:.8em; opacity:.98; line-height:1.8; color:#e6ebf7; text-shadow:0 1px 3px rgba(15,20,35,.6);}
.hero .date{color:#ffffff; font-size:.95em; margin-top:10px; text-shadow:0 1px 3px rgba(15,20,35,.55);}

/* ===== 頂部導覽 ===== */
.topnav{position:sticky; top:0; z-index:100; background:rgba(34,43,77,.97);
  backdrop-filter:blur(6px); border-bottom:2px solid var(--accent);}
.topnav .wrap{display:flex; align-items:center; gap:2px; flex-wrap:wrap;
  overflow:visible; padding:0 8px;}
.topnav a{color:#DCE6F0; text-decoration:none; font-size:.84em; padding:13px 10px;
  white-space:nowrap; flex:0 0 auto; transition:color .2s; position:relative;
  border-bottom:2px solid transparent; margin-bottom:-2px;}
.topnav a:hover{color:var(--accent);}
.topnav a.active{color:var(--accent); border-bottom-color:var(--accent); font-weight:700;}
.topnav .brand{font-weight:700; color:#fff; font-size:.95em; margin-right:6px;
  white-space:nowrap; flex:0 0 auto;}
/* VS Code 官方風格 tooltip（Monaco editor hover widget 同款） */
.topnav a[data-tip]::after{
  content:attr(data-tip);
  position:absolute; top:calc(100% + 6px); left:50%; transform:translateX(-50%) translateY(2px);
  background:#252526; color:#cccccc; border:1px solid #454545;
  border-radius:3px; padding:3px 8px; font-size:12px; line-height:1.4; white-space:nowrap;
  box-shadow:0 2px 8px rgba(0,0,0,.36);
  opacity:0; visibility:hidden; pointer-events:none; z-index:200;
  transition:opacity .12s ease, transform .12s ease;
}
.topnav a[data-tip]:hover::after{
  opacity:1; visibility:visible; transform:translateX(-50%) translateY(0);
}

/* ===== 章節 ===== */
section{margin:40px 0;}
h2.sec{display:flex; align-items:center; gap:10px; font-size:1.35em; color:var(--primary);
  border-left:6px solid var(--accent); padding-left:12px; margin-bottom:16px; scroll-margin-top:70px;}
h2.sec .tag{font-size:.6em; background:var(--primary); color:#fff; padding:2px 10px;
  border-radius:20px; letter-spacing:1px;}
h3{color:var(--primary2); margin:18px 0 8px; font-size:1.1em;}
p{margin:8px 0;}

/* ===== 卡片 ===== */
.card{background:var(--surface); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border:1px solid var(--line); border-radius:12px;
  padding:18px; margin:12px 0; box-shadow:0 1px 4px rgba(10,36,71,.08);}

/* ===== 表格 ===== */
.table-scroll{overflow-x:auto; -webkit-overflow-scrolling:touch; margin:12px 0;
  border:1px solid var(--line); border-radius:10px; background:var(--surface);}
table{width:100%; border-collapse:collapse; font-size:.9em; min-width:600px;}
th{background:var(--primary); color:#fff; padding:9px 10px; text-align:center;
  font-weight:600; border-bottom:3px solid var(--accent); white-space:nowrap;}
td{padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top;}
tbody tr:nth-child(even){background:var(--alt);}
tbody tr:hover{background:var(--hover);}
blockquote{margin:12px 0; padding:10px 16px; border-left:5px solid var(--accent);
  background:var(--blockquote-bg); border-radius:0 8px 8px 0; font-size:.92em;}
blockquote p{margin:2px 0;}
code{background:var(--code-bg); color:var(--text); padding:2px 6px; border-radius:4px; font-size:.9em;}
pre{background:var(--surface); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border:1px solid var(--line); border-radius:8px; padding:12px;
  overflow-x:auto; margin:12px 0;}
ul,ol{margin:8px 0 8px 24px;}

/* ===== 比較器 ===== */
.compare{position:sticky; top:58px; z-index:90; background:var(--surface);
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border:1px solid var(--line); border-bottom:3px solid var(--accent);
  border-radius:0 0 12px 12px; box-shadow:0 4px 12px rgba(10,36,71,.12);}
.compare .head{display:flex; align-items:center; justify-content:space-between;
  padding:10px 16px; background:var(--primary); color:#fff; border-radius:0 0 0 0;}
.compare .head b{font-size:1em;}
.compare .head .sel{font-size:.8em; color:var(--accent);}
.compare-tools{display:flex; gap:8px; flex-wrap:wrap; padding:8px 12px;
  border-bottom:1px solid var(--line);}
.compare-tools button{background:var(--primary2); color:#fff; border:none;
  padding:6px 14px; border-radius:20px; font-size:.85em; cursor:pointer;}
.compare-tools button:hover{background:var(--accent); color:var(--primary);}
.compare-tools .filters{display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-left:auto;}
.compare .chint{font-size:.72em; color:var(--muted); padding:3px 12px 7px; text-align:center;}
.compare-tools select,.compare-tools input[type=search]{border:1px solid var(--line);
  border-radius:20px; padding:5px 12px; font-size:.85em; background:var(--bg); color:var(--text);
  max-width:100%;}
.model-list{display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
  gap:8px; padding:10px 12px; max-height:340px; overflow-y:auto;}
.mitem{display:flex; align-items:center; gap:10px; padding:9px 12px;
  border:1px solid var(--line); border-radius:8px; background:var(--surface);
  backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px); cursor:pointer;
  transition:border-color .15s, box-shadow .15s;}
.mitem:hover{border-color:var(--primary2); box-shadow:0 2px 8px rgba(10,36,71,.12);}
.mitem.checked{border-color:var(--accent); background:var(--checked-bg); box-shadow:0 0 0 2px rgba(197,164,104,.35);}
.mitem input{width:18px; height:18px; accent-color:var(--accent); cursor:pointer; flex:0 0 auto;}
.mitem .info{flex:1; min-width:0;}
.mitem .info .name{font-weight:600; font-size:.92em; color:var(--primary);}
.mitem .info .tag{font-size:.75em; color:var(--muted);}
.mitem .badge{font-size:.72em; background:var(--primary); color:#fff; padding:2px 8px;
  border-radius:12px; white-space:nowrap; flex:0 0 auto;}

/* 比較面板 */
.panel{display:none; position:fixed; bottom:0; left:0; right:0; z-index:200;
  background:var(--surface); backdrop-filter:blur(14px); -webkit-backdrop-filter:blur(14px);
  border-top:3px solid var(--accent);
  box-shadow:0 -6px 20px rgba(10,36,71,.25); max-height:62vh; display:none; flex-direction:column;}
.panel.open{display:flex;}
.panel .phead{display:flex; align-items:center; justify-content:space-between;
  padding:10px 16px; background:var(--primary); color:#fff;}
.panel .phead button{background:var(--accent); color:var(--primary); border:none;
  padding:4px 12px; border-radius:16px; cursor:pointer; font-size:.85em;}
.panel .pscroll{overflow:auto; padding:12px 16px;}
.panel table{min-width:420px;}
.panel td:first-child{font-weight:600; color:var(--primary2); background:var(--alt);
  white-space:nowrap; width:120px;
  position:sticky; left:0; z-index:2; box-shadow:2px 0 3px rgba(15,61,92,.08);}
.panel thead th:first-child{position:sticky; left:0; z-index:3;}
.panel .phint{text-align:center; color:var(--muted); padding:14px; font-size:.9em;}

/* ===== 格價連結 + 顯示更多 ===== */
.plink{color:var(--primary2); text-decoration:underline; font-size:.82em; white-space:nowrap;}
.plink:hover{color:var(--accent);}
.more-wrap{text-align:center; padding:10px 12px; border-top:1px solid var(--line);}
#btnMore{background:var(--primary); color:#fff; border:none; padding:8px 22px;
  border-radius:20px; cursor:pointer; font-size:.9em; box-shadow:0 2px 6px rgba(15,61,92,.2);}
#btnMore:hover{background:var(--accent); color:var(--primary);}

/* ===== 比較器完善 ===== */
.selonly{font-size:.82em; color:var(--muted); display:flex; align-items:center; gap:4px; cursor:pointer;}
.selonly input{accent-color:var(--accent);}
.compare-tools .gocompare{background:var(--accent); color:var(--primary); font-weight:700;
  border:none; padding:7px 18px; border-radius:20px; cursor:pointer; font-size:.9em;
  box-shadow:0 2px 8px rgba(201,162,39,.45);}
.compare-tools .gocompare:hover{background:#DDB236;}
.compare-tools .gocompare:disabled{opacity:.5; cursor:not-allowed; box-shadow:none;}
.panel td.best{background:var(--best-bg); color:var(--best-fg); font-weight:700;}
.panel .rm{background:#C0392B; color:#fff; border:none; border-radius:10px;
  padding:1px 8px; font-size:.72em; cursor:pointer; margin-top:2px;}
.panel .rm:hover{background:#922B21;}

/* ===== 返回頂部 ===== */
#backTop{position:fixed; right:16px; bottom:20px; width:44px; height:44px;
  background:var(--primary); color:#fff; border:2px solid var(--accent);
  border-radius:50%; font-size:1.2em; cursor:pointer; z-index:150;
  display:none; align-items:center; justify-content:center;
  box-shadow:0 3px 10px rgba(15,61,92,.35);}
#backTop.show{display:flex; animation:backFade .25s ease;}
@keyframes backFade{from{opacity:0; transform:translateY(8px);} to{opacity:1; transform:none;}}
#backTop:hover{background:var(--accent); color:var(--primary);}

/* ===== 頁腳 ===== */
footer{background:var(--primary); color:#BFD0DE; text-align:center;
  padding:36px 16px 30px; margin-top:50px; font-size:.88em;}
footer b{color:#fff;}
footer .line{color:var(--accent);}
footer .blk{max-width:780px; margin:18px auto 0; text-align:left;
  background:rgba(255,255,255,.06); border:1px solid rgba(255,255,255,.14);
  border-radius:8px; padding:14px 18px;}
footer .blk h3{color:var(--accent); font-size:.95em; margin-bottom:6px;}
footer .blk p{font-size:.84em; line-height:1.8; margin:0;}
footer .blk a{color:#8FD3FF; text-decoration:none;}
footer .blk a:hover{text-decoration:underline;}
footer .ai{display:inline-block; margin-top:16px; padding:6px 14px;
  border:1px dashed var(--accent); border-radius:20px; font-size:.8em; color:#E8D9A0;}

/* ===== 響應式 ===== */
@media (max-width:640px){
  .topnav a{font-size:.75em; padding:10px 6px;}
  .topnav .brand{font-size:.85em;}
}
@media (max-width:720px){
  .topnav .wrap{flex-wrap:nowrap; overflow-x:auto; -webkit-overflow-scrolling:touch;
    scrollbar-width:none;}
  .topnav .wrap::-webkit-scrollbar{display:none;}
  .topnav a[data-tip]::after{display:none;}
  .hero .mascot{display:none;}
  .hero h1{font-size:1.6em;}
  .hero .sub{font-size:1em;}
  .stats{display:grid; grid-template-columns:1fr 1fr; gap:14px 20px;}
  .stats .n{font-size:1.6em;}
  section{margin:28px 0;}
  h2.sec{font-size:1.15em;}
  .model-list{grid-template-columns:1fr;}
  .compare-tools .filters{margin-left:0; width:100%;}
  .panel table{font-size:.8em;}
  .topnav a{padding:12px 10px; font-size:.8em;}
}
@media (prefers-color-scheme: dark){
  :root{color-scheme:dark;}
  /* 深色模式 + 玻璃下，深色元件用 --primary 對比不足 → 提亮背景 */
  a, a:visited{color:var(--primary2);}
  a:hover{color:var(--accent);}
  /* 標題欄/徽章/按鈕：深色下用亮藍，唔好用太深嘅 primary */
  th, .compare .head, .panel .phead, .mitem .badge, #btnMore, #backTop, h2.sec .tag{background:#4F66AD;}
  .compare .head .sel{color:#fff;}
  .compare-tools button{background:#5A6FB8;}
  /* accent 按鈕喺深色下：文字用深色先夠對比 */
  .compare-tools button:hover, .compare-tools .gocompare, #btnMore:hover, #backTop:hover, .panel .phead button{
    color:var(--on-accent);
  }
}
@media print{
  .topnav,.compare,.panel{display:none !important;}
  body{background:#fff;}
  .card{border:none; box-shadow:none; padding:0;}
  .hero{background:var(--primary); padding:30px;}
  a{color:inherit; text-decoration:none;}
}
</style>
</head>
<body>

<!-- ===== 封面 ===== -->
<header class="hero">
  <img class="mascot" src="__MASCOT_IMG__" alt="whale-girl 吉祥物">
  <span class="bubble" style="left:7%;top:30%;">🫧</span>
  <span class="bubble" style="left:15%;top:64%;animation-delay:1.6s;">🫧</span>
  <span class="bubble" style="right:10%;bottom:22%;animation-delay:.8s;">🫧</span>
  <span class="bubble" style="right:20%;top:26%;font-size:16px;animation-delay:2.4s;">🫧</span>
  <div class="wrap">
    <h1>香港空調對比報告</h1>
    <div class="sub">窗口式 · 分體式 · 流動式全面剖析 · 互動比較器</div>
    <div class="stats">
      <div><div class="n" id="statTotal">-</div><div class="l">型號庫</div></div>
      <div><div class="n" id="statPrice">-</div><div class="l">有實價</div></div>
      <div><div class="n" id="statSize">-</div><div class="l">有尺寸</div></div>
      <div><div class="n">29</div><div class="l">精選深度對比</div></div>
    </div>
    <div class="src">資料來源：機電署 EMSD 能源標籤資料庫（1,927 型號全量核實）· 8 品牌官網核實 220 型號 · 價錢快照：BigGo 香港格價 + Gemini AI 搜 + Price.com.hk（🔍 點擊搜最新價）· LIHKG 連登討論摘錄</div>
    <div class="date" id="deployInfo">__DATE_STATUS__</div>
  </div>
</header>

<!-- ===== 導覽 ===== -->
<nav class="topnav">
  <div class="wrap">
    <span class="brand">❄️ 空調報告</span>
    <a href="#compare" data-tip="互動比較器：揀機即時對比">⚖️ 揀機比較</a>
    <a href="#table" data-tip="核心 29 型號統合總表">📊 統合總表</a>
    <a href="#energy" data-tip="能源標籤級別分析">⚡ 能源分析</a>
    <a href="#rank" data-tip="能源效益及用戶評價排名">📈 排名</a>
    <a href="#recommend" data-tip="按場景最終推薦">🏆 推薦</a>
    <a href="#price" data-tip="官方網店價 vs Price 實價">💰 價格</a>
    <a href="#official" data-tip="8 品牌官網核實 220 型號；Gree/TOSOT 零售商交叉核實">🏭 官網核實</a>
    <a href="#verify" data-tip="EMSD 官方驗證結果">✅ 官方驗證</a>
    <a href="#forum" data-tip="LIHKG 連登討論摘錄（附原帖連結）">💬 論壇</a>
    <a href="#source" data-tip="資料來源及免責聲明">📝 來源</a>
  </div>
</nav>

<main class="wrap">

<!-- ===== 比較器 ===== -->
<section id="compare">
  <h2 class="sec">⚖️ 互動比較器 <span class="tag">揀機比較</span></h2>
  <p>喺下方揀 2 個或以上型號，底部會即時出現對比表。可以喺手機 / 平板 / 電腦用。</p>

  <div class="compare">
    <div class="head">
      <b>🛒 型號選擇區</b>
      <span class="sel" id="selCount">已選 0 個（最少 2 個）</span>
    </div>
    <div class="compare-tools">
      <button class="gocompare" id="btnCompare" onclick="openCompare()">⚖️ 開始比較</button>
      <button onclick="clearAll()">🗑 清除選擇</button>
      <button onclick="selectType('變頻')">選範圍內變頻</button>
      <button onclick="selectType('定頻')">選範圍內定頻</button>
      <label class="selonly"><input type="checkbox" id="fSelOnly" onchange="resetShown();renderList()"> 只顯示已選</label>
      <div class="filters">
        <input type="search" id="q" placeholder="🔍 搜尋品牌/型號" oninput="resetShown();renderList()">
        <select id="sortBy" onchange="resetShown();renderList()">
          <option value="">預設排序</option>
          <option value="price">價格 低→高</option>
          <option value="energy">能源級別 優→劣</option>
          <option value="kwh">年耗電 低→高</option>
          <option value="cspf">CSPF 高→低</option>
        </select>
        <select id="fBrand" onchange="resetShown();renderList()">
          <option value="">全部品牌</option>
        </select>
        <select id="fMount" onchange="resetShown();renderList()">
          <option value="">全部機型</option><option>窗口式</option><option>掛牆分體式</option><option>窗口分體式</option><option>座地/移動式</option><option>多聯式</option><option>天花式</option><option>分體式</option><option>流動式</option>
        </select>
        <select id="fHp" onchange="resetShown();renderList()">
          <option value="">全部匹數</option><option>3/4匹</option><option>1匹</option><option>1.5匹</option><option>2匹</option><option>2.5匹+</option>
        </select>
        <select id="fType" onchange="resetShown();renderList()">
          <option value="">全部類型</option><option>變頻</option><option>定頻</option>
        </select>
        <select id="fEnergy" onchange="resetShown();renderList()">
          <option value="">全部能源級別</option><option>1級</option><option>2級</option><option>3級</option><option>4級</option><option>5級</option>
        </select>
        <select id="fStatus" onchange="resetShown();renderList()">
          <option value="">全部狀態</option><option>有價</option><option>官方價</option><option>無價</option><option>停售</option>
        </select>
        <select id="fPrice" onchange="resetShown();renderList()">
          <option value="">全部價位</option><option>2以下</option><option>2-3</option><option>3-4</option><option>4-5</option><option>5以上</option>
        </select>
      </div>
    </div>
    <div class="chint">提示：Gree/TOSOT 保養為零售商規格（交叉核實），其餘品牌為官網核實</div>
    __NEW_HINT__
    <div class="model-list" id="modelList"></div>
    <div class="more-wrap"><button id="btnMore" onclick="showMore()">顯示更多型號</button></div>
  </div>

  <!-- 比較面板 -->
  <div class="panel" id="panel">
    <div class="phead">
      <b id="panelTitle">📋 型號對比</b>
      <span style="display:flex;gap:6px;">
        <button onclick="copyCompare()" style="background:#fff;color:var(--primary);">📋 複製結果</button>
        <button onclick="clearAll()" style="background:#fff;color:var(--primary);">🗑 清除</button>
        <button onclick="closePanel()">✕ 關閉</button>
      </span>
    </div>
    <div class="pscroll" id="panelBody"></div>
  </div>
</section>

<!-- ===== 詳細報告 ===== -->
<div class="md-content">
__CONTENT__
</div>

</main>

<footer>
  <b>香港空調對比報告 · <span id="verInfo">v__VERSION__</span></b><br>
  能源/雪種/耗電：機電署 EMSD 官方資料庫全量核實 · 8 品牌官網核實 220 型號

  <div class="blk">
    <h3>🔓 開源項目說明</h3>
    <p>本項目為開源內容，完整原始碼及數據公開於
      <a href="https://github.com/CalvinLau1012/aircon-compare" target="_blank" rel="noopener">GitHub：CalvinLau1012/aircon-compare</a>。<br>
      歡迎 Star ⭐、Fork、提交 Issue 或 Pull Request；數據庫（EMSD CSV / JSON）可自由下載使用，轉載請註明出處。</p>
  </div>

  <div class="blk">
    <h3>🙏 資料來源鳴謝</h3>
    <p>· 機電工程署 EMSD 能源標籤資料庫（官方能源/雪種/耗電數據）<br>
      · 品牌官網及總代理：信興集團、樂信網店、Panasonic、世紀開利、GENERAL 第一電業、HITACHI、COMFEE、美的<br>
      · 價格快照：Price.com.hk 2026-08-15（點擊 🔍 轉跳 Google 搜最新價）· 豐澤 / 百老匯 / 友和 / BUILT-IN PRO · LIHKG 電器台用戶評價</p>
  </div>

  <div class="blk">
    <h3>🤖 AI 製作提示</h3>
    <p>本網頁由 <b style="color:#8FD3FF">DeepSeek AI</b> 輔助製作，配合多輪官方資料核實；所有關鍵數據均經 EMSD 官方資料庫及品牌官網交叉驗證。</p>
  </div>

  <span class="ai">🤖 Powered by DeepSeek AI</span><br>
  <span class="line">──────</span><br>
  <span id="footStatus">__FOOT_STATUS__</span><br>
  本報告僅供選購參考，不構成購買建議 · 價格及供應隨時變動，請以商戶實時報價為準
</footer>

<button id="backTop" onclick="window.scrollTo({top:0,behavior:'smooth'})" title="返回頂部">↑</button>

<script>
const MODELS = __MODELS_JSON__;
const EMSD_EXTRA = __EMSD_JSON__;
const ALL = MODELS.concat(EMSD_EXTRA);
const FIELDS = __FIELDS_JSON__;
let selected = new Set();
let shown = 60;

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
// Google 搜價：完整品牌+型號+價錢，用戶喺自己瀏覽器搜最新價
function gsearch(m){return 'https://www.google.com/search?q='+encodeURIComponent(m.brand+' '+m.model+' 價錢');}
function resetShown(){shown = 60;}

function matches(m){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const hp=document.getElementById('fHp').value;
  const ty=document.getElementById('fType').value;
  const br=document.getElementById('fBrand').value;
  const en=document.getElementById('fEnergy').value;
  const mo=document.getElementById('fMount').value;
  const st=document.getElementById('fStatus').value;
  const pr=document.getElementById('fPrice').value;
  if(hp && m.hp!==hp) return false;
  if(ty && m.type!==ty) return false;
  if(br && m.brand!==br) return false;
  if(en && m.energy!==en) return false;
  if(mo && m.mount!==mo) return false;
  if(st && m.status!==st) return false;
  if(pr){
    const p=priceMin(m);
    if(pr==='2以下' && p>=2000) return false;
    if(pr==='2-3' && (p<2000||p>=3000)) return false;
    if(pr==='3-4' && (p<3000||p>=4000)) return false;
    if(pr==='4-5' && (p<4000||p>=5000)) return false;
    if(pr==='5以上' && p<5000) return false;
  }
  if(q && !(m.brand.toLowerCase().includes(q)||m.model.toLowerCase().includes(q))) return false;
  if(document.getElementById('fSelOnly').checked && !selected.has(m.brand+'|'+m.model)) return false;
  return true;
}

function priceMin(m){
  const ms=String(m.price).match(/\$([\d,]+)/g)||['$999999'];
  return parseInt(ms[0].replace(/[$,]/g,''));
}
function energyVal(m){return parseInt(m.energy)||9;}

function sortModels(arr){
  const v=document.getElementById('sortBy').value;
  const a=[...arr];
  if(v==='price') a.sort((x,y)=>priceMin(x)-priceMin(y));
  else if(v==='energy') a.sort((x,y)=>energyVal(x)-energyVal(y));
  else if(v==='kwh') a.sort((x,y)=>(parseInt(x.kwh)||9999)-(parseInt(y.kwh)||9999));
  else if(v==='cspf') a.sort((x,y)=>(parseFloat(y.cspf)||0)-(parseFloat(x.cspf)||0));
  // 已選型號永遠排前
  if(selected.size){
    a.sort((x,y)=>((selected.has(y.brand+'|'+y.model)?1:0)-(selected.has(x.brand+'|'+x.model)?1:0)));
  }
  return a;
}

function renderList(){
  const list=document.getElementById('modelList');
  const arr=sortModels(ALL.filter(matches));
  const vis=arr.slice(0, shown);
  list.innerHTML=vis.map((m)=>{
    const id=m.brand+'|'+m.model;
    const on=selected.has(id);
    const priceHtml = m.price
      ? `<a class="plink" href="${gsearch(m)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(m.price)} 🔍</a>`
      : `<a class="plink" href="${gsearch(m)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🔍 搜價</a>`;
    const wifiBadge = m.type==='變頻'?'🔷':'🔶';
    const energyTxt = m.energy ? esc(m.energy) : '—';
    const statusTxt = m.status && m.status!=='有價' ? ` · <b>${esc(m.status)}</b>` : '';
    const mountTxt = m.mount ? `${esc(m.mount)} ` : '';
    const modeTxt = m.mode ? `${esc(m.mode)} ` : '';
    const remoteTxt = m.remote==='✅' ? '· 有遙控 ' : (m.remote==='➖' ? '· 無遙控 ' : '');
    const extra = (m.gas ? ` · ${esc(m.gas)}` : '') + (m.cspf ? ` · CSPF ${esc(m.cspf)}` : '');
    return `<label class="mitem ${on?'checked':''}">
      <input type="checkbox" ${on?'checked':''} onchange="toggle('${esc(id)}',this)">
      <span class="info">
        <span class="name">${esc(m.brand)} ${esc(m.model)}</span><br>
        <span class="tag">${mountTxt}${modeTxt}${esc(m.hp||'?匹')} · ${esc(m.btu)} BTU · ${esc(m.type)} · ${energyTxt} ${remoteTxt}${statusTxt}${extra}· ${priceHtml}</span>
      </span>
      <span class="badge">${wifiBadge} ${energyTxt}</span>
    </label>`;
  }).join('')||'<div class="phint">冇符合嘅型號</div>';
  const btn=document.getElementById('btnMore');
  if(btn){
    if(arr.length>shown){ btn.style.display='block'; btn.textContent=`顯示更多（仲有 ${arr.length-shown} 個）`; }
    else btn.style.display='none';
  }
}

function showMore(){ shown += 100; renderList(); }

function toggle(id,el){
  if(el.checked) selected.add(id); else selected.delete(id);
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  const lab=el.closest('.mitem');
  if(lab) lab.classList.toggle('checked', el.checked);
  // 唔自動開面板，等用戶撳「開始比較」
  document.getElementById('btnCompare').disabled = selected.size < 2;
  // 若面板開住，即時更新內容
  const panel=document.getElementById('panel');
  if(panel.classList.contains('open')) buildPanel();
  if(selected.size<2) panel.classList.remove('open');
}

function updateUI(){
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  document.getElementById('btnCompare').disabled = selected.size < 2;
  const panel=document.getElementById('panel');
  if(panel.classList.contains('open')) buildPanel();
  if(selected.size<2) panel.classList.remove('open');
  renderList();
}

function openCompare(){
  if(selected.size<2){ alert('請先揀至少 2 個型號再撳「開始比較」'); return; }
  buildPanel();
  document.getElementById('panel').classList.add('open');
}

function selectedModels(){
  return ALL.filter(m=>selected.has(m.brand+'|'+m.model));
}

function buildPanel(){
  const list=selectedModels();
  const body=document.getElementById('panelBody');
  document.getElementById('panelTitle').textContent=`📋 型號對比（${list.length} 個）`;
  const rows=[['<b>屬性</b>', ...list.map(m=>`<b>${esc(m.brand)}<br>${esc(m.model)}<br><button class="rm" onclick="removeSel('${esc(m.brand+'|'+m.model).replace(/'/g,'&#39;')}')">✕ 移除</button></b>`)]];
  for(const [key,label] of FIELDS){
    // 搵最佳值（最少 2 個先高亮）
    let best=null;
    if(list.length>=2){
      if(key==='price'){
        const vals=list.map(m=>{const m1=String(m.price||'').match(/\$([\d,]+)/); return m1?parseInt(m1[1].replace(/,/g,'')):NaN;});
        const ok=vals.filter(v=>!isNaN(v));
        if(ok.length===list.length) best=Math.min(...ok);
      }else if(key==='kwh'){
        const vals=list.map(m=>parseFloat(m.kwh));
        if(vals.every(v=>!isNaN(v))) best=Math.min(...vals);
      }else if(key==='cspf'){
        const vals=list.map(m=>parseFloat(m.cspf));
        if(vals.every(v=>!isNaN(v))) best=Math.max(...vals);
      }
    }
    rows.push([`<td>${label}</td>`, ...list.map(m=>{
      const v=m[key];
      const txt=(v===null||v===undefined||v==='')?'待查':String(v);
      let cls='';
      if(best!==null && !isNaN(best)){
        if(key==='price'){
          const m1=String(m.price||'').match(/\$([\d,]+)/);
          if(m1 && parseInt(m1[1].replace(/,/g,''))===best) cls=' class="best"';
        }else{
          const pv=parseFloat(m[key]);
          if(pv===best) cls=' class="best"';
        }
      }
      const inner = key==='price' && txt==='待查'
        ? `<a href="${gsearch(m)}" target="_blank" rel="noopener">🔍 搜價</a>`
        : (key==='price'
            ? `${esc(txt)} <a href="${gsearch(m)}" target="_blank" rel="noopener">🔍</a>`
            : esc(txt));
      return `<td${cls}>${inner}</td>`;
    })]);
  }
  body.innerHTML=`<table><thead><tr>${rows[0].map(h=>`<th>${h}</th>`).join('')}</tr></thead>
    <tbody>${rows.slice(1).map(r=>`<tr>${r.join('')}</tr>`).join('')}</tbody></table>`;
}

function removeSel(id){
  selected.delete(id);
  const panel=document.getElementById('panel');
  if(selected.size<2) panel.classList.remove('open');
  updateUI();
}

function copyCompare(){
  const list=selectedModels();
  if(list.length<2){ alert('請先揀至少 2 個型號'); return; }
  const lines=['屬性\t'+list.map(m=>m.brand+' '+m.model).join('\t')];
  for(const [k,l] of FIELDS){
    lines.push(l+'\t'+list.map(m=>{const v=m[k]; return (v===null||v==='')?'待查':String(v);}).join('\t'));
  }
  navigator.clipboard.writeText(lines.join('\n')).then(()=>{
    alert('✅ 已複製對比結果！可以直接貼去 Excel / WhatsApp / 記事簿');
  }).catch(()=>{
    prompt('請手動複製以下內容：', lines.join('\n'));
  });
}

function clearAll(){selected.clear(); updateUI();}
function selectType(ty){
  const arr=sortModels(ALL.filter(matches)).filter(m=>m.type===ty);
  selected=new Set(arr.map(m=>m.brand+'|'+m.model));
  updateUI();
}
function closePanel(){document.getElementById('panel').classList.remove('open');}

// 將 markdown 表格包裝成可橫向捲動容器（手機友好）
document.addEventListener('DOMContentLoaded', ()=>{
  // 重置會殘留於 reload 嘅 checkbox（瀏覽器會保留 checked 狀態）
  const fso = document.getElementById('fSelOnly');
  if(fso) fso.checked = false;
  document.querySelectorAll('.md-content table').forEach(t=>{
    if(!t.closest('.table-scroll')){
      const w=document.createElement('div');
      w.className='table-scroll';
      t.parentNode.insertBefore(w,t);
      w.appendChild(t);
    }
  });
  // GitHub 風格 slug（同 md 目錄錨點一致）：移除非文字/emoji，空格→-
  function slugify(t){
    return t.toLowerCase()
      .replace(/[^\p{Script=Han}A-Za-z0-9\s-]/gu, '')
      .replace(/\s+/g, '-');
  }
  // 導覽用 id（頂部導覽列）
  const navMap={'統合對比總表':'table','官方驗證結果':'verify','能源標籤分析':'energy',
    '排名':'rank','最終推薦':'recommend','價格驗證':'price','品牌官網核實':'official','論壇討論精華':'forum','資料來源':'source',
    '定頻 vs 變頻':'compare-base'};
  // 為所有章節標題加 slug id（md 目錄超鏈接用）+ 導覽 id（雙錨點並存）
  const used = new Set();
  document.querySelectorAll('.md-content h1,.md-content h2,.md-content h3').forEach(h=>{
    const t=h.textContent.trim();
    let slug = slugify(t);
    let base = slug, k = 1;
    while(used.has(slug)){ slug = base + '-' + (k++); }
    used.add(slug);
    // 搵導覽 id
    let navId = null;
    for(const [kw,id] of Object.entries(navMap)){
      if(t.includes(kw)){ navId = id; }
    }
    if(navId && navId !== slug){
      // 標題用導覽 id，前面加 slug 錨點 span（md 目錄可跳）
      h.id = navId;
      const sp = document.createElement('span');
      sp.id = slug;
      sp.style.cssText = 'display:block; position:relative; top:-70px; height:0;';
      h.parentNode.insertBefore(sp, h);
    } else {
      h.id = slug;
    }
    h.style.scrollMarginTop = '70px';
  });
  // 修正 md 目錄連結：手寫 anchor 同實際 slug 有出入時自動匹配最接近 id
  function findCloseId(hashId){
    const key = hashId.replace(/-/g, '').toLowerCase();
    const allIds = [...document.querySelectorAll('[id]')].map(e=>e.id).filter(Boolean);
    const exact = allIds.find(id => id.replace(/-/g,'').toLowerCase() === key);
    if(exact) return exact;
    // 反向前綴：實際 id 以連結 key 開頭（連結文字係標題簡寫）
    const pre = allIds.filter(id => id.replace(/-/g,'').toLowerCase().startsWith(key));
    if(pre.length) return pre[0];
    return null;
  }
  document.querySelectorAll('.md-content a[href^="#"]').forEach(a=>{
    const id=(a.getAttribute('href')||'').slice(1);
    if(id && !document.getElementById(id)){
      const fixed = findCloseId(id);
      if(fixed) a.setAttribute('href', '#' + fixed);
    }
  });
  // Scrollspy：滾動時高亮導覽列當前章節
  const navLinks=[...document.querySelectorAll('.topnav a[href^="#"]')];
  let spyCur=null;
  function scrollSpy(){
    const y=window.scrollY + 110;
    let cur=null;
    for(const a of navLinks){
      const el=document.getElementById((a.getAttribute('href')||'').slice(1));
      if(!el) continue;
      const top=el.getBoundingClientRect().top + window.scrollY;
      if(top <= y) cur=a;
    }
    navLinks.forEach(a=>{
      a.style.color='';
      a.classList.toggle('active', a===cur);
    });
    // 只橫向捲動導覽列本身，唔好牽連頁面上下位置
    if(cur && cur!==spyCur){
      const navWrap=document.querySelector('.topnav .wrap');
      if(navWrap && navWrap.scrollWidth > navWrap.clientWidth){
        const wr=navWrap.getBoundingClientRect();
        const cr=cur.getBoundingClientRect();
        navWrap.scrollTo({left: navWrap.scrollLeft + (cr.left + cr.width/2) - (wr.left + wr.width/2), behavior:'smooth'});
      }
    }
    spyCur=cur;
  }
  let spyTicking=false;
  window.addEventListener('scroll',()=>{
    if(!spyTicking){ spyTicking=true; requestAnimationFrame(()=>{scrollSpy(); spyTicking=false;}); }
  },{passive:true});
  scrollSpy();
  // 自製平滑捲動（rAF 動畫，所有環境可靠）
  function smoothScrollTo(y){
    const start = window.scrollY;
    const diff = y - start;
    if(Math.abs(diff) < 2) return;
    const dur = Math.min(600, Math.max(250, Math.abs(diff) * 0.3));
    const t0 = performance.now();
    function step(t){
      const p = Math.min(1, (t - t0) / dur);
      const e = p < .5 ? 2*p*p : 1 - Math.pow(-2*p+2, 2)/2;
      window.scrollTo({top: start + diff * e, behavior:'instant'});
      if(p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
    // 保險：若 rAF 被節流（如背景分頁）而冇進展，直接跳轉
    setTimeout(()=>{
      if(Math.abs(window.scrollY - start) < 2){
        window.scrollTo({top: y, behavior:'instant'});
      }
    }, 400);
  }
  // 錨點連結平滑定位（md 內容內嘅 # 連結統一處理，確保任何環境都可跳轉）
  document.addEventListener('click', (e)=>{
    const a = e.target.closest('.md-content a[href^="#"]');
    if(!a) return;
    const id = (a.getAttribute('href')||'').slice(1);
    let el = document.getElementById(id);
    if(!el) return;
    e.preventDefault();
    // 若係零高度錨點 span，改為捲動到後面實際標題
    if(el.offsetHeight === 0 && el.nextElementSibling) el = el.nextElementSibling;
    const y = el.getBoundingClientRect().top + window.scrollY - 70;
    smoothScrollTo(Math.max(0, y));
    try{ history.replaceState(null, '', '#'+id); }catch(_){}
  });
  // 頂部導覽列：同樣用自製平滑捲動
  document.querySelectorAll('.topnav a').forEach(a=>{
    a.addEventListener('click', (e)=>{
      const href = a.getAttribute('href')||'';
      if(!href.startsWith('#')) return;
      const el = document.getElementById(href.slice(1));
      if(!el) return;
      e.preventDefault();
      const y = el.getBoundingClientRect().top + window.scrollY - 58;
      smoothScrollTo(Math.max(0, y));
    });
  });
});

// 初始化：填充品牌下拉 + 渲染 + 動態統計
(function(){
  const counts={};
  ALL.forEach(m=>counts[m.brand]=(counts[m.brand]||0)+1);
  const brands=[...new Set(ALL.map(m=>m.brand))].sort();
  const sel=document.getElementById('fBrand');
  brands.forEach(b=>{
    const o=document.createElement('option');
    o.value=b; o.textContent=`${b}（${counts[b]}）`;
    sel.appendChild(o);
  });
  document.getElementById('btnCompare').disabled = true;
  // 動態統計（永遠同數據同步）
  document.getElementById('statTotal').textContent = ALL.length.toLocaleString();
  document.getElementById('statPrice').textContent = ALL.filter(m=>m.price).length.toLocaleString();
  document.getElementById('statSize').textContent = ALL.filter(m=>m.size).length.toLocaleString();
  renderList();
})();

// 返回頂部按鈕顯示
window.addEventListener('scroll',()=>{
  const b=document.getElementById('backTop');
  if(b) b.classList.toggle('show', window.scrollY>300);
});

// ===== 部署資訊（治理文檔 §7.2.7：只讀取已部署 metadata.json） =====
// Last Deploy：metadata.json.deployTime（UTC）→ HKT 顯示
// Last Update：metadata.json.datasetDate；載入失敗顯示「暫不可用」，唔回退硬編舊值
(function loadDeployInfo(){
  const el = document.getElementById('deployInfo');
  const foot = document.getElementById('footStatus');
  const ver = document.getElementById('verInfo');
  fetch('metadata.json', {cache:'no-store'})
    .then(r => { if(!r.ok) throw new Error('http '+r.status); return r.json(); })
    .then(m => {
      if (ver && m.version) ver.textContent = 'v' + m.version;
      const ds = m.datasetDate;
      const dt = m.deployTime;
      let line = (ds ? ('📅 資料日期 ' + ds) : '📅 資料日期暫不可用');
      if (m.version) line += ' · v' + m.version;
      if (dt && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(dt)) {
        const hkt = new Date(new Date(dt).getTime() + 8*3600*1000);
        const p = hkt.toISOString().slice(0,16).replace('T',' ');
        line += ' · ✅ 最後部署 ' + p + ' HKT';
      } else {
        line += ' · 🔄 每日偵測新機 · 有機先更新';
      }
      if (el) el.textContent = line;
      if (foot) foot.textContent = '🔄 每日 00:30 偵測新機（EMSD 官方資料庫）· 有新機先分批核實更新 · '
        + (ds ? ('資料日期 ' + ds) : '資料日期暫不可用') + ' · 價錢為快照僅供參考';
    })
    .catch(() => {
      if (el) el.textContent = '📅 資料日期暫不可用 · 部署資訊暫不可用';
      if (ver) ver.textContent = 'v?';
    });
})();
</script>
</body>
</html>
"""


if __name__ == '__main__':
    build_html()
