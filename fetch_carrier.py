#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世紀開利官網 century-carrier.com 窗口機規格抓取（Carrier + Canopus 肯特）"""
import json, re, ssl, sys, io, os, urllib.request, urllib.error, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0 (compatible; AirconCompareBot/1.0; +https://github.com/CalvinLau1012/aircon-compare) AppleWebKit/537.36 Chrome/148.0 Safari/537.36','Accept-Language':'zh-HK,zh;q=0.9'})
    last = None
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429) and attempt < 2:
                time.sleep(int(e.headers.get('Retry-After') or 0) or 10)
            elif e.code in (403, 429):
                break
        except Exception as e:
            last = e
            time.sleep(2)
    raise last
def txt(html):
    t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    t = re.sub(r'<[^>]+>', '\n', t)
    lines = [l.strip() for l in t.split('\n') if l.strip()]
    return '\n'.join(lines), lines

urls = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'carrier_urls.json'), encoding='utf-8'))
out = {}
for url in urls:
    try:
        h = get(url)
        t, lines = txt(h)
        m = re.search(r'\b((?:CHK|CKM|TA|CAK|CAR)[A-Z0-9]*)\b', t)
        model = m.group(1) if m else 'UNK'
        # 標題行（型號後第一行）
        hp = btu = energy = ''
        for i, l in enumerate(lines):
            if l == model and i + 5 < len(lines):
                nxt = lines[i+1]
                mhp = re.search(r'([\d./]+\s*匹)', nxt)
                hp = mhp.group(1) if mhp else ''
                mbtu = re.search(r'([\d,]+)\s*BTU', t[i:i+2000] if False else t)
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
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'carrier_official.json'),'w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('完成', len(out))
