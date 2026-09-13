# -*- coding: utf-8 -*-
"""治理落地測試（AIRCON_COMPARE_GOVERNANCE.md）

- 六個規範區塊可提取、嚴格 JSON 解析、ID 唯一
- Registry 結構有效、required 功能測試綁定完整
- metadata.json 生成 → Schema 驗證 → 狀態顯示全鏈路
- 版本單一來源
"""
import json
import os
import re
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


# 已知測試綁定缺口（而家已全部綁定；如有新缺口必須申報，唔得偽造測試名）
KNOWN_UNBOUND = set()


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
    """版本單一來源：models_data.VERSION 係唯一手工來源，格式符合 SemVer"""
    import re
    assert re.match(r'^\d+\.\d+\.\d+$', models_data.VERSION), '版本必須係 SemVer'
    assert generate_html.VERSION == models_data.VERSION


def test_generated_html_has_no_static_version():
    """operations.version-display：生成物唔可以內嵌手工版本常量（只由 metadata.json 讀）"""
    p = os.path.join(ROOT, 'index.html')
    if not os.path.exists(p):
        p = os.path.join(ROOT, '空調對比報告.html')
    with open(p, encoding='utf-8') as f:
        html = f.read()
    m = re.search(r'id="verInfo">([^<]*)<', html)
    assert m, '生成物應該有 verInfo 版本顯示位'
    assert models_data.VERSION not in m.group(1), (
        f'verInfo 唔可以內嵌 models_data.VERSION（實際：{m.group(1)!r}）')


def test_html_output_lf(tmp_path):
    """生成 HTML 必須用 LF：Windows 預設 CRLF 會令本地生成同 CI/已入庫 index.html 唔一致"""
    out = tmp_path / 'out.html'
    generate_html.write_html_output(str(out), 'a\nb\n')
    assert out.read_bytes() == b'a\nb\n'


def test_feature_check_script():
    """feature-check 腳本：綁定完整即通過"""
    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'scripts', 'feature-check.py')],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, (r.stdout or '') + (r.stderr or '')


def _sha256_file(path):
    import hashlib
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_pdf_export(tmp_path):
    """report.pdf-export：PDF 可生成、係有效 %PDF、同 Web 用同一 metadata 規則。

    輸出寫入 pytest tmp_path（PR-1 修正）：測試唔可以覆寫 repo 根目錄嘅
    空調對比報告.pdf（使用者既有未提交修改必須保留）。
    """
    import generate_pdf
    repo_pdf = os.path.join(ROOT, '空調對比報告.pdf')
    before = _sha256_file(repo_pdf) if os.path.exists(repo_pdf) else None

    out = tmp_path / '空調對比報告.pdf'
    generate_pdf.build_pdf(str(out))
    assert out.exists(), 'PDF 檔案應該生成'
    with open(out, 'rb') as f:
        head = f.read(8)
    assert head.startswith(b'%PDF'), f'唔係有效 PDF：{head!r}'
    assert out.stat().st_size > 10000, 'PDF 太細，疑似空檔'

    # 回歸：測試前後 repo 根目錄 PDF 必須逐位元不變
    after = _sha256_file(repo_pdf) if os.path.exists(repo_pdf) else None
    assert after == before, '測試唔可以改動 repo 根目錄嘅 空調對比報告.pdf'


def test_ranking_recommendation_sections():
    """core.ranking / core.recommendation：報告內文包含排名/推薦章節且引用數據來源"""
    md = open(os.path.join(ROOT, '空調對比報告.md'), encoding='utf-8').read()
    assert '排名' in md, '報告應該有排名章節'
    assert '推薦' in md, '報告應該有推薦章節'
    assert ('EMSD' in md and '官網' in md), '排名/推薦應該引用數據來源'
