#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rasonic 官方網店核實（rasonicshop.hk 樂信牌專賣店網上商店）
分類頁（分頁）→ 產品頁 JSON-LD（名稱/介紹）
輸出: rasonic_official.json {型號: {name, price, desc, url, mode, remote, wifi}}
"""
import json
import os
import re
import sys
import time

from crawl_utils import fetch, no_verify_ssl_context, batch_failed, save_json, emit_fetch_receipt

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
CTX = no_verify_ssl_context()


def get(url, timeout=15):
    return fetch(url, timeout=timeout, context=CTX)


def strip_html(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t)


def main():
    with open(os.path.join(BASE, 'rasonic_urls.json'), encoding='utf-8') as f:
        urls = json.load(f)
    out = {}
    attempted = errors = 0
    error_models = []
    for i, url in enumerate(urls):
        attempted += 1
        model_key = None
        try:
            html = get(url)
            name = desc = price = ''
            for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
                try:
                    j = json.loads(ld)
                    if j.get('@type') == 'Product':
                        name = j.get('name', '')
                        desc = j.get('description', '')
                        off = j.get('offers') or {}
                        if isinstance(off, list):
                            off = off[0] if off else {}
                        price = str(off.get('price', '') or off.get('lowPrice', ''))
                except Exception:
                    pass
            # 型號：從名稱提取 RC- 開頭；提取不到 = 非產品／登入／錯誤頁，唔算成功
            m = re.search(r'\b(RC-[A-Z0-9]+)', name.upper())
            if not m:
                raise ValueError('搵唔到有效型號（可能係空白／登入／錯誤頁）')
            model = m.group(1)
            model_key = model
            if not name.strip():
                raise ValueError('冇產品名稱，無法確認身份')
            if not price:
                mp = re.search(r'<meta[^>]+property="og:price:amount"[^>]+content="([\d.]+)"', html)
                price = mp.group(1) if mp else ''
            if not price:
                prices = re.findall(r'HK\$([\d,]+(?:\.\d+)?)', html[:200000])
                # 排除運費 80：取出現次數最多或最大嘅合理價
                cands = [p.replace(',', '') for p in prices if int(float(p.replace(',', ''))) > 500]
                price = cands[0] if cands else ''
            item = {
                'name': name.strip(),
                'model': model,
                'price': 'HK$' + price if price else '',
                'desc': re.sub(r'\s+', ' ', desc)[:800],
                'url': url,
                'mode': '冷暖' if ('冷暖' in name or 'heat-pump' in url.lower()) else ('淨冷' if ('淨冷' in name or 'cooling' in url.lower()) else ''),
                'remote': '✅' if ('遙控' in name or 'remote' in url.lower() or '遙控' in desc) else '',
                'wifi': '✅' if ('Wi-Fi' in name or 'Wi Fi' in name or 'wi-fi' in url.lower()) else '',
            }
            out[model] = item
            print(f"{model}: {price:>9} | {item['mode'] or '-':4} | wifi={item['wifi'] or '-':4} | {name[:50]}")
        except Exception as e:
            errors += 1
            error_models.append(model_key or f'URL:{url}')
            print(f'{url[-40:]}: ERR {str(e)[:50]}')
        time.sleep(0.2)

    emit_fetch_receipt('fetch_rasonic.py', attempted, attempted - errors, errors,
                       succeeded_models=list(out.keys()), failed_models=error_models)
    if batch_failed(attempted, errors):
        print(f'❌ Rasonic {errors}/{attempted} 個目標失敗，唔覆寫現有快照，留待下次重試', file=sys.stderr)
        sys.exit(1)
    out_path = os.path.join(BASE, 'rasonic_official.json')
    if not out and os.path.exists(out_path):
        print('ℹ️ Rasonic 冇任何目標資料，保留現有快照', file=sys.stderr)
        return
    save_json(out_path, out, indent=1)
    print(f'完成 {len(out)} 個型號')


if __name__ == '__main__':
    main()
