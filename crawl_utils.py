#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
禮貌爬蟲共用工具（各 fetch_*.py / generate_html.py 共用，避免重複實現）
- 誠實 Bot UA（列明專案來源，方便網站管理員聯絡）
- 退避重試（遵從 Retry-After，唔硬碰限流）
- 隨機抖動延遲（避免突發流量）
- 型號規範化 norm_model（各腳本一字不差嘅去空格/大寫化）
- EMSD CSV 型號載入 load_models（去重）
"""
import csv
import json
import os
import random
import re
import ssl
import time
import urllib.request
import urllib.error

BOT_UA = ('Mozilla/5.0 (compatible; AirconCompareBot/1.0; '
          '+https://github.com/CalvinLau1012/aircon-compare) '
          'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36')

# 專案根目錄（所有腳本同層，__file__ 即係根目錄）
BASE = os.path.dirname(os.path.abspath(__file__))
EMSD_CSV = os.path.join(BASE, 'emsd_空調能源標籤.csv')


def norm_model(s):
    """型號規範化：去除非字母數字、轉大寫（各腳本統一比對用）"""
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


# 品牌 canonical ID 表（跨平台矯正：中文／英文／顯示名 → 統一 ID）。
# 只收錄已核實嘅別名（治理文檔 §0.4：唔可以編造映射）；
# 未知品牌用 canonical_brand() 嘅大寫化 fallback。
_CANONICAL_BRAND = {
    '開利': 'CARRIER', 'CARRIER': 'CARRIER', 'CAREER': 'CARRIER',
    '肯特': 'CANOPUS', 'CANOPUS': 'CANOPUS',
    '樂信牌': 'RASONIC', '樂信': 'RASONIC', 'RASONIC': 'RASONIC',
    '格力': 'GREE', 'GREE': 'GREE',
    '大松': 'TOSOT', 'TOSOT': 'TOSOT',
    'PANASONIC': 'PANASONIC', '樂聲': 'PANASONIC', '樂聲牌': 'PANASONIC',
    '美的': 'MIDEA', 'MIDEA': 'MIDEA',
    '日立牌': 'HITACHI', '日立': 'HITACHI', 'HITACHI': 'HITACHI',
    '珍寶': 'GENERAL', 'GENERAL': 'GENERAL',
    'COMFEE': 'COMFEE', "COMFEE'": 'COMFEE',
    '富士電機': 'FUJI', '富士': 'FUJI', 'FUJI': 'FUJI',
    '東芝': 'TOSHIBA', 'TOSHIBA': 'TOSHIBA',
    '三菱重工': 'MITSUBISHIHEAVY', 'MITSUBISHI HEAVY': 'MITSUBISHIHEAVY',
    '三菱電機': 'MITSUBISHIELECTRIC', 'MITSUBISHI ELECTRIC': 'MITSUBISHIELECTRIC',
    '三星': 'SAMSUNG', 'SAMSUNG': 'SAMSUNG',
    '大金': 'DAIKIN', 'DAIKIN': 'DAIKIN',
    '惠而浦': 'WHIRLPOOL', 'WHIRLPOOL': 'WHIRLPOOL',
    '伊萊克斯': 'ELECTROLUX', 'ELECTROLUX': 'ELECTROLUX',
    '聲寶': 'SHARP', 'SHARP': 'SHARP',
    '約克': 'YORK', 'YORK': 'YORK',
    '海爾': 'HAIER', 'HAIER': 'HAIER',
    '奧克斯': 'AUX', 'AUX': 'AUX',
    '麥克維爾': 'MCQUAY', 'MCQUAY': 'MCQUAY',
    'TRANE': 'TRANE',
    '韓國現代': 'HYUNDAI', '現代': 'HYUNDAI', 'HYUNDAI': 'HYUNDAI',
    'PHILIPS': 'PHILIPS', '飛利浦': 'PHILIPS',
    '豐澤牌': 'FORTRESS', '豐澤': 'FORTRESS', 'FORTRESS': 'FORTRESS',
    'LG': 'LG',
    'TCL': 'TCL',
    'WHITE-WESTINGHOUSE': 'WHITEWESTINGHOUSE', 'WHITEWESTINGHOUSE': 'WHITEWESTINGHOUSE',
    '西屋': 'WHITEWESTINGHOUSE',
    'FROSTAR': 'FROSTAR', '霜牌': 'FROSTAR',
    '小天鵝': 'LITTLESWAN', 'LITTLE SWAN': 'LITTLESWAN', 'LITTLESWAN': 'LITTLESWAN',
    # 顯示名（英文＋中文，清理後形式）→ 統一 ID
    'CARRIER開利': 'CARRIER', 'CANOPUS肯特': 'CANOPUS', 'RASONIC樂信': 'RASONIC',
    'GREE格力': 'GREE', 'TOSOT大松': 'TOSOT', 'PANASONIC樂聲': 'PANASONIC',
    'MIDEA美的': 'MIDEA', 'HITACHI日立': 'HITACHI', 'GENERAL珍寶': 'GENERAL',
    'FUJI富士': 'FUJI', 'TOSHIBA東芝': 'TOSHIBA', 'MITSUBISHIHEAVY三菱重工': 'MITSUBISHIHEAVY',
    'MITSUBISHIELECTRIC三菱電機': 'MITSUBISHIELECTRIC', 'SAMSUNG三星': 'SAMSUNG',
    'DAIKIN大金': 'DAIKIN', 'WHIRLPOOL惠而浦': 'WHIRLPOOL', 'ELECTROLUX伊萊克斯': 'ELECTROLUX',
    'SHARP聲寶': 'SHARP', 'YORK約克': 'YORK', 'HAIER海爾': 'HAIER',
    'AUX奧克斯': 'AUX', 'MCQUAY麥克維爾': 'MCQUAY', 'HYUNDAI現代': 'HYUNDAI',
    'PHILIPS飛利浦': 'PHILIPS', 'FORTRESS豐澤牌': 'FORTRESS',
    'WHITEWESTINGHOUSE西屋': 'WHITEWESTINGHOUSE',
}

# 官方 JSON 檔案 → 品牌預設（官方規格檔 key 只有型號，品牌由檔案本身確定）
_OFFICIAL_BRAND_DEFAULT = {
    'rasonic_official.json': 'Rasonic',
    'pana_official.json': 'Panasonic',
    'midea_official.json': 'Midea',
    'carrier_official.json': 'Carrier',
    'general_official.json': 'General',
    'shew_official.json': 'Rasonic',
}


def canonical_brand(brand):
    """品牌 canonical ID：跨平台矯正（中文／英文／顯示名 → 統一 ID）

    已核實別名用 _CANONICAL_BRAND（先直接 match，再清理後 match）；
    未知品牌 fallback 做大寫化（保留 CJK，去除其餘非字母數字），
    同一個拼寫保證同一個 key。
    """
    b = str(brand or '').strip()
    if b in _CANONICAL_BRAND:
        return _CANONICAL_BRAND[b]
    cleaned = re.sub(r'[^A-Z0-9\u4e00-\u9fff]', '', b.upper())
    return _CANONICAL_BRAND.get(cleaned) or cleaned or 'UNKNOWN'


def canonical_model_key(brand, model):
    """canonical 型號鍵：CANONICAL_BRAND|NORM_MODEL（全部黑名單／狀態／保護集共用）"""
    return f'{canonical_brand(brand)}|{norm_model(model)}'


def load_brand_lookup():
    """norm_model → 品牌原文 lookup（優先：models_data → EMSD CSV → 官方 JSON 預設）"""
    lookup = {}
    try:
        from models_data import MODELS
        for m in MODELS:
            k = norm_model(m.get('model'))
            if k:
                lookup.setdefault(k, m.get('brand'))
    except Exception:
        pass
    try:
        with open(EMSD_CSV, encoding='utf-8-sig') as f:
            for r in csv.reader(f):
                if len(r) < 15 or r[1].strip() == '型號':
                    continue
                k = norm_model(r[1])
                if k:
                    lookup.setdefault(k, r[0].strip())
    except Exception:
        pass
    for fname, brand in _OFFICIAL_BRAND_DEFAULT.items():
        p = os.path.join(BASE, fname)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding='utf-8') as f:
                data = json.load(f)
            for key in data:
                k = norm_model(key)
                if k:
                    lookup.setdefault(k, brand)
        except Exception:
            pass
    return lookup


def jitter_sleep(lo=0.3, hi=0.9):
    """隨機抖動延遲，避免機械式固定間隔"""
    time.sleep(random.uniform(lo, hi))


# 經確認嘅「官方頁面唔再存在」reason code（HTTP 4xx gone）：只可以配 failed target
# 用，唔可以當 succeeded；wrapper 只會將呢啲 reason 歸類為 coverage pending。
COVERAGE_PENDING_REASONS = frozenset({'http-404', 'http-410'})


def coverage_pending_reason(exc):
    """HTTP 404／410 → 'http-404'／'http-410'；其他錯誤回 None（一律當硬失敗）。"""
    import urllib.error as _urlerror
    if isinstance(exc, _urlerror.HTTPError) and exc.code in (404, 410):
        return f'http-{exc.code}'
    return None


def emit_fetch_receipt(script, attempted, succeeded, failed, succeeded_models=None,
                       already_verified=None, failed_models=None, covers=None, extra=None,
                       skipped=None, failure_reasons=None, coverage_pending_models=None,
                       coverage_pending_reasons=None):
    """印出可審計機器 receipt（單行 JSON；wrapper 解析）；唔含 token／私人路徑。

    契約：attempted == succeeded + failed；len(succeededModels)==succeeded；
    len(failedModels)==failed；len(alreadyVerified)==skipped；
    covers == canonical union(succeededModels, alreadyVerified)。

    新增（向後兼容、可選）：
    - failureReasons：{failed model: reason code}，必須覆蓋全部 failedModels；
    - coveragePendingModels／coveragePendingReasons：failed 之中經確認「官方頁面
      已唔存在」嘅 subset；reason 只准 COVERAGE_PENDING_REASONS。
    數量／集合唔一致即 ValueError（呼叫方 fail-closed，唔會出假 receipt）。
    """
    import sys as _sys
    succ = sorted({str(m) for m in (succeeded_models or [])})
    already = sorted({str(m) for m in (already_verified or [])})
    failm = sorted({str(m) for m in (failed_models or [])})
    if int(attempted) != int(succeeded) + int(failed):
        raise ValueError('receipt attempted != succeeded + failed')
    if len(succ) != int(succeeded):
        raise ValueError('receipt len(succeededModels) != succeeded')
    if len(failm) != int(failed):
        raise ValueError('receipt len(failedModels) != failed')
    skip_count = len(already) if skipped is None else int(skipped)
    if skip_count != len(already):
        raise ValueError('receipt skipped != len(alreadyVerified)')
    cov = sorted({norm_model(m) for m in (covers if covers is not None else succ + already)})
    succ_norm = {norm_model(m) for m in succ}
    already_norm = {norm_model(m) for m in already}
    fail_norm = {norm_model(m) for m in failm}
    if cov != sorted(succ_norm | already_norm):
        raise ValueError('receipt covers != canonical union(succeededModels, alreadyVerified)')
    if succ_norm & fail_norm:
        raise ValueError('receipt model 同時 succeeded 同 failed')
    payload = {
        'schemaVersion': 1,
        'script': script,
        'attempted': int(attempted),
        'succeeded': int(succeeded),
        'failed': int(failed),
        'skipped': skip_count,
        'succeededModels': succ,
        'failedModels': failm,
        'alreadyVerified': already,
        'covers': cov,
    }
    if failure_reasons is not None:
        reasons = {str(k): str(v) for k, v in dict(failure_reasons).items()}
        if {norm_model(k) for k in reasons} != fail_norm:
            raise ValueError('receipt failureReasons 同 failedModels 唔一致')
        payload['failureReasons'] = reasons
    pending = [str(m) for m in (coverage_pending_models or [])]
    if pending:
        pending_reasons = {str(k): str(v) for k, v in dict(coverage_pending_reasons or {}).items()}
        pending_norm = {norm_model(m) for m in pending}
        if not pending_norm <= fail_norm:
            raise ValueError('receipt coveragePendingModels 必須係 failedModels subset')
        if pending_norm != {norm_model(k) for k in pending_reasons}:
            raise ValueError('receipt coveragePendingReasons 同 coveragePendingModels 唔一致')
        bad = {v for v in pending_reasons.values() if v not in COVERAGE_PENDING_REASONS}
        if bad:
            raise ValueError(f'receipt coveragePendingReasons 有未知 reason：{sorted(bad)}')
        payload['coveragePendingModels'] = sorted(set(pending))
        payload['coveragePendingReasons'] = pending_reasons
    if extra:
        payload.update(extra)
    _sys.stdout.write('AIRCON_FETCH_RECEIPT ' + json.dumps(payload, ensure_ascii=False) + '\n')
    _sys.stdout.flush()


def batch_failed(attempted, errors, coverage_pending=0):
    """批次成敗判定：任一「實際嘗試」嘅目標失敗就係失敗（errors > 0）。

    - 已有有效資料而明確 skip 嘅目標唔計 attempted，亦唔算失敗；
    - attempted=0（完全冇待抓目標）唔算失敗，可成功保留既有快照；
    - `coverage_pending`：failed 之中經確認「官方頁面已唔存在」嘅數量（只可以
      由呼叫方用 HTTP 404／410 等實證分類；硬失敗一律要非零）；
    - 任何失敗都唔可以寫出部分結果覆寫上次完整快照（呼叫方須先檢查）。
    """
    return errors - int(coverage_pending) > 0


def fetch(url, timeout=15, retries=3, extra_headers=None, context=None):
    """帶退避重試嘅 GET（返回解碼文字）；連續 403/429 會拋出 HTTPError（叫用方應停止而非硬碰）

    context: 可選 ssl.SSLContext（部分香港官網 TLS 憑證鏈唔完整時用 no_verify_ssl_context()）。
    """
    last = None
    for attempt in range(retries):
        headers = {
            'User-Agent': BOT_UA,
            'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-HK,zh;q=0.9,en;q=0.5',
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, headers=headers)
        try:
            return urllib.request.urlopen(req, timeout=timeout, context=context).read().decode('utf-8', 'ignore')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (404, 410):
                raise  # 頁面確認唔存在：即刻停止，唔好再重試（由呼叫方分類 coverage pending）
            if e.code in (403, 429) and attempt < retries - 1:
                wait = int(e.headers.get('Retry-After') or 0) or 10 * (attempt + 1)
                time.sleep(wait)
            elif e.code in (403, 429):
                raise  # 已退避多次仍被拒 → 唔好再硬碰
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last


def load_json(path, default=None):
    """讀 JSON；檔案唔存在或壞檔回 default，唔會整死批次"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data, indent=None):
    """原子寫 JSON（ensure_ascii=False）；先寫 tmp 再 os.replace，失敗唔會留低半寫檔。"""
    path = os.fspath(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def html_to_text(html, joiner=' ', keep_lines=False):
    """HTML 轉純文字：移除 script/style/標籤，壓縮多餘空白"""
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html or '', flags=re.S | re.I)
    t = re.sub(r'<[^>]+>', '\n' if keep_lines else joiner, t)
    t = re.sub(r'[ \t\r]+', ' ', t)
    if keep_lines:
        lines = [ln.strip() for ln in t.split('\n') if ln.strip()]
        return '\n'.join(lines)
    return re.sub(r'\s+', joiner, t).strip()


def no_verify_ssl_context():
    """部分香港官網 TLS 憑證鏈唔完整，先提供一個唔驗證憑證嘅 context（同舊行為一致）"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_registrations(csv_path=None):
    """載入 EMSD CSV 全部登記記錄（唔去重）→ [(brand, model), ...]（D12）"""
    path = csv_path or EMSD_CSV
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    out = []
    for r in rows:
        if len(r) < 15 or r[1].strip() == '型號':
            continue
        out.append((r[0].strip(), r[1].strip()))
    return out


def load_models(csv_path=None):
    """載入 EMSD CSV 型號清單（canonical product view：按 canonical key 去重，D11/D12）"""
    path = csv_path or EMSD_CSV
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    models = []
    seen = set()
    for r in rows:
        if len(r) < 15 or r[1].strip() == '型號':
            continue
        m = r[1].strip()
        k = canonical_model_key(r[0], m)
        if not norm_model(m) or k in seen:
            continue
        seen.add(k)
        models.append(m)
    return models


# 能源標籤級別固定次序（1→5；顯示層唔可以因 0 而隱藏）
ENERGY_LEVELS = ('1級', '2級', '3級', '4級', '5級')


def energy_level(value):
    """EMSD 能源級別欄位值 → 'N級'（只認 1–5）；其他值原樣回傳（空白 → '待查'）"""
    v = str(value or '').strip()
    if v in ('1', '2', '3', '4', '5'):
        return v + '級'
    return v or '待查'


def load_energy_distributions(csv_path=None):
    """回傳 (registration_counts, canonical_model_counts)：
    - registration_counts：逐筆登記（同 load_registrations 一致）；
    - canonical_model_counts：按 canonical key 去重（同 load_models 同一 dedup 規則）。
    兩者語意唔同（D12）：registrationCount 唔等於 modelCount，唔可以互換或相加。
    """
    path = csv_path or EMSD_CSV
    with open(path, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    reg = {}
    canon = {}
    seen = set()
    for r in rows:
        if len(r) < 15 or r[1].strip() == '型號':
            continue
        lv = energy_level(r[4])
        reg[lv] = reg.get(lv, 0) + 1
        model = r[1].strip()
        k = canonical_model_key(r[0], model)
        if not norm_model(model) or k in seen:
            continue
        seen.add(k)
        canon[lv] = canon.get(lv, 0) + 1
    return reg, canon
