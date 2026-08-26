#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Feature Check（文檔 §9.2）：功能註冊表結構驗證 + 測試綁定檢查

- 提取 Registry 區塊並按 Schema 做結構驗證（ID 格式、enum、必需欄位）
- 檢查 required 功能嘅 testBindings 非空（綁定缺口必須報告）
- 綁定測試檔案必須存在（E1 靜態證據；實際執行由 pytest 負責）
用法：
  python scripts/feature-check.py
退出碼：0 = 通過；1 = 有缺口或結構錯誤
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_governance import extract_blocks, BlockError, GOV_FILE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CATEGORIES = {'core', 'data', 'ui', 'report', 'operations'}
PRIORITIES = {'P0', 'P1', 'P2'}
PROTECTIONS = {'required', 'optional', 'deprecated', 'removed'}
ID_RE = re.compile(r'^[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)+$')


def check_registry_schema(reg):
    errors = []
    if reg.get('blockId') != 'AIRCON_FEATURE_REGISTRY_V1':
        errors.append('blockId 唔正確')
    if reg.get('schemaVersion') != '1.0.0':
        errors.append('schemaVersion 唔正確')
    for key in ('statusSemantics', 'features'):
        if key not in reg:
            errors.append(f'缺少 {key}')
    feats = reg.get('features', [])
    if not isinstance(feats, list) or not feats:
        errors.append('features 必須係非空陣列')
    seen = set()
    for f in feats:
        fid = f.get('id')
        if not fid or not ID_RE.match(fid):
            errors.append(f'非法 id：{fid!r}')
        if fid in seen:
            errors.append(f'重複 id：{fid}')
        seen.add(fid)
        if f.get('category') not in CATEGORIES:
            errors.append(f'{fid}：非法 category {f.get("category")!r}')
        if f.get('priority') not in PRIORITIES:
            errors.append(f'{fid}：非法 priority')
        if f.get('protection') not in PROTECTIONS:
            errors.append(f'{fid}：非法 protection')
        for k in ('name', 'aliases', 'testContract', 'evidenceRequired', 'testBindings'):
            if k not in f:
                errors.append(f'{fid}：缺少欄位 {k}')
        if not isinstance(f.get('evidenceRequired', []), list) or not f['evidenceRequired']:
            errors.append(f'{fid}：evidenceRequired 必須非空')
    return errors


# 已知測試綁定缺口（未實現功能，等人類決定：實現或降級；治理文檔 §5.2 唔得偽造測試名）
KNOWN_UNBOUND = {'report.pdf-export', 'core.ranking', 'core.recommendation'}


def check_test_bindings(reg):
    """required 功能必須綁定實際測試（已知缺口除外）；綁定檔案必須存在"""
    missing, missing_files = [], []
    for f in reg.get('features', []):
        if f.get('protection') != 'required':
            continue
        if f['id'] in KNOWN_UNBOUND:
            continue
        binds = f.get('testBindings', [])
        if not binds:
            missing.append(f['id'])
            continue
        for b in binds:
            # 支援 pytest node id（file::test）：只檢查檔案部分存在
            file_part = b.split('::')[0]
            p = os.path.join(BASE, file_part)
            if not os.path.exists(p):
                missing_files.append(f"{f['id']} → {b}")
    return missing, missing_files


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    with open(GOV_FILE, encoding='utf-8') as fh:
        text = fh.read()
    try:
        blocks = extract_blocks(text)
    except BlockError as e:
        print(f'❌ {e}', file=sys.stderr)
        return 1
    reg = blocks['AIRCON_FEATURE_REGISTRY_V1']

    errors = check_registry_schema(reg)
    if errors:
        print('❌ Registry Schema 驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1

    missing, missing_files = check_test_bindings(reg)
    total = len(reg['features'])
    required = [f for f in reg['features'] if f['protection'] == 'required']
    print(f'✅ Registry 結構有效：{total} 項功能（required {len(required)} 項）')
    if KNOWN_UNBOUND:
        print(f'⚠️ 已知測試綁定缺口（等人類決定實現或降級）：{", ".join(sorted(KNOWN_UNBOUND))}',
              file=sys.stderr)
    if missing:
        print(f'❌ 未申報嘅測試綁定缺口（不得偽造測試名）：{", ".join(missing)}', file=sys.stderr)
        return 1
    if missing_files:
        print('❌ 綁定檔案唔存在：', file=sys.stderr)
        for m in missing_files:
            print('  -', m, file=sys.stderr)
        return 1
    print('✅ required 功能測試綁定完整（已知缺口已申報），綁定檔案全部存在')
    return 0


if __name__ == '__main__':
    sys.exit(main())
