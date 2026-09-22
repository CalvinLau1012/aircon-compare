#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
數據驗證閘門（自動更新用）
任何一項唔合格 → 非零退出 → 工作流失敗 → 唔會提交/推送（保住現有穩定數據）

契約檢查（唔以真實 count 代替契約，亦唔發明資料範圍）：
- EMSD CSV：表頭簽名、每行 15 欄、品牌／型號必填、數值欄可轉 float、
  能源級別只准 1–5、供暖欄容許官方「不適用」sentinel、變頻欄只准 是／否；
- 登記／型號計數關係：型號 ⊆ 登記，類型正確；
- prices.json／specs_emsd.json／官網 JSON：頂層同每個 entry 類型正確；
- 核心快照唔可以變空。

用法：
  python validate_data.py [--base <dir>]
退出碼：0 = 通過；1 = 有錯。
"""
import argparse
import csv
import json
import os
import sys

BASE_DEFAULT = os.path.dirname(os.path.abspath(__file__))
SENTINELS = ('不適用', '不適用／不適用', '-', '—', 'N/A', 'n/a')
# 供暖相關欄位：可以有官方 sentinel；其餘核心數值欄必須可轉 float
NUMERIC_COLS = (5, 6, 7, 10, 11, 12)
SENTINEL_COLS = (10, 11, 12)
ENERGY_COLS = (4, 9)


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _is_sentinel(v):
    return v in SENTINELS


def validate(base=BASE_DEFAULT):
    errors = []

    def check(name, value, low, high):
        if not (low <= value <= high):
            errors.append(f'{name} 超出安全範圍：{value}（期望 {low}–{high}）')
        else:
            print(f'✅ {name}: {value}')

    def load_json(name):
        p = os.path.join(base, name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            errors.append(f'{name} 無法讀取/解析：{e}')
            return None

    # 1) EMSD CSV：形狀 + 欄位契約
    csv_path = os.path.join(base, 'emsd_空調能源標籤.csv')
    try:
        with open(csv_path, encoding='utf-8-sig') as f:
            rows = list(csv.reader(f))
        if not rows:
            errors.append('EMSD CSV 為空')
        else:
            header = rows[0]
            if len(header) != 15 or header[1].strip() != '型號':
                errors.append(f'EMSD CSV 表頭唔符契約（15 欄、第 2 欄型號）：{header[:3]}')
            dup_headers = [i for i, r in enumerate(rows)
                           if i > 0 and len(r) >= 2 and r[1].strip() == '型號']
            if dup_headers:
                errors.append(f'EMSD CSV 有重複表頭：行號 {dup_headers[:10]}')
            else:
                print('✅ EMSD CSV 表頭唯一')
            bad_len, missing, nonnum, bad_level, bad_freq = [], [], [], [], []
            for i, r in enumerate(rows[1:], start=2):
                if len(r) != 15:
                    bad_len.append(i)
                    continue
                if not r[0].strip() or not r[1].strip():
                    missing.append(i)
                for idx in NUMERIC_COLS:
                    v = r[idx].strip()
                    if not v:
                        continue
                    if idx in SENTINEL_COLS and _is_sentinel(v):
                        continue
                    if not _is_number(v):
                        nonnum.append([i, idx, v[:20]])
                for idx in ENERGY_COLS:
                    v = r[idx].strip()
                    if not v or _is_sentinel(v):
                        continue
                    if not (v.isdigit() and 1 <= int(v) <= 5):
                        bad_level.append([i, idx, v[:10]])
                freq = r[14].strip()
                if freq and freq not in ('是', '否'):
                    bad_freq.append([i, freq[:10]])
            if bad_len:
                errors.append(f'EMSD CSV 有行唔係 15 欄：行號 {bad_len[:10]}')
            if missing:
                errors.append(f'EMSD CSV 有行缺品牌／型號：行號 {missing[:10]}')
            if nonnum:
                errors.append(f'EMSD CSV 數值欄無法轉換：{nonnum[:5]}')
            if bad_level:
                errors.append(f'EMSD CSV 能源級別唔係 1–5：{bad_level[:5]}')
            if bad_freq:
                errors.append(f'EMSD CSV 變頻欄唔係 是／否：{bad_freq[:5]}')
            if not (bad_len or missing or nonnum or bad_level or bad_freq):
                print(f'✅ EMSD CSV 結構契約：{len(rows) - 1} 行全部符合')
            check('EMSD CSV 行數（減表頭）', len(rows) - 1, 1700, 2200)
    except Exception as e:  # noqa: BLE001
        errors.append(f'EMSD CSV 讀取失敗：{e}')

    sys.path.insert(0, base)
    try:
        from crawl_utils import load_registrations, load_models
        csvp = os.path.join(base, 'emsd_空調能源標籤.csv')
        regs = load_registrations(csvp)
        models = load_models(csvp)
        if not isinstance(regs, list) or not isinstance(models, list):
            errors.append('登記／型號載入結果型別唔正確（必須 list）')
        else:
            bad_reg = [r for r in regs if not (isinstance(r, tuple) and len(r) == 2
                                               and r[0] and r[1])]
            if bad_reg:
                errors.append(f'登記記錄形狀唔正確（示範 {bad_reg[:2]}）')
            check('EMSD 登記記錄數（registrationCount）', len(regs), 1700, 2200)
            check('EMSD 型號數（modelCount）', len(models), 1600, 2200)
            if len(models) > len(regs):
                errors.append(f'型號數 {len(models)} 唔應該多過登記數 {len(regs)}')
    except Exception as e:  # noqa: BLE001
        errors.append(f'EMSD 登記/型號計數失敗：{e}')

    # 2) prices.json（基準 1,847；低過 1,700 代表抓取大規模失敗）
    p = load_json('prices.json')
    if p is not None:
        if not isinstance(p, dict):
            errors.append(f'prices.json 頂層必須係 object（got {type(p).__name__}）')
        else:
            bad = [k for k, v in p.items()
                   if not isinstance(v, dict) or ('price' in v and not isinstance(v['price'], str))]
            if bad:
                errors.append(f'prices.json entry 型別錯（示範 {bad[:5]}）')
            priced = sum(1 for v in p.values() if isinstance(v, dict) and v.get('price'))
            check('Price 有價型號數', priced, 1600, 2100)

    # 3) specs_emsd.json（基準 1,757）
    s = load_json('specs_emsd.json')
    if s is not None:
        if not isinstance(s, dict):
            errors.append(f'specs_emsd.json 頂層必須係 object（got {type(s).__name__}）')
        else:
            bad = [k for k, v in s.items() if not isinstance(v, dict)]
            if bad:
                errors.append(f'specs_emsd.json entry 型別錯（示範 {bad[:5]}）')
            check('specs_emsd 條目數', len(s), 1600, 2100)

    # 4) 核心/官網 JSON：型別 + 唔可以變空
    for name in ('specs.json', 'official_specs.json', 'rasonic_official.json',
                 'shew_official.json', 'pana_official.json', 'carrier_official.json',
                 'general_official.json', 'midea_official.json'):
        d = load_json(name)
        if d is None:
            continue
        if not isinstance(d, dict):
            errors.append(f'{name} 頂層必須係 object（got {type(d).__name__}）')
            continue
        if len(d) == 0:
            errors.append(f'{name} 為空，拒絕更新')
        bad = [k for k, v in d.items() if not isinstance(v, dict)]
        if bad:
            errors.append(f'{name} entry 型別錯（示範 {bad[:5]}）')

    return errors


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='數據驗證閘門')
    ap.add_argument('--base', default=BASE_DEFAULT)
    args = ap.parse_args(argv)
    errors = validate(args.base)
    if errors:
        print('❌ 驗證失敗：')
        for e in errors:
            print('  -', e)
        return 1
    print('🎉 全部數據驗證通過，可以安全更新')
    return 0


if __name__ == '__main__':
    sys.exit(main())
