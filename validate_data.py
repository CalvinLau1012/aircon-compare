#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
數據驗證閘門（自動更新用）
任何一項唔合格 → 非零退出 → 工作流失敗 → 唔會提交/推送（保住現有穩定數據）
"""
import csv
import json
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.dirname(os.path.abspath(__file__))
errors = []


def check(name, value, low, high):
    if not (low <= value <= high):
        errors.append(f'{name} 超出安全範圍：{value}（期望 {low}–{high}）')
    else:
        print(f'✅ {name}: {value}')


def load_json(name):
    p = os.path.join(BASE, name)
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        errors.append(f'{name} 無法讀取/解析：{e}')
        return None


# 1) EMSD CSV（基準 1,927 行）
try:
    with open(os.path.join(BASE, 'emsd_空調能源標籤.csv'), encoding='utf-8-sig') as f:
        rows = sum(1 for _ in csv.reader(f)) - 1
    check('EMSD CSV 行數', rows, 1700, 2200)
except Exception as e:
    errors.append(f'EMSD CSV 讀取失敗：{e}')

# 2) prices.json（基準 1,847；低過 1,700 代表抓取大規模失敗）
p = load_json('prices.json')
if isinstance(p, dict):
    priced = sum(1 for v in p.values() if isinstance(v, dict) and v.get('price'))
    check('Price 有價型號數', priced, 1600, 2100)

# 3) PricesAPI 快照（有檔案先驗證；首批可能只覆蓋核心/部分型號）
papi_path = os.path.join(BASE, 'pricesapi_prices.json')
if os.path.exists(papi_path):
    pa = load_json('pricesapi_prices.json')
    if isinstance(pa, dict):
        pa_priced = sum(1 for v in pa.values() if isinstance(v, dict) and v.get('price'))
        check('PricesAPI 有價型號數', pa_priced, 1, 2000)

# 4) specs_emsd.json（基準 1,757）
s = load_json('specs_emsd.json')
if isinstance(s, dict):
    check('specs_emsd 條目數', len(s), 1600, 2100)

# 5) 核心/官網 JSON 唔可以變空
for name in ('specs.json', 'official_specs.json', 'rasonic_official.json',
             'shew_official.json', 'pana_official.json', 'carrier_official.json',
             'general_official.json', 'midea_official.json'):
    d = load_json(name)
    if d is not None and len(d) == 0:
        errors.append(f'{name} 為空，拒絕更新')

if errors:
    print('❌ 驗證失敗：')
    for e in errors:
        print('  -', e)
    sys.exit(1)

print('🎉 全部數據驗證通過，可以安全更新')
