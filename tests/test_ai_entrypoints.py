# -*- coding: utf-8 -*-
"""AI 入口與治理源一致性（最小漂移檢查）

- AGENTS.md 與 .github/copilot-instructions.md 都要指向唯一治理源；
- 兩者都要有最低關鍵約束（不得降級 required／不得偽造 metadata／VERSION 單一來源）；
- 如存在 docs/generated 投影，必須同內嵌區塊一致（冇投影則明確不存在，不需虛構）。
"""
import importlib.util
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = importlib.util.spec_from_file_location(
    'extract_gov_entry', os.path.join(BASE, 'scripts', 'extract_governance.py'))
eg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(eg)

GOV = 'docs/AIRCON_COMPARE_GOVERNANCE.md'
AGENTS = os.path.join(BASE, 'AGENTS.md')
COPILOT = os.path.join(BASE, '.github', 'copilot-instructions.md')


def _read(p):
    return open(p, encoding='utf-8').read()


def test_entrypoints_reference_governance_source():
    for p in (AGENTS, COPILOT):
        assert GOV in _read(p), f'{p} 必須指向唯一治理源 {GOV}'


def test_entrypoints_have_core_constraints():
    for p in (AGENTS, COPILOT):
        text = _read(p)
        assert 'models_data.py' in text and 'VERSION' in text, f'{p} 缺版本單一來源'
        assert ('不得偽造' in text or '不得伪造' in text or '唔可以偽造' in text
                or '不得偽造 metadata' in text), f'{p} 缺不得偽造約束'
        assert 'required' in text, f'{p} 缺 required 保護約束'


def test_entrypoints_no_private_line_identifiers():
    for p in (AGENTS, COPILOT):
        text = _read(p)
        # 以 runtime 組合避免測試檔自身被 gate 命中
        privates = ('release-' + '299c3e9', 'aircon-' + 'docker', 'AIRCON_' + 'BASE_DIR',
                    '/' + 'srv/aircon-compare', 'AIRCON_' + 'STAGING_PORT')
        for pat in privates:
            assert pat not in text, f'{p} 仍含私人部署標識：{pat}'


def test_generated_projections_match_embedded_blocks():
    gen = os.path.join(BASE, 'docs', 'generated')
    if not os.path.isdir(gen):
        # 目前冇生成投影；如將來新增，必須逐一等於內嵌區塊（唔可以獨立編輯）
        assert not os.path.exists(gen)
        return
    with open(os.path.join(BASE, GOV), encoding='utf-8') as f:
        blocks = eg.extract_blocks(f.read())
    for name, bid in (('FEATURE_REGISTRY.json', 'AIRCON_FEATURE_REGISTRY_V1'),
                      ('SUCCESS_CRITERIA.json', 'AIRCON_SUCCESS_CRITERIA_V1'),
                      ('metadata.schema.json', 'AIRCON_METADATA_SCHEMA_V1'),
                      ('feature-registry.schema.json', 'AIRCON_FEATURE_REGISTRY_SCHEMA_V1'),
                      ('success-criteria.schema.json', 'AIRCON_SUCCESS_CRITERIA_SCHEMA_V1')):
        p = os.path.join(gen, name)
        if os.path.exists(p):
            assert json.load(open(p, encoding='utf-8')) == blocks[bid], (
                f'投影漂移：{name}')
