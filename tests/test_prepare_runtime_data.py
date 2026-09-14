# -*- coding: utf-8 -*-
"""Fix D（持久發佈）回歸：runtime 資料準備守衛

- 舊格式 key 偵測（黑名單 + model_status）
- --check 模式唔會改檔，需要遷移時 exit 10
- 已 canonical → no-op（唔會誤跑唔 idempotent 嘅遷移）
- 遷移成功後驗證（舊 key = 0、key 數不變）
- 遷移失敗 / 遷移後仍有舊 key / key 數改變 → 阻斷 exit 1
"""
import importlib.util
import json
import os
import stat

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SPEC = importlib.util.spec_from_file_location(
    'prepare_runtime_data', os.path.join(BASE, 'scripts', 'prepare_runtime_data.py'))
prd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prd)

OLD_BL = {'version': 1, 'updated': '2026-08-26', 'models': {
    'RC-X7U': {'status': 'x'}, 'RA-10RF': {'status': 'y'}}}
OLD_ST = {'RC-X7U': {'misses': 1}, 'RA-10RF': {'misses': 2}}
NEW_BL = {'version': 1, 'updated': '2026-09-03', 'models': {
    'UNKNOWN|RCX7U': {'status': 'x'}, 'HITACHI|RA10RF': {'status': 'y'}}}
NEW_ST = {'UNKNOWN|RCX7U': {'misses': 1}, 'HITACHI|RA10RF': {'misses': 2}}


def _write(path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)


def _migrator(tmp_path, body):
    p = tmp_path / 'fake_migrator.py'
    p.write_text(body, encoding='utf-8')
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def _setup(tmp_path, bl, st, migrator=None):
    blp = tmp_path / 'model_blacklist.json'
    stp = tmp_path / 'model_status.json'
    _write(blp, bl)
    _write(stp, st)
    prd.BLACKLIST = str(blp)
    prd.STATUS = str(stp)
    if migrator:
        prd.MIGRATOR = migrator
    return blp, stp


def test_legacy_keys_detects_both(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST)
    assert prd.legacy_keys(str(blp)) == ['RC-X7U', 'RA-10RF']
    assert prd.legacy_keys(str(stp)) == ['RC-X7U', 'RA-10RF']
    assert prd.legacy_keys(str(tmp_path / 'missing.json')) == []


def test_check_mode_no_write_exit10(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST, migrator='/nonexistent')
    before = blp.read_bytes()
    assert prd.main(['--check']) == 10
    assert blp.read_bytes() == before, '--check 唔可以改檔'


def test_canonical_input_is_noop(tmp_path):
    blp, stp = _setup(tmp_path, NEW_BL, NEW_ST, migrator='/nonexistent')
    assert prd.main([]) == 0
    assert json.loads(blp.read_text(encoding='utf-8')) == NEW_BL


def test_migration_success_verified(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST)
    mig = _migrator(tmp_path, f'''
import json, sys
for p, is_bl in [({str(blp)!r}, True), ({str(stp)!r}, False)]:
    d = json.load(open(p, encoding='utf-8'))
    models = d['models'] if is_bl else d
    out = {{}}
    for k, v in models.items():
        out[('UNKNOWN|' + k.replace('-', '')) if k.startswith('RC') else ('HITACHI|' + k.replace('-', ''))] = v
    if is_bl:
        d['models'] = out
        json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    else:
        json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
''')
    prd.MIGRATOR = mig
    assert prd.main([]) == 0
    assert prd.legacy_keys(str(blp)) == []
    assert prd.legacy_keys(str(stp)) == []
    assert len(json.loads(blp.read_text(encoding='utf-8'))['models']) == 2


def test_migration_failure_blocks(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST)
    prd.MIGRATOR = _migrator(tmp_path, 'import sys; sys.exit(1)')
    assert prd.main([]) == 1
    assert prd.legacy_keys(str(blp)) == ['RC-X7U', 'RA-10RF']


def test_migration_leftover_legacy_blocks(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST)
    prd.MIGRATOR = _migrator(tmp_path, 'print("did nothing")')
    assert prd.main([]) == 1


def test_migration_key_count_change_blocks(tmp_path):
    blp, stp = _setup(tmp_path, OLD_BL, OLD_ST)
    mig = _migrator(tmp_path, f'''
import json
p = {str(blp)!r}
d = json.load(open(p, encoding='utf-8'))
d['models'] = {{'UNKNOWN|RCX7U': {{'status': 'x'}}}}
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
st = {str(stp)!r}
s = json.load(open(st, encoding='utf-8'))
json.dump({{'UNKNOWN|RCX7U': s['RC-X7U'], 'HITACHI|RA10RF': s['RA-10RF']}},
          open(st, 'w', encoding='utf-8'), ensure_ascii=False)
''')
    prd.MIGRATOR = mig
    assert prd.main([]) == 1


def test_missing_blacklist_blocks(tmp_path):
    prd.BLACKLIST = str(tmp_path / 'nope.json')
    prd.STATUS = str(tmp_path / 'nope_status.json')
    assert prd.main([]) == 1
