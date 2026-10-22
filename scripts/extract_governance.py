#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
治理規範區塊提取器（docs/AIRCON_COMPARE_GOVERNANCE.md → 驗證 JSON 區塊）

文檔 §1.2 機器區塊規則：
  1. 按區塊 ID 找到恰好一個 BEGIN 和一個 END
  2. 只接受二者之間恰好一個 ```json fenced block
  3. 嚴格 JSON 解析（拒絕註釋、尾隨逗號、重複鍵）
  4. 校驗區塊自身 blockId
  5. 任何缺失、重複、解析失敗都以非零狀態退出

用法：
  python scripts/extract-governance.py                 # 驗證全部區塊
  python scripts/extract-governance.py --dump <ID>     # 輸出某區塊 JSON
"""
import json
import re
import sys
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOV_FILE = os.path.join(BASE, 'docs', 'AIRCON_COMPARE_GOVERNANCE.md')

# 文檔 §1.2 定義嘅六個規範區塊
EXPECTED_BLOCKS = [
    'AIRCON_AI_CONTEXT_V1',
    'AIRCON_FEATURE_REGISTRY_SCHEMA_V1',
    'AIRCON_FEATURE_REGISTRY_V1',
    'AIRCON_METADATA_SCHEMA_V1',
    'AIRCON_SUCCESS_CRITERIA_SCHEMA_V1',
    'AIRCON_SUCCESS_CRITERIA_V1',
]
# Independent, explicitly versioned extensions.  They are NOT additional
# canonical blocks: extract_blocks() still returns exactly the six above.
SCHEMA_EXTENSIONS = ['AIRCON_METADATA_SCHEMA_V1_1', 'AIRCON_PRICE_STATUS_SCHEMA_V1']


class BlockError(Exception):
    pass


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise BlockError(f'JSON duplicate key: {key}')
        out[key] = value
    return out


def _reject_constant(name):
    """json.loads 預設接受 NaN／Infinity；治理 JSON 必須拒絕。"""
    raise BlockError(f'JSON 唔接受非標準常數：{name}')


def extract_blocks(text):
    """返回 {blockId: parsed_json}；任何結構問題拋 BlockError"""
    # 先全掃 marker：未知 ID、重複、嵌套／交錯、未配對一律阻斷
    marker_re = re.compile(r'<!--\s*AIRCON:NORMATIVE:([A-Z0-9_]+):(BEGIN|END)\s*-->')
    known = {bid[len('AIRCON_'):] if bid.startswith('AIRCON_') else bid for bid in EXPECTED_BLOCKS}
    stack = []
    for m in marker_re.finditer(text):
        marker_id, kind = m.group(1), m.group(2)
        if marker_id not in known:
            raise BlockError(f'未知治理 marker：{marker_id}:{kind}')
        if kind == 'BEGIN':
            if stack:
                raise BlockError(f'治理 marker 嵌套／交錯：{marker_id}:BEGIN 喺 {stack[-1]}:BEGIN 之內')
            stack.append(marker_id)
        else:
            if not stack or stack[-1] != marker_id:
                raise BlockError(f'治理 marker 未配對：{marker_id}:END')
            stack.pop()
    if stack:
        raise BlockError(f'治理 marker 未閉合：{stack[-1]}:BEGIN')
    # 非標準文字 marker（例如錯 prefix）唔容許静默略過
    for bad in re.finditer(r'<!--\s*AIRCON:NORMATIVE:[^>]*?\s*-->', text):
        if not marker_re.fullmatch(bad.group(0)):
            raise BlockError(f'未知／格式錯嘅治理 marker：{bad.group(0)[:80]}')

    out = {}
    for bid in EXPECTED_BLOCKS:
        # 文檔 marker 用嘅係 blockId 去掉 AIRCON_ 前綴（如 AI_CONTEXT_V1）
        marker_id = bid[len('AIRCON_'):] if bid.startswith('AIRCON_') else bid
        begin = f'<!-- AIRCON:NORMATIVE:{marker_id}:BEGIN -->'
        end = f'<!-- AIRCON:NORMATIVE:{marker_id}:END -->'
        b_idx = text.find(begin)
        e_idx = text.find(end)
        if b_idx == -1 or e_idx == -1:
            raise BlockError(f'區塊 {bid} 缺少 BEGIN 或 END 標記')
        if text.find(begin, b_idx + 1) != -1:
            raise BlockError(f'區塊 {bid} BEGIN 標記重複')
        if text.find(end, e_idx + 1) != -1:
            raise BlockError(f'區塊 {bid} END 標記重複')
        if e_idx < b_idx:
            raise BlockError(f'區塊 {bid} END 出現喺 BEGIN 之前')
        seg = text[b_idx + len(begin):e_idx]
        # 只接受恰好一個 ```json fenced block
        fences = re.findall(r'```json\s*', seg)
        closes = re.findall(r'^```\s*$', seg, re.M)
        if len(fences) != 1 or len(closes) != 1:
            raise BlockError(f'區塊 {bid} 必須包含恰好一個 ```json 代碼塊')
        m = re.search(r'```json\s*\n(.*?)\n```\s*$', seg, re.S)
        if not m:
            raise BlockError(f'區塊 {bid} JSON 代碼塊格式不正確')
        raw = m.group(1)
        # 拒絕重複鍵同 NaN／Infinity
        try:
            obj = json.loads(raw, object_pairs_hook=_unique_pairs,
                             parse_constant=_reject_constant)
        except json.JSONDecodeError as e:
            raise BlockError(f'區塊 {bid} JSON 解析失敗（拒絕註釋/尾隨逗號）：{e}')
        if not isinstance(obj, dict):
            raise BlockError(f'區塊 {bid} 必須係 JSON object')
        if obj.get('blockId') != bid:
            raise BlockError(f'區塊 {bid} blockId 唔匹配：{obj.get("blockId")!r}')
        out[bid] = obj
    return out


def _parse_json_fence(seg, label):
    fences = re.findall(r'```json\s*', seg)
    closes = re.findall(r'^```\s*$', seg, re.M)
    if len(fences) != 1 or len(closes) != 1:
        raise BlockError(f'{label} 必須包含恰好一個 ```json 代碼塊')
    m = re.search(r'```json\s*\n(.*?)\n```\s*$', seg, re.S)
    if not m:
        raise BlockError(f'{label} JSON 代碼塊格式不正確')
    try:
        return json.loads(m.group(1), object_pairs_hook=_unique_pairs,
                          parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise BlockError(f'{label} JSON 解析失敗：{e}')


def extract_schema_extensions(text):
    """Return explicitly versioned schema extensions (never silently last block)."""
    marker_re = re.compile(
        r'<!--\s*AIRCON:SCHEMA_EXTENSION:([A-Z0-9_]+):(BEGIN|END)\s*-->')
    known = {bid[len('AIRCON_'):] if bid.startswith('AIRCON_') else bid
             for bid in SCHEMA_EXTENSIONS}
    stack = []
    for m in marker_re.finditer(text):
        marker_id, kind = m.group(1), m.group(2)
        if marker_id not in known:
            raise BlockError(f'未知 schema extension marker：{marker_id}:{kind}')
        if kind == 'BEGIN':
            if stack:
                raise BlockError(f'schema extension marker 嵌套／交錯：{marker_id}')
            stack.append(marker_id)
        else:
            if not stack or stack[-1] != marker_id:
                raise BlockError(f'schema extension marker 未配對：{marker_id}:END')
            stack.pop()
    if stack:
        raise BlockError(f'schema extension marker 未閉合：{stack[-1]}')
    out = {}
    for bid in SCHEMA_EXTENSIONS:
        marker_id = bid[len('AIRCON_'):] if bid.startswith('AIRCON_') else bid
        begin = f'<!-- AIRCON:SCHEMA_EXTENSION:{marker_id}:BEGIN -->'
        end = f'<!-- AIRCON:SCHEMA_EXTENSION:{marker_id}:END -->'
        b_idx = text.find(begin); e_idx = text.find(end)
        if b_idx == -1 or e_idx == -1:
            continue
        if text.find(begin, b_idx + 1) != -1 or text.find(end, e_idx + 1) != -1:
            raise BlockError(f'schema extension {bid} marker 重複')
        if e_idx < b_idx:
            raise BlockError(f'schema extension {bid} END 先於 BEGIN')
        seg = text[b_idx + len(begin):e_idx]
        obj = _parse_json_fence(seg, f'schema extension {bid}')
        if not isinstance(obj, dict) or obj.get('blockId') != bid:
            raise BlockError(f'schema extension {bid} blockId 唔匹配')
        if obj.get('supersedes') not in EXPECTED_BLOCKS:
            raise BlockError(f'schema extension {bid} supersedes 唔係已知 canonical block')
        if not isinstance(obj.get('schema'), dict):
            raise BlockError(f'schema extension {bid} 缺少 schema object')
        out[bid] = obj
    return out


def metadata_schema(text=None):
    """Explicitly select the effective metadata schema.

    Rule: use the extension only when its blockId AND supersedes both match the
    canonical metadata block.  No "last block wins" fallback.  If no extension
    exists, use the original canonical schema unchanged.
    """
    if text is None:
        with open(GOV_FILE, encoding='utf-8') as f:
            text = f.read()
    blocks = extract_blocks(text)
    exts = extract_schema_extensions(text)
    ext = exts.get('AIRCON_METADATA_SCHEMA_V1_1')
    if ext is not None:
        if ext.get('supersedes') != 'AIRCON_METADATA_SCHEMA_V1':
            raise BlockError('metadata schema extension 唔係取代 AIRCON_METADATA_SCHEMA_V1')
        schema = ext['schema']
        if schema.get('$id') != 'urn:aircon-compare:metadata-schema:1.0.0+source-status.1':
            raise BlockError('metadata schema extension $id 唔符明確版本')
        return schema
    return blocks['AIRCON_METADATA_SCHEMA_V1']


def check_unique_ids(obj, path=''):
    """檢查 Registry / Success Criteria 內部 ID 唯一性（只對 list 做）"""
    if isinstance(obj, list) and path.endswith(('.features', '.criteria')):
        ids = [x.get('id') for x in obj if isinstance(x, dict)]
        if len(ids) != len(set(ids)):
            raise BlockError(f'{path} 存在重複 ID')
    if isinstance(obj, dict):
        for k, v in obj.items():
            check_unique_ids(v, f'{path}.{k}' if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            check_unique_ids(v, f'{path}[{i}]')


def main():
    # Windows 控制台編碼保護
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    dump_id = None
    if '--dump' in sys.argv:
        idx = sys.argv.index('--dump')
        if idx + 1 >= len(sys.argv):
            print('❌ --dump 需要區塊 ID，例如 --dump AIRCON_FEATURE_REGISTRY_V1', file=sys.stderr)
            return 2
        dump_id = sys.argv[idx + 1]
    if not os.path.exists(GOV_FILE):
        print(f'❌ 搵唔到 {GOV_FILE}', file=sys.stderr)
        return 2
    with open(GOV_FILE, encoding='utf-8') as f:
        text = f.read()
    try:
        blocks = extract_blocks(text)
        from validate_metadata import validate
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
        # 內嵌 Schema 自身必須係合法 Draft 2020-12 Schema
        for schema_id in ('AIRCON_FEATURE_REGISTRY_SCHEMA_V1',
                          'AIRCON_SUCCESS_CRITERIA_SCHEMA_V1',
                          'AIRCON_METADATA_SCHEMA_V1'):
            Draft202012Validator.check_schema(blocks[schema_id])
        extensions = extract_schema_extensions(text)
        for ext in extensions.values():
            Draft202012Validator.check_schema(ext['schema'])
        effective_metadata = metadata_schema(text)
        if effective_metadata.get('$id') != 'urn:aircon-compare:metadata-schema:1.0.0+source-status.1':
            raise BlockError('effective metadata schema 版本唔符')
        # Registry / Success Criteria 真實驗證（完整 Schema，含 const/enum/pattern/minimum）
        for instance, schema in (
            ('AIRCON_FEATURE_REGISTRY_V1', 'AIRCON_FEATURE_REGISTRY_SCHEMA_V1'),
            ('AIRCON_SUCCESS_CRITERIA_V1', 'AIRCON_SUCCESS_CRITERIA_SCHEMA_V1'),
        ):
            errors = validate(blocks[instance], blocks[schema])
            if errors:
                raise BlockError('; '.join(errors))
        for bid in EXPECTED_BLOCKS:
            check_unique_ids(blocks[bid], bid)
    except (BlockError, SchemaError) as e:
        print(f'❌ 治理區塊驗證失敗：{e}', file=sys.stderr)
        return 1
    ext_count = len(extract_schema_extensions(text))
    print(f'✅ 治理區塊全部有效：{len(blocks)} 個 canonical 區塊 + {ext_count} 個明確版本 extension；'
          f'ID 唯一、JSON 嚴格解析、Schema 完整驗證通過')
    if dump_id is not None:
        dump_exts = extract_schema_extensions(text)
        if dump_id in blocks:
            print(json.dumps(blocks[dump_id], ensure_ascii=False, indent=2))
        elif dump_id in dump_exts:
            print(json.dumps(dump_exts[dump_id], ensure_ascii=False, indent=2))
        else:
            print(f'❌ 冇區塊 {dump_id}（可用：{", ".join(EXPECTED_BLOCKS + SCHEMA_EXTENSIONS)}）',
                  file=sys.stderr)
            return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
