#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世紀開利官網 century-carrier.com 窗口機規格抓取（Carrier + Canopus 肯特）"""
import json, re, sys, os, time
from crawl_utils import fetch, html_to_text, no_verify_ssl_context, batch_failed, save_json, emit_fetch_receipt

CTX = no_verify_ssl_context()


def get(u):
    return fetch(u, context=CTX)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, 'carrier_urls.json'), encoding='utf-8') as f:
        urls = json.load(f)
    out = {}
    attempted = errors = 0
    error_models = []
    for url in urls:
        attempted += 1
        model_key = None
        try:
            h = get(url)
            t = html_to_text(h, keep_lines=True)
            lines = t.split('\n')
            m = re.search(r'\b((?:CHK|CKM|TA|CAK|CAR)[A-Z0-9]*)\b', t)
            if not m:
                raise ValueError('搵唔到有效型號（可能係空白／登入／錯誤頁）')
            model = m.group(1)
            model_key = model
            # 標題行（型號後第一行）
            hp = btu = energy = ''
            for i, l in enumerate(lines):
                if l == model and i + 5 < len(lines):
                    nxt = lines[i + 1]
                    mhp = re.search(r'([\d./]+\s*匹)', nxt)
                    hp = mhp.group(1) if mhp else ''
                    break
            mbtu = re.search(r'([\d,]+)\s*BTU', t)
            btu = mbtu.group(1) if mbtu else ''
            mene = re.search(r'(\d)\s*級能源標籤', t)
            energy = mene.group(1) + '級' if mene else ''
            wifi = '✅' if re.search(r'Wi-?Fi', t, re.I) else ''
            mwr = re.search(r'(\d+)\s*年全機[^0-9]*(\d+)\s*年壓縮機', t)
            warranty = f'{mwr.group(1)}/{mwr.group(2)}年' if mwr else ''
            mode = '冷暖' if '冷暖' in t[:15000] else ('淨冷' if '淨冷' in t[:15000] else '')
            gas = 'R32' if 'R32' in t[:15000] else ('R410A' if 'R410A' in t[:15000] else '')
            item = {'hp': hp, 'btu': btu, 'energy': energy, 'wifi': wifi,
                    'warranty': warranty, 'mode': mode, 'gas': gas, 'url': url}
            if not any((hp, btu, energy, warranty, mode, gas, wifi)):
                raise ValueError('冇有效規格（可能係空白／登入／錯誤頁）')
            out[model] = item
            print(f"{model}: {hp} {btu}BTU {energy} {gas} wifi={wifi or '-'} {warranty}")
        except Exception as e:
            errors += 1
            error_models.append(model_key or f'URL:{url}')
            print(f'ERR {url[-20:]}: {str(e)[:40]}')
        time.sleep(0.15)
    emit_fetch_receipt('fetch_carrier.py', attempted, attempted - errors, errors,
                       succeeded_models=list(out.keys()), failed_models=error_models)
    if batch_failed(attempted, errors):
        print(f'❌ Carrier {errors}/{attempted} 個目標失敗，唔覆寫現有快照，留待下次重試', file=sys.stderr)
        sys.exit(1)
    out_path = os.path.join(base, 'carrier_official.json')
    if not out and os.path.exists(out_path):
        print('ℹ️ Carrier 冇任何目標資料，保留現有快照', file=sys.stderr)
        return
    save_json(out_path, out, indent=1)
    print('完成', len(out))


if __name__ == '__main__':
    main()
