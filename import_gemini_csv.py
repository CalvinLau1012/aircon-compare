# -*- coding: utf-8 -*-
"""將 Gemini 網頁版返回嘅 CSV 整合為 gemini_prices.json（格式同 biggo_prices.json 一致）

用法：
  1. 將 Gemini 回覆嘅 CSV 儲存為 gemini_result.csv（同目錄）
  2. python import_gemini_csv.py
  3. 會生成/合併 gemini_prices.json，並重新生成網頁
"""
import csv
import io
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE, 'gemini_prices.json')


def norm_model(s):
    return re.sub(r'[^A-Z0-9]', '', str(s).upper())


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    src = os.path.join(BASE, 'gemini_result.csv')
    if not os.path.exists(src):
        print('❌ 搵唔到 gemini_result.csv——請先將 Gemini 回覆嘅 CSV 儲存做呢個檔名')
        return

    # 讀 Gemini 返回 CSV（可能帶 markdown 碼塊 ```csv ... ```，自動清洗）
    raw = io.open(src, encoding='utf-8-sig', errors='replace').read()
    raw = re.sub(r'^```(?:csv)?\s*|\s*```$', '', raw, flags=re.M)
    rows = list(csv.reader(io.StringIO(raw)))
    header = [c.strip().lower() for c in rows[0]] if rows else []

    # 現有 gemini_prices.json
    out = {}
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding='utf-8') as f:
            out = json.load(f)

    ok = skip = 0
    for r in rows[1:]:
        if len(r) < 3 or not r[0].strip():
            continue
        rec = dict(zip(header, r))
        model = (rec.get('model') or '').strip()
        low = (rec.get('price_low') or '').strip().replace(',', '')
        high = (rec.get('price_high') or '').strip().replace(',', '')
        source = (rec.get('source') or '').strip()
        if not model:
            continue
        if not low.isdigit() or not high.isdigit():
            # 留空（搜唔到）→ 唔寫入，保留舊數據
            skip += 1
            continue
        lo, hi = int(low), int(high)
        if lo > hi:
            lo, hi = hi, lo
        price = f'${lo:,}-{hi:,}' if hi > lo else f'${lo:,}起'
        out[model] = {
            'price': price,
            'merchants': None,
            'url': 'https://www.google.com/search?q=' + model,
            'source': source or 'Gemini AI 搜索',
            'ai': True,
            'updated': time.strftime('%Y-%m-%d'),
        }
        ok += 1

    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False)
    print(f'✅ 整合完成：新增 {ok} 個 AI 搜索價 · 跳過 {skip} 個（留空/無資料）· 總共 {len(out)} 個')
    print(f'存於 {OUT_PATH}')
    print('提示：之後跑 python generate_html.py 就會套用（BigGo > Gemini AI > Price 舊快照）')


if __name__ == '__main__':
    main()
