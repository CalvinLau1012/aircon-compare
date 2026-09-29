#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
品牌官網規格核實 v2（直接抓官網產品頁，唔經 Google）
Panasonic: 24 個窗口機產品頁（server-rendered，urllib 可抓）
輸出: official_specs.json
"""
import json
import os
import re
import sys
import time

from crawl_utils import (fetch, no_verify_ssl_context, batch_failed, save_json,
                         emit_fetch_receipt, coverage_pending_reason,
                         COVERAGE_PENDING_REASONS, load_models, norm_model)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
CTX = no_verify_ssl_context()

# 分類頁攞到嘅全部產品 URL（含 item id）
PANASONIC = [
    ('CW-SUL70BA', 'https://www.panasonic.hk/zh-cht/item/9592--cw-sul70ba'),
    ('CW-SUL90BA', 'https://www.panasonic.hk/zh-cht/item/9593--cw-sul90ba'),
    ('CW-HZ70AA', 'https://www.panasonic.hk/zh-cht/item/6831--cw-hz70aa'),
    ('CW-HZ90AA', 'https://www.panasonic.hk/zh-cht/item/6834--cw-hz90aa'),
    ('CW-HZ120AA', 'https://www.panasonic.hk/zh-cht/item/6837--cw-hz120aa'),
    ('CW-HZ180AA', 'https://www.panasonic.hk/zh-cht/item/6840--cw-hz180aa'),
    ('CW-HZ240AA', 'https://www.panasonic.hk/zh-cht/item/6843--cw-hz240aa'),
    ('CW-HU70AA', 'https://www.panasonic.hk/zh-cht/item/6846--cw-hu70aa'),
    ('CW-HU90AA', 'https://www.panasonic.hk/zh-cht/item/6849--cw-hu90aa'),
    ('CW-HU120AA', 'https://www.panasonic.hk/zh-cht/item/6852--cw-hu120aa'),
    ('CW-HU180AA', 'https://www.panasonic.hk/zh-cht/item/6855--cw-hu180aa'),
    ('CW-HU240AA', 'https://www.panasonic.hk/zh-cht/item/6858--cw-hu240aa'),
    ('CW-SU70AA', 'https://www.panasonic.hk/zh-cht/item/6861--cw-su70aa'),
    ('CW-SU90AA', 'https://www.panasonic.hk/zh-cht/item/6864--cw-su90aa'),
    ('CW-SU120AA', 'https://www.panasonic.hk/zh-cht/item/6867--cw-su120aa'),
    ('CW-SU180AA', 'https://www.panasonic.hk/zh-cht/item/6870--cw-su180aa'),
    ('CW-SU240AA', 'https://www.panasonic.hk/zh-cht/item/6873--cw-su240aa'),
    ('CW-SUL120BA', 'https://www.panasonic.hk/zh-cht/item/6876--cw-sul120ba'),
    ('CW-SUL180BA', 'https://www.panasonic.hk/zh-cht/item/6879--cw-sul180ba'),
    ('CW-SUL240BA', 'https://www.panasonic.hk/zh-cht/item/6882--cw-sul240ba'),
    ('CW-N721JA', 'https://www.panasonic.hk/zh-cht/item/6885--cw-n721ja'),
    ('CW-N921JA', 'https://www.panasonic.hk/zh-cht/item/6888--cw-n921ja'),
    ('CW-N1221VA', 'https://www.panasonic.hk/zh-cht/item/6891--cw-n1221va'),
    ('CW-N1821EA', 'https://www.panasonic.hk/zh-cht/item/6894--cw-n1821ea'),
]
HITACHI_PAGES = [
    'https://www.hitachi-homeappliances.com.hk/tc/products/7330-btu-h.html',
    'https://www.hitachi-homeappliances.com.hk/tc/products/8530-btu-h-1.html',
    'https://www.hitachi-homeappliances.com.hk/tc/products/12000-btu-h.html',
    'https://www.hitachi-homeappliances.com.hk/tc/products/17410-btu-h.html',
    'https://www.hitachi-homeappliances.com.hk/tc/products/21495-btu-h.html',
]

COMFEE_MODELS = [
    'cwf-07crfn8-ad5', 'cwf-09crfn8-ad5', 'cwf-12crfn8-ad5', 'cwf-18crfn8-ad5',
    'cfw-07ff-m', 'cfw-09ff-m', 'cfw-12ff-m', 'cfw-18ff-m',
    'cafb-12crn8-pc2', 'cafc-18crn8-qc3', 'cafa-09crn8pc2',
    'cf-09vagf-h', 'cf-12vagf-h', 'cf-18vagf-h',
    'cfs-10vgpf', 'cfs-13vgpf', 'cfs-18vgpf', 'cfs-25vgpf',
]
# 2026-09-29 repair：舊 target 'cafa-09crn8-pc2'（連字號）實測 HTTP 404，而
# 'cafa-09crn8pc2'（無連字號）HTTP 200 且係官方在售頁；舊 target 係重複 stale key，
# 移除佢唔會移除任何型號（canonical cover 由正確 slug 提供）。


def get(url, timeout=15):
    return fetch(url, timeout=timeout, context=CTX)


def strip_html(html):
    txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    txt = re.sub(r'<[^>]+>', '\n', txt)
    txt = re.sub(r'[ \t\r]+', ' ', txt)
    return txt


def grab(body, kws, n=90):
    """關鍵字後跨行抓取"""
    for kw in kws:
        i = body.find(kw)
        if i >= 0:
            seg = ' '.join(body[i:i + n].split())
            return seg
    return ''


def parse_panasonic(html):
    body = strip_html(html)
    m = re.search(r'<title>([^<]*)</title>', html)
    title = m.group(1) if m else ''
    return {
        'size': grab(body, ['體積(高', '體積 (高', '機身體積'], 70),
        'weight': grab(body, ['淨重'], 45),
        'warranty': grab(body, ['保用'], 60),
        'wifi': 'Wi-Fi' in body or 'WiFi' in body,
        'remote': '遙控' in body,
        'heat': '冷暖' in title,
        'cool': '淨冷' in title,
        'title': title.strip(),
    }


def emsd_registered():
    """目前 EMSD CSV 已登記型號 canonical set（每次 batch 只 load 一次）。"""
    return {norm_model(m) for m in load_models()}


def pending_eligible(reason, model, url_token, registered):
    """coverage pending 資格（2026-09-29 返修）：

    - reason 必須係 HTTP 404／410（其他一律硬失敗）；
    - target model 必須喺目前 EMSD 已登記型號 canonical set；
    - URL token 必須同 target model canonical 相等（唔靠 substring）；
    三者缺一即唔可以 pending，交由呼叫方當硬失敗。
    """
    if not reason:
        return False
    if not registered or norm_model(model) not in registered:
        return False
    if not url_token or norm_model(url_token) != norm_model(model):
        return False
    return True


def fetch_panasonic(existing=None, registered=None):
    registered = registered if registered is not None else emsd_registered()
    results = {}
    attempted = errors = 0
    skipped = []
    failed = []
    failure_reasons = {}
    for model, url in PANASONIC:
        if existing and model in existing and existing[model].get('size'):
            skipped.append(model)
            continue
        attempted += 1
        try:
            html = get(url)
            spec = parse_panasonic(html)
            if not any((spec.get('size'), spec.get('weight'), spec.get('warranty'), spec.get('title'))):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            spec['url'] = url
            results[model] = spec
            print(f"  {model}: {spec.get('size','?')[:40]} | {spec.get('weight','?')[:25]} | heat={spec['heat']} cool={spec['cool']}")
        except Exception as e:
            errors += 1
            failed.append(model)
            token_match = re.search(r'(CW-[A-Z0-9]+)', url.upper())
            url_token = token_match.group(1) if token_match else None
            reason = coverage_pending_reason(e)
            if pending_eligible(reason, model, url_token, registered):
                failure_reasons[model] = reason
            else:
                failure_reasons[model] = 'product-hard-failure'
            print(f'  {model}: ERR {str(e)[:60]}')
        time.sleep(0.3)
    return results, attempted, errors, skipped, failed, failure_reasons


def fetch_hitachi(existing=None, registered=None):
    registered = registered if registered is not None else emsd_registered()
    results = {}
    attempted = errors = 0
    skipped = []
    failed = []
    failure_reasons = {}
    listing_failures = []
    models = set()
    for url in HITACHI_PAGES:
        # 列表頁係列出型號嘅前置步驟，唔係一個「型號 target」：成功抓取唔可以
        # 計入 succeededModels（否則 counts／lists 唔一致）；失敗就要硬性記錄。
        try:
            html = get(url)
            body = strip_html(html)
            for m in re.finditer(r'\b(RAW-[A-Z]{2}\d{2}[A-Z]+|RA-\d{2}[A-Z]+)\b', body):
                models.add(m.group(1).upper())
        except Exception as e:
            listing_failures.append(url)
            print(f'  列表頁 {url[-25:]}: ERR {str(e)[:50]}')
        time.sleep(0.2)
    for url in listing_failures:
        attempted += 1
        errors += 1
        token = f'HITACHI-LISTING:{url}'
        failed.append(token)
        failure_reasons[token] = 'listing-hard-failure'
    if not models and not listing_failures:
        # 全部列表頁 HTTP 成功但解析唔到任何型號＝預期之外內容（改版／登入／空白）
        for url in HITACHI_PAGES:
            attempted += 1
            errors += 1
            token = f'HITACHI-LISTING:{url}'
            failed.append(token)
            failure_reasons[token] = 'listing-empty-hard-failure'
        print('  HITACHI 列表頁全部解析唔到型號（可能係空白／登入／錯誤頁），唔可以當成功',
              file=sys.stderr)
    print(f'HITACHI 在售型號 {len(models)} 個: {sorted(models)}')
    base = 'https://www.hitachi-homeappliances.com.hk/tc/products/'
    for model in sorted(models):
        if existing and model in existing and existing[model].get('size'):
            skipped.append(model)
            continue
        attempted += 1
        url = base + model.lower() + '.html'
        try:
            html = get(url)
            body = strip_html(html)
            spec = {
                'size': grab(body, ['產品主機體尺寸'], 100),
                'weight': grab(body, ['淨重', '重量'], 50),
                'energy': grab(body, ['能源標籤'], 40),
                'gas': grab(body, ['雪種', '製冷劑'], 40),
                'wifi': 'Wi-Fi' in body or 'WiFi' in body,
                'remote': '遙控' in body,
                'heat': '冷暖' in body,
                'cool': '淨冷' in body or '窗口式冷氣機' in body,
                'url': url,
            }
            if not any((spec.get('size'), spec.get('weight'), spec.get('energy'), spec.get('gas'))):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            results[model] = spec
            print(f"  {model}: {spec.get('size','?')[:50]} | {spec.get('weight','?')[:20]} | {spec.get('gas','?')[:25]}")
        except Exception as e:
            errors += 1
            failed.append(model)
            url_token = re.search(r'((?:RAW-[A-Z]{2}\d{2}[A-Z]+|RA-\d{2}[A-Z]+))',
                                  url.upper())
            url_token = url_token.group(1) if url_token else None
            reason = coverage_pending_reason(e)
            if pending_eligible(reason, model, url_token, registered):
                failure_reasons[model] = reason
            else:
                failure_reasons[model] = 'product-hard-failure'
            print(f'  {model}: ERR {str(e)[:50]}')
        time.sleep(0.25)
    return results, attempted, errors, skipped, failed, failure_reasons


def fetch_comfee(existing=None, registered=None):
    registered = registered if registered is not None else emsd_registered()
    results = {}
    attempted = errors = 0
    skipped = []
    failed = []
    failure_reasons = {}
    for slug in COMFEE_MODELS:
        key = slug.upper()
        if existing and key in existing and existing[key].get('size'):
            skipped.append(key)
            continue
        attempted += 1
        url = f'https://www.feelcomfee.com/hk/products/air-conditioner/{slug}'
        try:
            html = get(url)
            body = strip_html(html)
            spec = {
                'size': grab(body, ['尺寸'], 70),
                'weight': grab(body, ['淨重'], 50),
                'gas': grab(body, ['雪種'], 40),
                'wifi': 'Wi-Fi' in body or 'WiFi' in body or 'IoT' in body,
                'remote': '遙控' in body,
                'energy': grab(body, ['能源標籤'], 40),
                'url': url,
            }
            if not any((spec.get('size'), spec.get('weight'), spec.get('gas'), spec.get('energy'))):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            results[key] = spec
            print(f"  {key}: {spec.get('size','?')[:45]} | {spec.get('weight','?')[:22]} | gas={spec.get('gas','?')[:20]}")
        except Exception as e:
            errors += 1
            failed.append(key)
            url_token = url.rsplit('/', 1)[-1].upper()
            reason = coverage_pending_reason(e)
            if pending_eligible(reason, key, url_token, registered):
                failure_reasons[key] = reason
            else:
                failure_reasons[key] = 'product-hard-failure'
            print(f'  {key}: ERR {str(e)[:50]}')
        time.sleep(0.25)
    return results, attempted, errors, skipped, failed, failure_reasons


def main():
    out_path = os.path.join(BASE, 'official_specs.json')
    all_results = {}
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as f:
            all_results = json.load(f)
    # 只喺記憶體累積；任何目標失敗都唔會寫出部分結果覆寫上次完整快照。
    base = dict(all_results)
    # EMSD 已登記型號 set 每次 batch 只 load 一次（coverage pending 資格用）。
    registered = emsd_registered()
    r, pa, pe, ps, pf, pr = fetch_panasonic(base, registered)
    base.update(r)
    r2, ha, he, hs, hf, hr = fetch_hitachi(base, registered)
    base.update(r2)
    r3, ca, ce, cs, cf, cr = fetch_comfee(base, registered)
    base.update(r3)
    attempted = pa + ha + ca
    errors = pe + he + ce
    already_verified = list(ps) + list(hs) + list(cs)
    succeeded_models = (list(r.keys()) + list(r2.keys()) + list(r3.keys()))
    failed_models = list(pf) + list(hf) + list(cf)
    failure_reasons = {**pr, **hr, **cr}
    # 嚴格會計：每個 actual attempt 必須對應一個成功輸出（有 evidence）或一個失敗
    if len(succeeded_models) != attempted - errors or len(failed_models) != errors:
        raise RuntimeError(
            'fetch_official receipt accounting mismatch：'
            f'attempted={attempted} succeeded={len(succeeded_models)} failed={len(failed_models)}')
    pending_models = [m for m in failed_models
                      if failure_reasons.get(m) in COVERAGE_PENDING_REASONS]
    pending_reasons = {m: failure_reasons[m] for m in pending_models}
    emit_fetch_receipt('fetch_official.py', attempted, len(succeeded_models), errors,
                       succeeded_models=succeeded_models, already_verified=already_verified,
                       failed_models=failed_models, skipped=len(already_verified),
                       failure_reasons=failure_reasons,
                       coverage_pending_models=pending_models,
                       coverage_pending_reasons=pending_reasons)
    if batch_failed(attempted, errors, coverage_pending=len(pending_models)):
        print(f'❌ Panasonic/HITACHI/COMFEE {errors}/{attempted} 個目標失敗，'
              '唔覆寫現有快照，留待下次重試', file=sys.stderr)
        sys.exit(1)
    if base == all_results:
        print(f'ℹ️ official 冇新資料（全部目標已 skip／庫存已最新），保留現有快照', file=sys.stderr)
        return
    save_json(out_path, base, indent=1)
    print(f'完成，累計 {len(base)} 個型號（Panasonic+HITACHI+COMFEE）')


if __name__ == '__main__':
    main()
