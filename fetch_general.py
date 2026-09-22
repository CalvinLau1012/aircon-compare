#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GENERAL 珍寶香港總代理 general-aircon.com 窗口機規格抓取"""
import json, re, sys, os, time
from crawl_utils import fetch, html_to_text, no_verify_ssl_context, batch_failed, save_json, emit_fetch_receipt

CTX = no_verify_ssl_context()


def get(u):
    return fetch(u, context=CTX, extra_headers={'Accept-Language': 'zh-Hant,zh;q=0.9'})


URLS = [
    ('AKWB7NID', 'https://www.general-aircon.com/zh-hant/product/akwb7nid-3-4-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AKWB7NIC', 'https://www.general-aircon.com/zh-hant/product/akwb7nic-3-4-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AKWA7HNR', 'https://www.general-aircon.com/zh-hant/product/akwa7hnr-3-4-hp-r32-refrigerant-cooling'),
    ('AKWB9NID', 'https://www.general-aircon.com/zh-hant/product/akwb9nid-1-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AKWB9NIC', 'https://www.general-aircon.com/zh-hant/product/akwb9nic-1-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AKWA9HNR', 'https://www.general-aircon.com/zh-hant/product/akwa9hnr-1-hp-r32-refrigerant-cooling'),
    ('AMWB12NID', 'https://www.general-aircon.com/zh-hant/product/amwb12nid-1-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AMWB12NIC', 'https://www.general-aircon.com/zh-hant/product/amwb12nic-1-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AMWA12HNR', 'https://www.general-aircon.com/zh-hant/product/amwa12hnr-1-5-hp-r32-refrigerant-cooling'),
    ('AFWB18NID', 'https://www.general-aircon.com/zh-hant/product/afwb18nid-2-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AFWB18NIC', 'https://www.general-aircon.com/zh-hant/product/afwb18nic-2-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('AFWA18HNR', 'https://www.general-aircon.com/zh-hant/product/afwa18hnr-2hp-r32-refrigerant-cooling'),
    ('AFWA17FAT', 'https://www.general-aircon.com/zh-hant/product/afwa17fat-2hp-cooling'),
    ('ALWB24NID', 'https://www.general-aircon.com/zh-hant/product/alwb24nid-2-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('ALWB24NIC', 'https://www.general-aircon.com/zh-hant/product/alwb24nic-2-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c'),
    ('ALWA24HNR', 'https://www.general-aircon.com/zh-hant/product/alwa24hnr-2-5hp-r32-refrigerant-cooling'),
]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    base = os.path.dirname(os.path.abspath(__file__))
    out = {}
    attempted = errors = 0
    error_models = []
    for model, url in URLS:
        attempted += 1
        try:
            h = get(url)
            t = html_to_text(h)
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
            if not any((size, weight, mode, item['gas'])):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            out[model] = item
            print(f"{model}: {size} | {weight} | {item['gas']} | {mode}")
        except Exception as e:
            errors += 1
            error_models.append(model)
            print(f'{model}: ERR {str(e)[:50]}')
        time.sleep(0.2)
    emit_fetch_receipt('fetch_general.py', attempted, attempted - errors, errors,
                       succeeded_models=list(out.keys()), failed_models=error_models)
    if batch_failed(attempted, errors):
        print(f'❌ GENERAL {errors}/{attempted} 個目標失敗，唔覆寫現有快照，留待下次重試', file=sys.stderr)
        sys.exit(1)
    out_path = os.path.join(base, 'general_official.json')
    if not out and os.path.exists(out_path):
        print('ℹ️ GENERAL 冇任何目標資料，保留現有快照', file=sys.stderr)
        return
    save_json(out_path, out, indent=1)
    print('完成', len(out))


if __name__ == '__main__':
    main()
