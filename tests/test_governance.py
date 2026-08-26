# -*- coding: utf-8 -*-
"""治理落地測試（AIRCON_COMPARE_GOVERNANCE.md）

- 六個規範區塊可提取、嚴格 JSON 解析、ID 唯一
- Registry 結構有效、required 功能測試綁定完整
- metadata.json 生成 → Schema 驗證 → 狀態顯示全鏈路
- 版本單一來源
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

import generate_html
import models_data
from extract_governance import extract_blocks, BlockError, GOV_FILE

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_governance_blocks_valid():
    """六個規範區塊：唯一、可解析、blockId 正確"""
    with open(GOV_FILE, encoding='utf-8') as f:
        text = f.read()
    blocks = extract_blocks(text)
    assert len(blocks) == 6
    assert blocks['AIRCON_AI_CONTEXT_V1']['blockId'] == 'AIRCON_AI_CONTEXT_V1'
    assert blocks['AIRCON_FEATURE_REGISTRY_V1']['schemaVersion'] == '1.0.0'


def test_governance_blocks_reject_broken_marker():
    """BEGIN 重複必須被拒絕"""
    text = '<!-- AIRCON:NORMATIVE:AI_CONTEXT_V1:BEGIN -->\n' * 2
    try:
        extract_blocks(text)
        assert False, '應該拋 BlockError'
    except BlockError:
        pass


# 已知測試綁定缺口（未實現功能，等人類決定：實現或降級——治理文檔 §5.2 唔得偽造測試名）
KNOWN_UNBOUND = {'report.pdf-export', 'core.ranking', 'core.recommendation'}


def test_feature_registry_schema_valid():
    """Registry：ID 格式、enum、必需欄位、唯一性"""
    with open(GOV_FILE, encoding='utf-8') as f:
        blocks = extract_blocks(f.read())
    reg = blocks['AIRCON_FEATURE_REGISTRY_V1']
    ids = [x['id'] for x in reg['features']]
    assert len(ids) == len(set(ids)), '功能 ID 重複'
    for f in reg['features']:
        assert f['protection'] in ('required', 'optional', 'deprecated', 'removed')
        assert f['priority'] in ('P0', 'P1', 'P2')
        assert f['category'] in ('core', 'data', 'ui', 'report', 'operations')
        if f['protection'] == 'required' and f['id'] not in KNOWN_UNBOUND:
            assert f['testBindings'] != [], f"{f['id']} 測試綁定不得為空（唔得偽造測試名）"


def test_metadata_generate_and_validate():
    """metadata.json 生成 → Schema 驗證全鏈路"""
    tmp = os.path.join(ROOT, 'tests', '.tmp_metadata.json')
    args = [
        sys.executable, os.path.join(ROOT, 'scripts', 'gen-metadata.py'),
        '--version', models_data.VERSION,
        '--build', 'B20260826.1',
        '--commit', 'a' * 40,
        '--workflow-run-id', '123456789',
        '--dataset-date', '2026-08-25',
        '--dataset-date-basis', 'retrieval-date-fallback',
        '--dataset-source-url', 'https://www.emsd.gov.hk/energylabel/tc/households/rac/select_ac_result.php',
        '--dataset-snapshot-id', 'emsd-20260825',
        '--dataset-hash', 'sha256:' + 'b' * 64,
        '--record-count', '1927',
        '--release-payload-hash', 'sha256:' + 'c' * 64,
        '--out', tmp, '--force',
    ]
    r = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, r.stderr
    r2 = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'scripts', 'validate_metadata.py'), tmp],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r2.returncode == 0, r2.stderr
    os.remove(tmp)


def test_metadata_validation_rejects_bad():
    """壞 metadata（缺欄位/錯 enum/rollback 缺 rollbackOfBuild）必須被拒"""
    from validate_metadata import validate
    with open(GOV_FILE, encoding='utf-8') as f:
        schema = extract_blocks(f.read())['AIRCON_METADATA_SCHEMA_V1']
    base = {
        'schemaVersion': '1.0.0', 'version': '1.2.7', 'build': 'B1',
        'commit': 'a' * 40, 'deployTime': '2026-08-26T00:00:00Z',
        'workflowRunId': '1', 'deploymentType': 'release',
        'releasePayloadHash': 'sha256:' + 'b' * 64,
        'datasetDate': '2026-08-25', 'datasetDateBasis': 'retrieval-date-fallback',
        'datasetRetrievedAt': '2026-08-25T00:00:00Z',
        'datasetSourceUrl': 'https://example.com', 'datasetSnapshotId': 's1',
        'datasetHash': 'sha256:' + 'c' * 64, 'recordCount': 100,
    }
    assert validate(base, schema) == []
    bad = dict(base)
    del bad['datasetDate']
    assert validate(bad, schema), '缺 datasetDate 應該被拒'
    bad = dict(base)
    bad['deploymentType'] = 'rollback'
    assert validate(bad, schema), 'rollback 缺 rollbackOfBuild 應該被拒'
    bad = dict(base)
    bad['extra'] = 'x'
    assert validate(bad, schema), '額外欄位應該被拒'


def test_format_status_metadata_driven():
    """狀態文字嚟自 metadata.json：HKT 轉換 + 資料日期"""
    meta = {
        'version': '1.2.7',
        'deployTime': '2026-08-26T02:00:00Z',   # UTC → HKT 10:00
        'datasetDate': '2026-08-25',
    }
    line1, line2 = generate_html.format_status(meta, models_data.VERSION)
    assert '1.2.7' in line1
    assert '2026-08-26 10:00' in line1, f'HKT 轉換錯：{line1}'
    assert 'HKT' in line1
    assert '2026-08-25' in line1 and '2026-08-25' in line2
    # 冇 metadata：顯示暫不可用，唔回退硬編舊值
    line1b, _ = generate_html.format_status({}, models_data.VERSION)
    assert '暫不可用' in line1b
    assert '2026-08-15' not in line1b, '唔得回退硬編舊日期'


def test_version_single_source():
    """版本單一來源：models_data.VERSION 係唯一手工來源"""
    assert models_data.VERSION == '1.2.7'
    assert generate_html.VERSION == models_data.VERSION


def test_feature_check_script():
    """feature-check 腳本：綁定完整即通過"""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'scripts', 'feature-check.py')],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, (r.stdout or '') + (r.stderr or '')
