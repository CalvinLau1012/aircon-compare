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


# 1) EMSD CSV：無重複表頭 + 登記數／型號數（D12）
try:
    with open(os.path.join(BASE, 'emsd_空調能源標籤.csv'), encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    dup_headers = [i for i, r in enumerate(rows) if i > 0 and len(r) >= 2 and r[1].strip() == '型號']
    if dup_headers:
        errors.append(f'EMSD CSV 有重複表頭：行號 {dup_headers[:10]}')
    else:
        print('✅ EMSD CSV 表頭唯一')
    check('EMSD CSV 行數（減表頭）', len(rows) - 1, 1700, 2200)
except Exception as e:
    errors.append(f'EMSD CSV 讀取失敗：{e}')

sys.path.insert(0, BASE)
try:
    from crawl_utils import load_registrations, load_models
    regs = load_registrations()
    models = load_models()
    check('EMSD 登記記錄數（registrationCount）', len(regs), 1700, 2200)
    check('EMSD 型號數（modelCount）', len(models), 1600, 2200)
    if len(models) > len(regs):
        errors.append(f'型號數 {len(models)} 唔應該多過登記數 {len(regs)}')
except Exception as e:
    errors.append(f'EMSD 登記/型號計數失敗：{e}')

# 2) prices.json（基準 1,847；低過 1,700 代表抓取大規模失敗）
p = load_json('prices.json')
if isinstance(p, dict):
    priced = sum(1 for v in p.values() if isinstance(v, dict) and v.get('price'))
    check('Price 有價型號數', priced, 1600, 2100)

# 3) specs_emsd.json（基準 1,757）
s = load_json('specs_emsd.json')
if isinstance(s, dict):
    check('specs_emsd 條目數', len(s), 1600, 2100)

# 4) 核心/官網 JSON 唔可以變空
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
