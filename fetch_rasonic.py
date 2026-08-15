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
import ssl
import sys
import time
import urllib.request

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'zh-HK,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read().decode('utf-8', 'ignore')


def strip_html(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t)


def main():
    urls = json.load(open(os.path.join(BASE, 'rasonic_urls.json'), encoding='utf-8'))
    out = {}
    for i, url in enumerate(urls):
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
            # 型號：從名稱提取 RC- 開頭
            m = re.search(r'\b(RC-[A-Z0-9]+)', name.upper())
            model = m.group(1) if m else 'UNKNOWN-' + str(i)
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
            print(f'{url[-40:]}: ERR {str(e)[:50]}')
        time.sleep(0.2)

    with open(os.path.join(BASE, 'rasonic_official.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f'完成 {len(out)} 個型號')


if __name__ == '__main__':
    main()
