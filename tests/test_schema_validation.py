# -*- coding: utf-8 -*-
"""完整 Schema／治理提取回歸（P0）

- validate_metadata 用完整 Draft 2020-12 + FormatChecker：const/minimum/maxLength/
  allOf/rollback/真實日期/URI、root 非 object 都要拒絕
- extract_governance 拒絕 duplicate JSON keys、區塊唯一、Registry 按內嵌 Schema 驗證
- CLI 錯誤可讀、非零、唔會 traceback
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))
from extract_governance import extract_blocks, BlockError, GOV_FILE  # noqa: E402
from validate_metadata import validate, validate_core  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    'feature_check_mod', os.path.join(BASE, 'scripts', 'feature-check.py'))
feature_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(feature_check)

with open(GOV_FILE, encoding='utf-8') as _f:
    SCHEMA = extract_blocks(_f.read())['AIRCON_METADATA_SCHEMA_V1']

VALID = {
    'schemaVersion': '1.0.0', 'version': '1.2.9', 'build': 'B20260921.1',
    'commit': 'a' * 40, 'deployTime': '2026-09-21T00:00:00Z',
    'workflowRunId': '1', 'deploymentType': 'release',
    'releasePayloadHash': 'sha256:' + 'b' * 64,
    'datasetDate': '2026-09-21', 'datasetDateBasis': 'retrieval-date-fallback',
    'datasetRetrievedAt': '2026-09-20T18:59:57Z',
    'datasetSourceUrl': 'https://www.emsd.gov.hk/x.php', 'datasetSnapshotId': 's1',
    'datasetHash': 'sha256:' + 'c' * 64, 'recordCount': 1,
}


def _mut(**over):
    meta = dict(VALID)
    meta.update(over)
    return meta


def test_valid_metadata_passes():
    assert validate(VALID, SCHEMA) == []


@pytest.mark.parametrize('bad', [
    _mut(schemaVersion='2.0.0'),                 # const
    _mut(version='1.2'),                         # SemVer pattern
    _mut(build='B' * 129),                       # maxLength
    _mut(commit='a' * 39),                       # 完整 SHA
    _mut(deployTime='2026-09-21T00:00:00+08:00'),  # UTC Z
    _mut(deployTime='2026-09-21T00:00:00Z '),      # trailing space
    _mut(datasetDate='2026-02-30'),              # 真實日曆（FormatChecker date）
    _mut(datasetDate='2026-9-1'),                # date pattern
    _mut(datasetDateBasis='guessed'),            # enum
    _mut(datasetSourceUrl='not a uri'),          # format uri
    _mut(recordCount=0),                         # minimum 1
    _mut(rawRecordCount=-1),                     # minimum 0
    _mut(deploymentType='bogus'),                # enum
    _mut(extra='x'),                             # additionalProperties
])
def test_schema_rejects_invalid(bad):
    assert validate(bad, SCHEMA), f'應該拒絕：{bad}'


def test_rollback_conditional():
    rollback = _mut(deploymentType='rollback')
    assert validate(rollback, SCHEMA), 'rollback 缺 rollbackOfBuild 必須拒絕'
    rollback['rollbackOfBuild'] = 'B-old'
    assert validate(rollback, SCHEMA) == []


@pytest.mark.parametrize('root', [[], None, 'x', 42])
def test_root_non_object_rejected(root):
    assert validate(root, SCHEMA), 'root 非 object 必須拒絕'


def test_validate_core_placeholder_and_rejects_bad_core():
    core = dict(VALID)
    del core['releasePayloadHash']
    assert validate(core, SCHEMA), '正式 Schema 仍然拒絕無 payloadHash core'
    assert validate_core(core, SCHEMA) == [], '核心事實（其餘齊全）應通過 --core 驗證'
    bad = dict(core)
    bad['datasetDate'] = '2026-02-30'
    assert validate_core(bad, SCHEMA)


def test_extract_governance_rejects_duplicate_json_keys(tmp_path):
    text = open(GOV_FILE, encoding='utf-8').read()
    tampered = text.replace(
        '"blockId": "AIRCON_AI_CONTEXT_V1"',
        '"blockId": "AIRCON_AI_CONTEXT_V1",\n  "blockId": "AIRCON_DUPLICATE"', 1)
    assert tampered != text
    with pytest.raises(BlockError, match='duplicate'):
        extract_blocks(tampered)


def test_extract_governance_rejects_broken_marker():
    text = open(GOV_FILE, encoding='utf-8').read().replace(
        '<!-- AIRCON:NORMATIVE:METADATA_SCHEMA_V1:END -->', '', 1)
    with pytest.raises(BlockError):
        extract_blocks(text)


def test_registry_full_schema_catches_downgrade():
    with open(GOV_FILE, encoding='utf-8') as f:
        reg = extract_blocks(f.read())['AIRCON_FEATURE_REGISTRY_V1']
    mutated = json.loads(json.dumps(reg))
    mutated['features'][0]['protection'] = 'weakened'
    errors = feature_check.check_registry_schema(mutated)
    assert errors, '非法 protection 必須被完整 Schema 攔截'
    mutated2 = json.loads(json.dumps(reg))
    mutated2['features'][0]['testBindings'] = [1]
    assert feature_check.check_registry_schema(mutated2), 'testBindings 非字串要攔截'


def test_governance_cli_readable_errors():
    script = os.path.join(BASE, 'scripts', 'extract_governance.py')
    r = subprocess.run([sys.executable, script, '--dump'], capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 2 and '--dump' in r.stderr and 'Traceback' not in r.stderr
    r2 = subprocess.run([sys.executable, script, '--dump', 'NOPE'], capture_output=True,
                        text=True, encoding='utf-8', errors='replace')
    assert r2.returncode == 2 and 'NOPE' in r2.stderr and 'Traceback' not in r2.stderr
    r3 = subprocess.run([sys.executable, script], capture_output=True,
                        text=True, encoding='utf-8', errors='replace')
    assert r3.returncode == 0, r3.stderr
    assert 'Schema 完整驗證' in r3.stdout


def test_validate_metadata_cli_readable_errors(tmp_path):
    script = os.path.join(BASE, 'scripts', 'validate_metadata.py')
    bad = tmp_path / 'bad.json'
    bad.write_text('{not json', encoding='utf-8')
    r = subprocess.run([sys.executable, script, str(bad)], capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 1 and '有效 JSON' in r.stderr and 'Traceback' not in r.stderr
    missing = subprocess.run([sys.executable, script, str(tmp_path / 'nope.json')],
                             capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert missing.returncode == 1 and '搵唔到' in missing.stderr


# ---------------------------------------------------------------- 提取器嚴格 JSON／marker

def _gov_text():
    return open(GOV_FILE, encoding='utf-8').read()


def test_extract_rejects_nan_and_infinity():
    text = _gov_text()
    for const in ('NaN', 'Infinity', '-Infinity'):
        tampered = text.replace('"schemaVersion": "1.0.0",\n  "normative": true,',
                                f'"schemaVersion": "1.0.0",\n  "normative": {const},', 1)
        assert tampered != text
        with pytest.raises(BlockError, match='常數'):
            extract_blocks(tampered)


def test_extract_rejects_unknown_marker():
    text = _gov_text() + '\n<!-- AIRCON:NORMATIVE:BOGUS_V1:BEGIN -->\n'
    with pytest.raises(BlockError, match='未知'):
        extract_blocks(text)


def test_extract_rejects_nested_marker():
    text = _gov_text().replace(
        '<!-- AIRCON:NORMATIVE:AI_CONTEXT_V1:END -->',
        '<!-- AIRCON:NORMATIVE:METADATA_SCHEMA_V1:BEGIN -->\n'
        '<!-- AIRCON:NORMATIVE:AI_CONTEXT_V1:END -->', 1)
    with pytest.raises(BlockError, match='嵌套|交錯|未配對'):
        extract_blocks(text)


def test_extract_rejects_unpaired_marker():
    text = _gov_text().replace('<!-- AIRCON:NORMATIVE:AI_CONTEXT_V1:BEGIN -->', '', 1)
    with pytest.raises(BlockError, match='未配對|缺少'):
        extract_blocks(text)


def test_extract_rejects_extra_fence():
    text = _gov_text().replace(
        '"blockId": "AIRCON_AI_CONTEXT_V1",',
        '"blockId": "AIRCON_AI_CONTEXT_V1",\n  "example": ```json```,', 1)
    with pytest.raises(BlockError):
        extract_blocks(text)


def test_extract_rejects_duplicate_keys():
    text = _gov_text().replace(
        '"blockId": "AIRCON_AI_CONTEXT_V1",',
        '"blockId": "AIRCON_AI_CONTEXT_V1", "blockId": "DUP",', 1)
    with pytest.raises(BlockError, match='duplicate'):
        extract_blocks(text)
