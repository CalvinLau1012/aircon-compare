#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核心 29 型號規格補充抓取
- 豐澤產品頁：尺寸、重量、保養、WiFi、噪音（如有）
- Price og:description：尺寸、雪種交叉驗證
輸出：specs.json
"""
import json
import os
import re
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36'

CORE_MODELS = [
    'TA-09EOG', 'TA-12EOG', 'TA-18EOG',
    'W09R5A', 'W12R5A', 'W18R5A', 'W24R5A',
    'CHK09BE', 'CHK12BE', 'CHK18BE',
    'MW-09CR8C', 'MW-12CR8C',
    'RA-10RF', 'RC-XG9', 'RC-XG12', 'RFR18FNTN',
    'CWF-09CRFN8-AD5', 'CWF-12CRFN8-AD5', 'CWF-18CRFN8-AD5',
    'CHK09EAVXP', 'CHK12EAVXP', 'CHK18EAVX',
    'MW-09CRF8B', 'GWF09P', 'GWF12DB',
    'AMWB12NID', 'CW-HU90AA', 'CW-HU120AA', 'RC-TS18UV',
]


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'zh-HK,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8', 'ignore')


def extract_og(html):
    m = re.search(r'property="og:description" content="([^"]*)"', html)
    if not m:
        return {}
    d = {}
    for part in m.group(1).split(','):
        if ':' in part:
            k, v = part.split(':', 1)
            d[k.strip()] = v.strip()
    return d


def fetch_fortress(model):
    """豐澤搜尋 → 產品頁規格"""
    try:
        html = get('https://www.fortress.com.hk/zh-hk/search?q=' + urllib.parse.quote(model))
    except Exception as e:
        return {'error': str(e)[:80]}
    # 搵產品連結
    links = re.findall(r'href="(/zh-hk/product/[^"]+)"', html)
    if not links:
        return {'error': 'no product link'}
    prod_url = 'https://www.fortress.com.hk' + links[0]
    try:
        ph = get(prod_url)
    except Exception as e:
        return {'error': 'product page: ' + str(e)[:80]}
    out = {'fortress_url': prod_url}
    # 規格欄位
    def field(name):
        m = re.search(name + r'[^<\n]*</[^>]+>\s*<[^>]*>([^<]{1,60})<', ph)
        if m:
            return m.group(1).strip()
        m = re.search(name + r'[^"]*"?\s*[:：]\s*"?([^"<,\n]{1,60})', ph)
        return m.group(1).strip() if m else None
    out['size'] = field('機身體積') or field('尺寸')
    out['weight'] = field('重量')
    out['warranty'] = field('保養期') or field('保用')
    return out


def main():
    results = {}
    print('開始抓核心 29 型號規格...')
    for i, model in enumerate(CORE_MODELS):
        # Price og:description
        spec = {}
        try:
            sh = get('https://www.price.com.hk/search.php?g=A&q=' + urllib.parse.quote(model))
            pid = re.search(r'product\.php\?p=(\d+)', sh)
            if pid:
                ph = get('https://www.price.com.hk/product.php?p=' + pid.group(1))
                og = extract_og(ph)
                spec['price_og'] = og
                if '室內機尺寸' in og:
                    spec['size_price'] = og['室內機尺寸']
                spec['gas_price'] = og.get('雪種')
        except Exception as e:
            spec['price_error'] = str(e)[:60]

        # 豐澤
        try:
            fz = fetch_fortress(model)
            spec.update(fz)
        except Exception as e:
            spec['fortress_error'] = str(e)[:60]

        results[model] = spec
        print(f'{i+1}/29 {model}: size={spec.get("size", "?")} weight={spec.get("weight", "?")} '
              f'warranty={spec.get("warranty", "?")} price_size={spec.get("size_price", "?")}')
        time.sleep(0.4)

    out = os.path.join(BASE, 'specs.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print('存於', out)


if __name__ == '__main__':
    main()
