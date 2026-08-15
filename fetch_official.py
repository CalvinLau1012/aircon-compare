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
import ssl
import sys
import time
import urllib.request

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = r'd:\香港窗口式空調查找'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36'
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

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
    'cafb-12crn8-pc2', 'cafa-09crn8-pc2', 'cafc-18crn8-qc3', 'cafa-09crn8pc2',
    'cf-09vagf-h', 'cf-12vagf-h', 'cf-18vagf-h',
    'cfs-10vgpf', 'cfs-13vgpf', 'cfs-18vgpf', 'cfs-25vgpf',
]


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'zh-HK,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=timeout, context=CTX).read().decode('utf-8', 'ignore')


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


def parse_panasonic(model, html):
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


def fetch_panasonic(existing=None):
    results = {}
    for model, url in PANASONIC:
        if existing and model in existing and existing[model].get('size'):
            continue
        try:
            html = get(url)
            spec = parse_panasonic(model, html)
            spec['url'] = url
            results[model] = spec
            print(f"  {model}: {spec.get('size','?')[:40]} | {spec.get('weight','?')[:25]} | heat={spec['heat']} cool={spec['cool']}")
        except Exception as e:
            print(f'  {model}: ERR {str(e)[:60]}')
        time.sleep(0.3)
    return results


def fetch_hitachi(existing=None):
    results = {}
    models = set()
    for url in HITACHI_PAGES:
        try:
            html = get(url)
            body = strip_html(html)
            for m in re.finditer(r'\b(RAW-[A-Z]{2}\d{2}[A-Z]+|RA-\d{2}[A-Z]+)\b', body):
                models.add(m.group(1).upper())
        except Exception as e:
            print(f'  列表頁 {url[-25:]}: ERR {str(e)[:50]}')
        time.sleep(0.2)
    print(f'HITACHI 在售型號 {len(models)} 個: {sorted(models)}')
    base = 'https://www.hitachi-homeappliances.com.hk/tc/products/'
    for model in sorted(models):
        if existing and model in existing and existing[model].get('size'):
            continue
        url = base + model.lower() + '.html'
        try:
            html = get(url)
            body = strip_html(html)
            size = grab(body, ['產品主機體尺寸'], 90) or grab(body, ['毫米'], 80)
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
            results[model] = spec
            print(f"  {model}: {spec.get('size','?')[:50]} | {spec.get('weight','?')[:20]} | {spec.get('gas','?')[:25]}")
        except Exception as e:
            print(f'  {model}: ERR {str(e)[:50]}')
        time.sleep(0.25)
    return results


def fetch_comfee(existing=None):
    results = {}
    for slug in COMFEE_MODELS:
        key = slug.upper()
        if existing and key in existing and existing[key].get('size'):
            continue
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
            results[slug.upper()] = spec
            print(f"  {slug.upper()}: {spec.get('size','?')[:45]} | {spec.get('weight','?')[:22]} | gas={spec.get('gas','?')[:20]}")
        except Exception as e:
            print(f'  {slug.upper()}: ERR {str(e)[:50]}')
        time.sleep(0.25)
    return results


def main():
    out_path = os.path.join(BASE, 'official_specs.json')
    all_results = {}
    if os.path.exists(out_path):
        with open(out_path, encoding='utf-8') as f:
            all_results = json.load(f)
    r = fetch_panasonic(all_results)
    all_results.update(r)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print(f'Panasonic 完成，累計 {len(all_results)} 個型號')
    r2 = fetch_hitachi(all_results)
    all_results.update(r2)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print(f'HITACHI 完成，累計 {len(all_results)} 個型號')
    r3 = fetch_comfee(all_results)
    all_results.update(r3)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print(f'COMFEE 完成，累計 {len(all_results)} 個型號')


if __name__ == '__main__':
    main()
