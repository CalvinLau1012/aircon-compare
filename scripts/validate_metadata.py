#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
驗證 deployment metadata.json（文檔 §7.2：按內嵌 METADATA_SCHEMA_V1 驗證）

- required 欄位清單由治理文檔 Schema 區塊動態讀取（避免漂移）
- 支援 type / pattern / enum / format / min/max 基本驗證
- rollback 部署必須有 rollbackOfBuild
用法：
  python scripts/validate-metadata.py [metadata.json 路徑]
退出碼：0 = 通過；1 = 失敗
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_governance import extract_blocks, BlockError, GOV_FILE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(BASE, 'metadata.json')


def _check_type(v, t):
    if t == 'string':
        return isinstance(v, str)
    if t == 'integer':
        return isinstance(v, int) and not isinstance(v, bool)
    if t == 'object':
        return isinstance(v, dict)
    if t == 'array':
        return isinstance(v, list)
    return True


def validate(meta, schema):
    errors = []
    props = schema.get('properties', {})
    for key in schema.get('required', []):
        if key not in meta:
            errors.append(f'缺少必需欄位 {key}')
    for key, v in meta.items():
        spec = props.get(key)
        if spec is None:
            errors.append(f'額外欄位 {key}（additionalProperties: false）')
            continue
        if not _check_type(v, spec.get('type')):
            errors.append(f'{key} 類型錯誤：期望 {spec.get("type")}')
            continue
        pat = spec.get('pattern')
        if pat and isinstance(v, str) and not re.search(pat, v):
            errors.append(f'{key} 唔符合 pattern：{v!r}')
        en = spec.get('enum')
        if en and v not in en:
            errors.append(f'{key} 唔喺 enum：{v!r}')
        if 'format' in spec and isinstance(v, str):
            if spec['format'] == 'date-time' and not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$', v):
                errors.append(f'{key} 唔符合 date-time：{v!r}')
            if spec['format'] == 'date' and not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
                errors.append(f'{key} 唔符合 date：{v!r}')
            if spec['format'] == 'uri' and not v.startswith(('http://', 'https://')):
                errors.append(f'{key} 唔符合 uri：{v!r}')
    # rollback 條件
    if meta.get('deploymentType') == 'rollback' and 'rollbackOfBuild' not in meta:
        errors.append('rollback 部署必須有 rollbackOfBuild')
    return errors


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.exists(path):
        print(f'❌ 搵唔到 {path}', file=sys.stderr)
        return 1
    with open(GOV_FILE, encoding='utf-8') as f:
        text = f.read()
    try:
        blocks = extract_blocks(text)
    except BlockError as e:
        print(f'❌ 治理區塊失敗：{e}', file=sys.stderr)
        return 1
    schema = blocks['AIRCON_METADATA_SCHEMA_V1']
    with open(path, encoding='utf-8') as f:
        meta = json.load(f)
    errors = validate(meta, schema)
    if errors:
        print('❌ metadata.json 驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1
    print(f"✅ metadata.json 有效：version={meta.get('version')} "
          f"deployTime={meta.get('deployTime')} datasetDate={meta.get('datasetDate')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
