#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世紀開利官網 century-carrier.com 窗口機規格抓取（Carrier + Canopus 肯特）"""
import json, re, sys, os, time
from crawl_utils import fetch, html_to_text, no_verify_ssl_context

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
    for url in urls:
        try:
            h = get(url)
            t = html_to_text(h, keep_lines=True)
            lines = t.split('\n')
            m = re.search(r'\b((?:CHK|CKM|TA|CAK|CAR)[A-Z0-9]*)\b', t)
            model = m.group(1) if m else 'UNK'
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
            out[model] = item
            print(f"{model}: {hp} {btu}BTU {energy} {gas} wifi={wifi or '-'} {warranty}")
        except Exception as e:
            print(f'ERR {url[-20:]}: {str(e)[:40]}')
        time.sleep(0.15)
    with open(os.path.join(base, 'carrier_official.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print('完成', len(out))


if __name__ == '__main__':
    main()
