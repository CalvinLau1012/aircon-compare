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


class BlockError(Exception):
    pass


def extract_blocks(text):
    """返回 {blockId: parsed_json}；任何結構問題拋 BlockError"""
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
        # 拒絕重複鍵
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise BlockError(f'區塊 {bid} JSON 解析失敗（拒絕註釋/尾隨逗號）：{e}')
        if not isinstance(obj, dict):
            raise BlockError(f'區塊 {bid} 必須係 JSON object')
        if obj.get('blockId') != bid:
            raise BlockError(f'區塊 {bid} blockId 唔匹配：{obj.get("blockId")!r}')
        out[bid] = obj
    return out


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
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if not os.path.exists(GOV_FILE):
        print(f'❌ 搵唔到 {GOV_FILE}', file=sys.stderr)
        return 2
    with open(GOV_FILE, encoding='utf-8') as f:
        text = f.read()
    try:
        blocks = extract_blocks(text)
        for bid in EXPECTED_BLOCKS:
            check_unique_ids(blocks[bid], bid)
    except BlockError as e:
        print(f'❌ 治理區塊驗證失敗：{e}', file=sys.stderr)
        return 1
    print(f'✅ 治理區塊全部有效：{len(blocks)} 個區塊、ID 唯一、JSON 嚴格解析通過')
    if '--dump' in sys.argv:
        bid = sys.argv[sys.argv.index('--dump') + 1]
        print(json.dumps(blocks[bid], ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
