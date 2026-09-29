#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rasonic／Frostar 官方網店核實（rasonicshop.hk 樂信牌專賣店網上商店）
分類頁（分頁）→ 產品頁 JSON-LD（名稱/介紹）
輸出: rasonic_official.json {型號: {name, price, desc, url, mode, remote, wifi}}

2026-09-29 repair（user-approved R2/R3）：
- 除 RC- 系列外，支援 Frostar FR-KS 系列（FR-KS7/9/12/18）；
- 型號身份必須同時出現喺 Product JSON-LD name 同 URL slug（正規化後），
  防止 redirect／錯誤頁被當成成功；
- HTTP 404／410 且 URL slug 可以對應到 EMSD 已登記型號 → 明確 coverage pending
  （唔會發明資料、唔會移除型號）；其他網絡／parser／內容錯誤一律硬失敗；
- coverage pending 時保留舊快照條目，唔會用新 snapshot 刪走舊規格。
"""
import json
import os
import re
import sys
import time

from crawl_utils import (fetch, no_verify_ssl_context, batch_failed, save_json,
                         emit_fetch_receipt, coverage_pending_reason,
                         load_models, norm_model)

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
CTX = no_verify_ssl_context()

# 支援型號 token：RC-xxx（樂信）同 FR-KSxx（霜牌/Frostar）
MODEL_TOKEN_RE = re.compile(r'(FR-KS\d+[A-Z]*|RC-[A-Z0-9]+)')


def get(url, timeout=15):
    return fetch(url, timeout=timeout, context=CTX)


def strip_html(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t)


def _norm_token(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def _product_json_ld(html):
    """回傳 Product JSON-LD dict；冇／解析失敗回 None（唔可以當成功）。"""
    product = None
    for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
        try:
            j = json.loads(ld)
        except Exception:
            continue
        if isinstance(j, dict) and j.get('@type') == 'Product':
            product = j
    return product


def parse_product_page(html, url):
    """由官方產品頁解析型號／規格；身份唔完整即 raise ValueError。"""
    product = _product_json_ld(html)
    if not isinstance(product, dict):
        raise ValueError('冇 Product JSON-LD（可能係空白／登入／錯誤頁）')
    name = str(product.get('name') or '').strip()
    if not name:
        raise ValueError('Product JSON-LD 冇名稱，無法確認身份')
    m = MODEL_TOKEN_RE.search(name.upper())
    if not m:
        raise ValueError('Product name 搵唔到支援型號（RC-／FR-KS）')
    model = m.group(1)
    # 型號身份：原 configured URL slug 嘅精確型號 token（唔係 substring）必須同
    # Product JSON-LD name token 完全一致。crawl_utils.fetch 冇提供 redirect 後 final
    # URL 證據，所以呢度只聲明「configured URL slug + Product JSON-LD name」身份，
    # 唔會聲稱 redirected final URL 已驗證。
    url_token = _model_from_url(url)
    if not url_token or _norm_token(url_token) != _norm_token(model):
        raise ValueError(
            f'型號 {model} 同 configured URL slug 型號 {url_token!r} 唔一致（拒絕當成功）')
    desc = str(product.get('description') or '')
    offers = product.get('offers') or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = ''
    if isinstance(offers, dict):
        price = str(offers.get('price') or offers.get('lowPrice') or '')
    if not price:
        mp = re.search(r'<meta[^>]+property="og:price:amount"[^>]+content="([\d.]+)"', html)
        price = mp.group(1) if mp else ''
    if not price:
        prices = re.findall(r'HK\$([\d,]+(?:\.\d+)?)', html[:200000])
        cands = [p.replace(',', '') for p in prices
                 if int(float(p.replace(',', ''))) > 500]
        price = cands[0] if cands else ''
    item = {
        'name': name,
        'model': model,
        'price': 'HK$' + price if price else '',
        'desc': re.sub(r'\s+', ' ', desc)[:800],
        'url': url,
        'mode': '冷暖' if ('冷暖' in name or 'heat-pump' in url.lower()) else
                ('淨冷' if ('淨冷' in name or 'cooling' in url.lower()) else ''),
        'remote': '✅' if ('遙控' in name or 'remote' in url.lower() or '遙控' in desc) else '',
        'wifi': '✅' if ('Wi-Fi' in name or 'Wi Fi' in name or 'wi-fi' in url.lower()) else '',
    }
    item['evidence'] = {
        'source': 'json-ld-product',
        'identityBasis': 'configured-url-slug+product-json-ld-name',
        'urlSlugToken': url_token,
        'configuredUrl': url,
        'sku': str(product.get('sku') or ''),
        'modelInName': True,
        'modelInConfiguredUrl': True,
        'availability': str((offers.get('availability') if isinstance(offers, dict) else '') or ''),
    }
    return item


def _model_from_url(url):
    """由 configured URL slug 推導精確型號 token（唔靠 substring；亦唔當成功資料）。"""
    m = MODEL_TOKEN_RE.search(str(url).upper())
    return m.group(1) if m else None


def main():
    with open(os.path.join(BASE, 'rasonic_urls.json'), encoding='utf-8') as f:
        urls = json.load(f)
    out_path = os.path.join(BASE, 'rasonic_official.json')
    existing = {}
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding='utf-8') as f:
                existing = json.load(f)
        except (OSError, ValueError) as e:
            print(f'❌ 現有 rasonic_official.json 損毀，唔可以默默重寫：{e}', file=sys.stderr)
            sys.exit(1)
        if not isinstance(existing, dict):
            print('❌ 現有 rasonic_official.json 結構唔正確，唔可以默默重寫', file=sys.stderr)
            sys.exit(1)
    # 保留舊快照：coverage pending 只更新成功目標，唔會刪走舊條目／規格
    out = dict(existing)
    attempted = errors = 0
    succeeded_models = []
    error_models = []
    failure_reasons = {}
    pending_models = []
    pending_reasons = {}
    emsd_models = {norm_model(m) for m in load_models()}
    for url in urls:
        attempted += 1
        try:
            html = get(url)
            item = parse_product_page(html, url)
            model = item['model']
            out[model] = item
            succeeded_models.append(model)
            print(f"{model}: {item['price']:>9} | {item['mode'] or '-':4} | "
                  f"wifi={item['wifi'] or '-':4} | {item['name'][:50]}")
        except Exception as e:
            errors += 1
            derived = _model_from_url(url)
            reason = coverage_pending_reason(e)
            if reason and derived and norm_model(derived) in emsd_models:
                # 已確認官方頁面唔存在，而且 URL 身份對得上 EMSD 已登記型號：
                # 只可當 coverage pending（保留舊快照），絕對唔可以發明規格。
                pending_models.append(derived)
                pending_reasons[derived] = reason
                failure_reasons[derived] = reason
                error_models.append(derived)
                print(f'{url[-40:]}: COVERAGE-PENDING {reason}（{derived}；保留舊快照）')
            else:
                # 網絡／parser／身份不確定：硬失敗
                token = derived or f'URL:{url}'
                error_models.append(token)
                failure_reasons[token] = 'product-hard-failure'
                print(f'{url[-40:]}: ERR {str(e)[:50]}')
        time.sleep(0.2)

    if len(succeeded_models) != attempted - errors or len(error_models) != errors:
        raise RuntimeError(
            'fetch_rasonic receipt accounting mismatch：'
            f'attempted={attempted} succeeded={len(succeeded_models)} failed={len(error_models)}')

    emit_fetch_receipt('fetch_rasonic.py', attempted, len(succeeded_models), errors,
                       succeeded_models=succeeded_models, skipped=0,
                       failed_models=error_models, failure_reasons=failure_reasons,
                       coverage_pending_models=pending_models,
                       coverage_pending_reasons=pending_reasons)
    if batch_failed(attempted, errors, coverage_pending=len(pending_models)):
        print(f'❌ Rasonic {errors}/{attempted} 個目標失敗，唔覆寫現有快照，留待下次重試',
              file=sys.stderr)
        sys.exit(1)
    if out == existing:
        print('ℹ️ Rasonic 冇任何新資料（全部目標已 coverage pending／無變更），保留現有快照',
              file=sys.stderr)
        return
    save_json(out_path, out, indent=1)
    print(f'完成 {len(out)} 個型號（本輪新核實 {len(succeeded_models)} 個；'
          f'coverage pending {len(pending_models)} 個）')


if __name__ == '__main__':
    main()
