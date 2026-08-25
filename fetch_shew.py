#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信興官網 shew.com.hk Rasonic/FROSTAR/Panasonic 窗口機規格抓取"""
import json, re, sys, time, os
from crawl_utils import fetch, html_to_text, no_verify_ssl_context
CTX = no_verify_ssl_context()
def get(u):
    return fetch(u, context=CTX)
def grab(t, kws, n=80):
    for kw in kws:
        i = t.find(kw)
        if i >= 0:
            return t[i:i+n]
    return ''

def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shew_urls.json'), encoding='utf-8') as f:
        urls = json.load(f)
    out = {}
    for url in urls:
        slug = url.rsplit('/', 1)[-1]
        model = slug.replace('.aspx', '').upper()
        try:
            h = get(url)
            t = html_to_text(h)
            m = re.search(r'體積\s*\(高\s*[xX×]\s*闊\s*[xX×]\s*深\)\s*:?\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*[xX×]\s*([\d.]+)', t) or re.search(r'([\d.]+)\s*[xX×]\s*([\d.]+)\s*[xX×]\s*([\d.]+)\s*毫米', t)
            size = f'{m.group(1)}\u00d7{m.group(2)}\u00d7{m.group(3)}' if m else ''
            mw = re.search(r'淨重\s*([\d.]+)\s*公斤', t)
            weight = mw.group(1) + 'kg' if mw else ''
            mwr = re.search(r'(\d+)\s*年全機保修[,\s]*(\d+)\s*年壓縮機保修', t)
            warranty = f'{mwr.group(1)}/{mwr.group(2)}年' if mwr else ''
            mode = '冷暖' if '冷暖' in t[:6000] else ('淨冷' if '淨冷' in t[:6000] else '')
            item = {'size': size, 'weight': weight, 'warranty': warranty, 'mode': mode,
                    'remote': '✅' if '無線遙控' in t or '遙控器' in t else '',
                    'wifi': '✅' if re.search(r'Wi-?Fi', t, re.I) else '',
                    'url': url}
            out[model] = item
            print(f"  {model}: {size} | {weight} | {warranty} | {mode} | wifi={item['wifi']}")
        except Exception as e:
            print(f'  {model}: ERR {str(e)[:40]}')
        time.sleep(0.15)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shew_official.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('完成', len(out), '型號')


if __name__ == '__main__':
    main()
