#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信興官網 shew.com.hk Rasonic/FROSTAR/Panasonic 窗口機規格抓取"""
import json, re, sys, time, os
from crawl_utils import fetch, html_to_text, no_verify_ssl_context, batch_failed, save_json, emit_fetch_receipt
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
    attempted = errors = 0
    error_models = []
    for url in urls:
        attempted += 1
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
            if not (size or weight or warranty):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            out[model] = item
            print(f"  {model}: {size} | {weight} | {warranty} | {mode} | wifi={item['wifi']}")
        except Exception as e:
            errors += 1
            error_models.append(model)
            print(f'  {model}: ERR {str(e)[:40]}')
        time.sleep(0.15)
    emit_fetch_receipt('fetch_shew.py', attempted, attempted - errors, errors,
                       succeeded_models=list(out.keys()), failed_models=error_models)
    if batch_failed(attempted, errors):
        print(f'❌ shew {errors}/{attempted} 個目標失敗，唔覆寫現有快照，留待下次重試', file=sys.stderr)
        sys.exit(1)
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shew_official.json')
    if not out and os.path.exists(out_path):
        print('ℹ️ shew 冇任何目標資料，保留現有快照', file=sys.stderr)
        return
    save_json(out_path, out, indent=1)
    print('完成', len(out), '型號')


if __name__ == '__main__':
    main()
