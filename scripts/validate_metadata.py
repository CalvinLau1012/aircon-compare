#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
驗證 deployment metadata.json（文檔 §7.2：按內嵌 METADATA_SCHEMA_V1 驗證）

- required 欄位清單由治理文檔 Schema 區塊動態讀取（避免漂移）
- 完整 Draft 2020-12 + FormatChecker（含 const / minimum / 日期合法性）
- rollback 部署必須有 rollbackOfBuild
用法：
  python scripts/validate_metadata.py [metadata.json 路徑]
  python scripts/validate_metadata.py --core <metadata.core.json>   # 只驗核心事實（未 finalize）
退出碼：0 = 通過；1 = 失敗
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_governance import extract_blocks, BlockError, GOV_FILE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(BASE, 'metadata.json')


def validate(meta, schema):
    """Full Draft 2020-12, including format and conditional constraints.

    保持函數介面：回傳可讀錯誤字串清單（空清單 = 通過）。root 唔係 object 都會
    由 Schema 嘅 `type: object` 拒絕，唔會拋 traceback。
    """
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [f"{'.'.join(map(str, e.absolute_path)) or '$'}: {e.message}"
            for e in sorted(validator.iter_errors(meta), key=lambda e: str(e.absolute_path))]


def validate_core(meta, schema):
    """核心事實驗證：正式 Schema 拒絕無 releasePayloadHash；core 用 placeholder
    hash 驗其餘全部約束，確保未 finalize 嘅核心事實仍然 fail-closed。"""
    probe = dict(meta) if isinstance(meta, dict) else meta
    if isinstance(probe, dict) and 'releasePayloadHash' not in probe:
        probe['releasePayloadHash'] = 'sha256:' + '0' * 64
    return validate(probe, schema)


def _load_schema():
    with open(GOV_FILE, encoding='utf-8') as f:
        text = f.read()
    try:
        blocks = extract_blocks(text)
    except BlockError as e:
        print(f'❌ 治理區塊失敗：{e}', file=sys.stderr)
        return None
    return blocks['AIRCON_METADATA_SCHEMA_V1']


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='驗證 metadata.json（完整 Draft 2020-12）')
    ap.add_argument('path', nargs='?', default=DEFAULT_PATH, help='metadata.json 路徑')
    ap.add_argument('--core', action='store_true',
                    help='驗證未 finalize 嘅 metadata core（核心事實齊全，但不可部署）')
    args = ap.parse_args(argv)

    path = args.path
    if not os.path.exists(path):
        print(f'❌ 搵唔到 {path}', file=sys.stderr)
        return 1
    schema = _load_schema()
    if schema is None:
        return 1
    try:
        with open(path, encoding='utf-8') as f:
            meta = json.load(f)
    except json.JSONDecodeError as e:
        print(f'❌ {path} 唔係有效 JSON：{e}', file=sys.stderr)
        return 1
    except OSError as e:
        print(f'❌ 讀唔到 {path}：{e}', file=sys.stderr)
        return 1
    errors = validate_core(meta, schema) if args.core else validate(meta, schema)
    if errors:
        kind = 'metadata core' if args.core else 'metadata.json'
        print(f'❌ {kind} 驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1
    if args.core:
        print(f"✅ 核心事實有效（未 finalize，不可部署，仍缺 releasePayloadHash）："
              f"version={meta.get('version')} deployTime={meta.get('deployTime')} "
              f"datasetDate={meta.get('datasetDate')}")
    else:
        print(f"✅ metadata.json 有效：version={meta.get('version')} "
              f"deployTime={meta.get('deployTime')} datasetDate={meta.get('datasetDate')}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
