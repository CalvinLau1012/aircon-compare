# -*- coding: utf-8 -*-
"""feature-check 證據回歸（GATE-03）

- 由「檔案存在」提升到 pytest 實際 collection node ids：假 node 阻斷
- 空測試（無斷言）靜態阻斷；class／param 形式支援
- --run-tests 收集 setup/call/teardown 與 skip/xfail/fail；skip 唔可以當通過
- 報告係機器可讀 JSON（repo 外）
"""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

_SPEC = importlib.util.spec_from_file_location(
    'feature_check_ev', os.path.join(BASE, 'scripts', 'feature-check.py'))
feature_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(feature_check)


def _write(tmp, body):
    tests = tmp / 'tests'
    tests.mkdir(exist_ok=True)
    (tests / 'test_demo.py').write_text(textwrap.dedent(body), encoding='utf-8')


def _registry(binding, fid='core.demo'):
    return {'features': [{'id': fid, 'protection': 'required', 'testBindings': [binding]}]}


def test_check_assertions_rejects_always_pass(tmp_path, monkeypatch):
    """治理 §9.2：空斷言、常量斷言、吞例外總是成功都要阻斷"""
    monkeypatch.setattr(feature_check, 'BASE', str(tmp_path))
    _write(tmp_path, '''
        def test_assert_true():
            assert True
        def test_assert_const_compare():
            assert 1 == 1
        def test_const_name():
            x = True
            assert x
        def test_pass_only():
            pass
        def test_swallow_then_const():
            try:
                compute()
            except Exception:
                pass
            assert True
        def test_unittest_const():
            self.assertTrue(True)
        def test_real():
            assert compute() == 1
        def test_raises():
            import pytest
            with pytest.raises(ValueError):
                int('x')
    ''')
    for name in ('test_assert_true', 'test_assert_const_compare', 'test_const_name',
                 'test_pass_only', 'test_swallow_then_const', 'test_unittest_const'):
        ok, reason = feature_check.check_assertions(f'tests/test_demo.py::{name}')
        assert not ok, f'{name} 應該被拒：{reason}'
    assert feature_check.check_assertions('tests/test_demo.py::test_real')[0]
    assert feature_check.check_assertions('tests/test_demo.py::test_raises')[0]


def test_check_assertions_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_check, 'BASE', str(tmp_path))
    _write(tmp_path, '''
        import pytest
        def test_has_assert():
            assert compute() == 1
        def test_empty():
            x = 1
        def test_raises():
            with pytest.raises(ValueError):
                int('x')
        class TestKlass:
            def test_method(self):
                assert compute() == 1
    ''')
    assert feature_check.check_assertions('tests/test_demo.py::test_has_assert')[0]
    assert feature_check.check_assertions('tests/test_demo.py::test_raises')[0]
    assert feature_check.check_assertions('tests/test_demo.py::TestKlass::test_method')[0]
    ok, reason = feature_check.check_assertions('tests/test_demo.py::test_empty')
    assert not ok and '斷言' in reason
    ok2, reason2 = feature_check.check_assertions('tests/test_demo.py::test_missing')
    assert not ok2


def test_fake_node_blocked_by_collection(tmp_path, monkeypatch):
    """綁定一個喺真實檔案但唔存在嘅 node id → collection 阻斷"""
    with open(feature_check.GOV_FILE, encoding='utf-8') as f:
        reg = feature_check.extract_blocks(f.read())['AIRCON_FEATURE_REGISTRY_V1']
    mutated = json.loads(json.dumps(reg, ensure_ascii=False))
    mutated['features'][0]['testBindings'] = ['tests/test_core.py::test_totally_fake_node']
    report = tmp_path / 'report.json'
    rep, errors = feature_check.run_evidence(mutated, False, str(report), 120, 120)
    assert any('假 node' in e or '唔存在' in e for e in errors), errors
    assert rep['bindings']['tests/test_core.py::test_totally_fake_node']['status'] == 'missing'
    assert report.exists()
    saved = json.load(open(report, encoding='utf-8'))
    assert saved['ok'] is False


def test_run_evidence_skip_is_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_check, 'BASE', str(tmp_path))
    _write(tmp_path, '''
        import pytest
        def compute():
            return 1
        def test_skips():
            assert compute() == 1
            pytest.skip('demo skip')
    ''')
    report = tmp_path / 'report.json'
    rep, errors = feature_check.run_evidence(
        _registry('tests/test_demo.py::test_skips'), True, str(report), 120, 120)
    assert errors, 'skip 唔可以當通過'
    assert rep['bindings']['tests/test_demo.py::test_skips']['status'] == 'failed'
    saved = json.load(open(report, encoding='utf-8'))
    phases = saved['run']['tests']['tests/test_demo.py::test_skips']['phases']
    assert phases['call']['outcome'] == 'skipped', '報告要記錄 skip 階段'


def test_run_evidence_failure_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_check, 'BASE', str(tmp_path))
    _write(tmp_path, '''
        def compute():
            return 2
        def test_fails():
            assert compute() == 1, 'boom'
    ''')
    report = tmp_path / 'report.json'
    rep, errors = feature_check.run_evidence(
        _registry('tests/test_demo.py::test_fails'), True, str(report), 120, 120)
    assert errors
    saved = json.load(open(report, encoding='utf-8'))
    assert saved['run']['tests']['tests/test_demo.py::test_fails']['result'] == 'failed'
    assert 'boom' in saved['run']['tests']['tests/test_demo.py::test_fails']['phases']['call']['longrepr']


def test_run_evidence_pass_and_param_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_check, 'BASE', str(tmp_path))
    _write(tmp_path, '''
        import pytest
        @pytest.mark.parametrize('x', [1, 2])
        def test_param(x):
            assert x in (1, 2)
    ''')
    report = tmp_path / 'report.json'
    rep, errors = feature_check.run_evidence(
        _registry('tests/test_demo.py::test_param'), True, str(report), 120, 120)
    assert errors == [], errors
    entry = rep['bindings']['tests/test_demo.py::test_param']
    assert len(entry['matched']) == 2, 'param 綁定要匹配全部參數案例'
    assert entry['status'] == 'passed'
    saved = json.load(open(report, encoding='utf-8'))
    assert saved['ok'] is True
    assert all(t['result'] == 'passed' for t in saved['run']['tests'].values())


def test_plugin_records_collection_errors_and_exit(tmp_path):
    """plugin collect 模式要寫 exitStatus／collectionErrors（唔可以永遠空）"""
    (tmp_path / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
    (tmp_path / 'test_broken.py').write_text('import nonexistent_module_xyz\n', encoding='utf-8')
    report = tmp_path / 'collect.json'
    env = dict(os.environ, PYTHONPATH=os.path.join(BASE, 'scripts'))
    r = subprocess.run(
        [sys.executable, '-m', 'pytest', '-p', 'pytest_evidence_plugin',
         '--evidence-report', str(report), '--evidence-mode', 'collect', '--collect-only', '-q',
         str(tmp_path / 'test_broken.py'), str(tmp_path / 'test_ok.py')],
        cwd=BASE, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode != 0
    data = json.load(open(report, encoding='utf-8'))
    assert data['phase'] == 'finished'
    assert data['exitStatus'] != 0
    assert data['collectionErrors'], 'collection 錯誤必須記錄'


def test_plugin_records_setup_call_teardown(tmp_path):
    (tmp_path / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
    report = tmp_path / 'run.json'
    env = dict(os.environ, PYTHONPATH=os.path.join(BASE, 'scripts'))
    r = subprocess.run(
        [sys.executable, '-m', 'pytest', '-p', 'pytest_evidence_plugin',
         '--evidence-report', str(report), '--evidence-mode', 'run', '-q',
         str(tmp_path / 'test_ok.py')],
        cwd=BASE, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, r.stderr
    data = json.load(open(report, encoding='utf-8'))
    assert data['exitStatus'] == 0
    entry = next(iter(data['tests'].values()))
    assert set(entry['phases']) == {'setup', 'call', 'teardown'}
    assert entry['result'] == 'passed'


def test_check_test_bindings_rejects_bad_format():
    reg = {'features': [{'id': 'core.demo', 'protection': 'required',
                         'testBindings': ['tests/test_core.py::test_ok', 'tests/test_core.py']}]}
    missing, bad = feature_check.check_test_bindings(reg)
    assert missing == []
    assert any('格式' in b for b in bad), bad


def test_feature_check_cli_static_evidence_report():
    script = os.path.join(BASE, 'scripts', 'feature-check.py')
    report = os.path.join(os.environ.get('TEMP', '/tmp'), 'feature-check-test-cli.json')
    r = subprocess.run([sys.executable, script, '--report', report],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert r.returncode == 0, r.stderr
    data = json.load(open(report, encoding='utf-8'))
    assert data['ok'] is True
    assert data['collection']['matchedNodes'], '要有實際 collection node ids'


# ---------------------------------------------------------------- fail-closed

def _fake_proc(code):
    import types
    return types.SimpleNamespace(returncode=code, stdout='', stderr='')


def _run_pass_report(node, **over):
    tests = {node: {'phases': {
        'setup': {'outcome': 'passed'},
        'call': {'outcome': 'passed'},
        'teardown': {'outcome': 'passed'}},
        'result': 'passed'}}
    report = {'phase': 'finished', 'exitStatus': 0, 'collected': [node],
              'collectionErrors': [], 'deselected': [], 'tests': tests}
    report.update(over)
    return report


def _collect_report(node, **over):
    report = {'phase': 'finished', 'exitStatus': 0, 'collected': [node],
              'collectionErrors': [], 'deselected': []}
    report.update(over)
    return report

def _with_fake_pytest(monkeypatch, collect, run):
    calls = {'n': 0}

    def fake(targets, mode, report_path, timeout):
        calls['n'] += 1
        proc, report = (collect if mode == 'collect' else run)
        return {'proc': proc, 'report': report, 'tail': ''}, None

    monkeypatch.setattr(feature_check, '_run_pytest', fake)
    # 這些測試聚焦 collection/run fail-closed；靜態斷言檢查另有專門測試
    monkeypatch.setattr(feature_check, 'check_assertions', lambda binding: (True, ''))
    return calls


def test_run_evidence_fails_on_nonzero_run_subprocess(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node)),
                      (_fake_proc(2), _run_pass_report(node, exitStatus=2)))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert errors, 'run subprocess 非零必須阻斷'
    assert rep['ok'] is False


def test_run_evidence_fails_on_collection_errors(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(2), _collect_report(None, collected=[], collectionErrors=[{'nodeid': 'x', 'longrepr': 'boom'}])),
                      (_fake_proc(0), _run_pass_report(node)))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert any('collection' in e for e in errors), errors
    assert rep['ok'] is False


def test_run_evidence_fails_on_deselected(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node, deselected=[node])),
                      (_fake_proc(0), _run_pass_report(node)))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert any('deselect' in e for e in errors), errors


def test_run_evidence_fails_on_unfinished_session(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node)),
                      (_fake_proc(0), {'collected': [node], 'tests': {}}))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert any('未完成' in e for e in errors), errors


def test_run_evidence_fails_on_missing_teardown(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    broken = _run_pass_report(node)
    del broken['tests'][node]['phases']['teardown']
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node)),
                      (_fake_proc(0), broken))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert any('teardown' in e for e in errors), errors


def test_run_evidence_fails_on_xpass_wasxfail(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    xrep = _run_pass_report(node)
    xrep['tests'][node]['phases']['call']['wasxfail'] = 'expected fail'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node)),
                      (_fake_proc(0), xrep))
    rep, errors = feature_check.run_evidence(
        _registry(node), True, str(tmp_path / 'r.json'), 60, 60)
    assert errors, 'XPASS／wasxfail 唔可以當通過'


def test_approved_skip_is_per_node_and_never_exempts_fail(tmp_path, monkeypatch):
    a = 'tests/test_demo.py::test_a'
    b = 'tests/test_demo.py::test_b'
    monkeypatch.setattr(feature_check, 'APPROVED_SKIP', {a})
    rep = _run_pass_report(a)
    rep['collected'] = [a, b]
    rep['tests'][b] = dict(_run_pass_report(b)['tests'][b])
    rep['tests'][b]['phases']['call'] = dict(rep['tests'][b]['phases']['call'])
    rep['tests'][b]['phases']['call']['outcome'] = 'failed'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(a, collected=[a, b])),
                      (_fake_proc(1), rep))
    reg = {'features': [
        {'id': 'core.a', 'protection': 'required', 'testBindings': [a]},
        {'id': 'core.b', 'protection': 'required', 'testBindings': [b]}]}
    r, errors = feature_check.run_evidence(reg, True, str(tmp_path / 'r2.json'), 60, 60)
    assert any('core.b' in e for e in errors), errors
    assert r['bindings'][b]['status'] == 'failed'


def test_run_evidence_report_write_failure_nonzero(tmp_path, monkeypatch):
    node = 'tests/test_demo.py::test_pass'
    _with_fake_pytest(monkeypatch,
                      (_fake_proc(0), _collect_report(node)),
                      (_fake_proc(0), _run_pass_report(node)))
    blocker = tmp_path / 'blocker'
    blocker.write_text('x', encoding='utf-8')
    report_path = str(blocker / 'report.json')  # parent 係檔案，makedirs 必失敗
    rep, errors = feature_check.run_evidence(
        _registry(node), True, report_path, 60, 60)
    assert any('報告寫入失敗' in e for e in errors), errors
    assert rep['ok'] is False
