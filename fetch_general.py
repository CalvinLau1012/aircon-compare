#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GENERAL 珍寶香港總代理 general-aircon.com 窗口機規格抓取"""
import json, re, ssl, sys, io, os, urllib.request, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36','Accept-Language':'zh-Hant,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
def txt(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', ' ', t)
    return re.sub(r'\s+', ' ', t)

URLS = [
('AKWB7NID','https://www.general-aircon.com/zh-hant/product/akwb7nid-3-4-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AKWB7NIC','https://www.general-aircon.com/zh-hant/product/akwb7nic-3-4-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AKWA7HNR','https://www.general-aircon.com/zh-hant/product/akwa7hnr-3-4-hp-r32-refrigerant-cooling'),
('AKWB9NID','https://www.general-aircon.com/zh-hant/product/akwb9nid-1-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AKWB9NIC','https://www.general-aircon.com/zh-hant/product/akwb9nic-1-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AKWA9HNR','https://www.general-aircon.com/zh-hant/product/akwa9hnr-1-hp-r32-refrigerant-cooling'),
('AMWB12NID','https://www.general-aircon.com/zh-hant/product/amwb12nid-1-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AMWB12NIC','https://www.general-aircon.com/zh-hant/product/amwb12nic-1-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AMWA12HNR','https://www.general-aircon.com/zh-hant/product/amwa12hnr-1-5-hp-r32-refrigerant-cooling'),
('AFWB18NID','https://www.general-aircon.com/zh-hant/product/afwb18nid-2-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AFWB18NIC','https://www.general-aircon.com/zh-hant/product/afwb18nic-2-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('AFWA18HNR','https://www.general-aircon.com/zh-hant/product/afwa18hnr-2hp-r32-refrigerant-cooling'),
('AFWA17FAT','https://www.general-aircon.com/zh-hant/product/afwa17fat-2hp-cooling'),
('ALWB24NID','https://www.general-aircon.com/zh-hant/product/alwb24nid-2-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('ALWB24NIC','https://www.general-aircon.com/zh-hant/product/alwb24nic-2-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
('ALWA24HNR','https://www.general-aircon.com/zh-hant/product/alwa24hnr-2-5hp-r32-refrigerant-cooling'),
]
out = {}
for model, url in URLS:
    try:
        h = get(url)
        t = txt(h)
        # 尺寸：尺寸(高x寬x深) 後接三數字
        m = re.search(r'尺寸\s*\(高\s*x\s*寬\s*x\s*深\)[^0-9]*([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)', t, re.I)
        size = f'{m.group(1)}\u00d7{m.group(2)}\u00d7{m.group(3)}' if m else ''
        mw = re.search(r'淨重\s*[^0-9]*([\d.]+)', t, re.I)
        weight = mw.group(1) + 'kg' if mw else ''
        mode = '冷暖' if '冷暖' in t[:8000] else ('淨冷' if '淨冷' in t[:8000] else '')
        item = {'size': size, 'weight': weight, 'mode': mode,
                'remote': '✅' if '無線遙控' in t or '遙控器' in t else '',
                'wifi': '✅' if re.search(r'Wi-?Fi', t, re.I) else '',
                'gas': 'R32' if 'R32' in t[:8000] else ('R410A' if 'R410A' in t[:8000] else ''),
                'url': url}
        out[model] = item
        print(f"{model}: {size} | {weight} | {item['gas']} | {mode}")
    except Exception as e:
        print(f'{model}: ERR {str(e)[:50]}')
    time.sleep(0.2)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'general_official.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('完成', len(out))
