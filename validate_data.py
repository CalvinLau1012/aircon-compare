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

# 3) BigGo 主力價錢快照（基準 731；低過 500 代表批次大規模失敗）
bg = load_json('biggo_prices.json')
if isinstance(bg, dict):
    bg_priced = sum(1 for v in bg.values() if isinstance(v, dict) and v.get('price'))
    check('BigGo 有價型號數', bg_priced, 500, 900)

# 4) PricesAPI 核心 29 驗收快照（有檔案先驗證）
papi_path = os.path.join(BASE, 'pricesapi_prices.json')
if os.path.exists(papi_path):
    pa = load_json('pricesapi_prices.json')
    if isinstance(pa, dict):
        pa_priced = sum(1 for v in pa.values() if isinstance(v, dict) and v.get('price'))
        check('PricesAPI 核心驗收有價數', pa_priced, 1, 40)

# 5) prices_meta.json 批次狀態一致性
meta = load_json('prices_meta.json')
if isinstance(meta, dict):
    idx = meta.get('price_batch_idx')
    active = meta.get('price_batch_start')
    if active:
        if not isinstance(idx, int) or not 0 <= idx <= 6:
            errors.append(f'價錢批次 idx 異常：{idx}（active={active}）')
        if idx >= 7:
            errors.append(f'價錢批次已完成但未清理 price_batch_start（idx={idx}）')
    check('prices_meta 可讀取', 1, 1, 1)

# 6) specs_emsd.json（基準 1,757）
s = load_json('specs_emsd.json')
if isinstance(s, dict):
    check('specs_emsd 條目數', len(s), 1600, 2100)

# 7) 淘汰黑名單 / 追蹤檔一致性
bl = load_json('model_blacklist.json')
if bl is not None:
    if not isinstance(bl, dict) or not isinstance(bl.get('models', {}), dict):
        errors.append('model_blacklist.json 格式異常')
    else:
        check('型號黑名單數', len(bl.get('models', {})), 0, 400)
tr = load_json('model_status.json')
if tr is not None and not isinstance(tr, dict):
    errors.append('model_status.json 格式異常')

# 8) 核心/官網 JSON 唔可以變空
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
