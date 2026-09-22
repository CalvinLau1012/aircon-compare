#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Feature Check（文檔 §9.2）：功能註冊表結構驗證 + 測試綁定證據

- 提取 Registry 區塊並按內嵌 Schema 做完整 Draft 2020-12 驗證（ID 格式、enum、必需欄位）
- required 功能 testBindings 必須非空，且係 `檔案::node` 格式
- 用 pytest 實際 collection 解析 node ids（唔再只查檔案存在）：假 node、拼錯參數、
  只有檔案存在但測試名唔存在都會阻斷
- 靜態檢查綁定測試有斷言（空測試／只有 pass 會阻斷）
- `--run-tests`：用 pytest 證據外掛實際執行綁定節點，收集 setup/call/teardown、
  skip/xfail/fail；required 綁定必須 passed，不可 skip
- 機器報告寫入 repo 外（預設系統 temp；CI 可用 FEATURE_CHECK_REPORT 指定）

用法：
  python scripts/feature-check.py                # 結構 + collection + 靜態斷言檢查
  python scripts/feature-check.py --run-tests    # 再實際執行綁定測試並收集證據
退出碼：0 = 通過；1 = 有缺口、結構錯誤或證據失敗
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_governance import extract_blocks, BlockError, GOV_FILE

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, 'scripts')

CATEGORIES = {'core', 'data', 'ui', 'report', 'operations'}
PRIORITIES = {'P0', 'P1', 'P2'}
PROTECTIONS = {'required', 'optional', 'deprecated', 'removed'}
ID_RE = re.compile(r'^[a-z][a-z0-9]*(\.[a-z][a-z0-9-]*)+$')
NODE_RE = re.compile(r'^[^:]+\.py::[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*$')

# 已知測試綁定缺口（而家已全部綁定；如有新缺口必須申報，唔得偽造測試名）
KNOWN_UNBOUND = set()
# 已批准嘅 skip 例外（只可以由人類按治理 §15.3 加入；空集合 = 任何 skip 都阻斷）
APPROVED_SKIP = set()

DEFAULT_TIMEOUT_COLLECT = 300
DEFAULT_TIMEOUT_RUN = 1800


def _load_registry_schema():
    with open(GOV_FILE, encoding='utf-8') as f:
        blocks = extract_blocks(f.read())
    return blocks['AIRCON_FEATURE_REGISTRY_SCHEMA_V1']


def check_registry_schema(reg):
    """Registry 結構 + 完整 Draft 2020-12 Schema 驗證（回傳錯誤列表，保持舊介面）"""
    errors = []
    if reg.get('blockId') != 'AIRCON_FEATURE_REGISTRY_V1':
        errors.append('blockId 唔正確')
    if reg.get('schemaVersion') != '1.0.0':
        errors.append('schemaVersion 唔正確')
    for key in ('statusSemantics', 'features'):
        if key not in reg:
            errors.append(f'缺少 {key}')
    feats = reg.get('features', [])
    if not isinstance(feats, list) or not feats:
        errors.append('features 必須係非空陣列')
    seen = set()
    for f in feats:
        fid = f.get('id')
        if not fid or not ID_RE.match(fid):
            errors.append(f'非法 id：{fid!r}')
        if fid in seen:
            errors.append(f'重複 id：{fid}')
        seen.add(fid)
        if f.get('category') not in CATEGORIES:
            errors.append(f'{fid}：非法 category {f.get("category")!r}')
        if f.get('priority') not in PRIORITIES:
            errors.append(f'{fid}：非法 priority')
        if f.get('protection') not in PROTECTIONS:
            errors.append(f'{fid}：非法 protection')
        for k in ('name', 'aliases', 'testContract', 'evidenceRequired', 'testBindings'):
            if k not in f:
                errors.append(f'{fid}：缺少欄位 {k}')
        if not isinstance(f.get('evidenceRequired', []), list) or not f['evidenceRequired']:
            errors.append(f'{fid}：evidenceRequired 必須非空')
    # 完整 Schema（含 minimum／maxLength／uniqueItems／pattern）；避免只鏡像人手檢查
    try:
        from validate_metadata import validate
        errors.extend(validate(reg, _load_registry_schema()))
    except Exception as e:  # Schema 壞／驗證器例外都要阻斷，唔可以當通過
        errors.append(f'Registry Schema 驗證失敗：{type(e).__name__}: {e}')
    return errors


def check_test_bindings(reg):
    """required 功能必須綁定測試（已知缺口除外）；綁定檔案必須存在且格式係 檔案::node。

    保持舊介面（回傳 missing, missing_files）供相容；node 真確性由 collection 證據驗證。
    """
    missing, missing_files = [], []
    for f in reg.get('features', []):
        if f.get('protection') != 'required':
            continue
        if f['id'] in KNOWN_UNBOUND:
            continue
        binds = f.get('testBindings', [])
        if not binds:
            missing.append(f['id'])
            continue
        for b in binds:
            file_part = b.split('::', 1)[0]
            p = os.path.join(BASE, file_part)
            if '::' not in b or not NODE_RE.match(b):
                missing_files.append(f"{f['id']} → 綁定格式唔係 檔案::node：{b!r}")
            elif not os.path.exists(p):
                missing_files.append(f"{f['id']} → {b}")
    return missing, missing_files


def _is_constant_expr(node, consts=None):
    """表達式係唔係純常量（literal／常量運算／容器／比較／boolop／三元）。"""
    consts = consts or set()
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Name):
        return node.id in consts
    if isinstance(node, ast.UnaryOp):
        return _is_constant_expr(node.operand, consts)
    if isinstance(node, ast.BinOp):
        return _is_constant_expr(node.left, consts) and _is_constant_expr(node.right, consts)
    if isinstance(node, ast.BoolOp):
        return all(_is_constant_expr(v, consts) for v in node.values)
    if isinstance(node, ast.Compare):
        return all(_is_constant_expr(c, consts) for c in [node.left, *node.comparators])
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_constant_expr(e, consts) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all((k is None or _is_constant_expr(k, consts)) and _is_constant_expr(v, consts)
                   for k, v in zip(node.keys, node.values))
    if isinstance(node, ast.IfExp):
        return (_is_constant_expr(node.test, consts)
                and _is_constant_expr(node.body, consts)
                and _is_constant_expr(node.orelse, consts))
    return False


def _local_constant_names(func):
    """單次賦值且值為常量嘅局部名稱（用嚟捉 x = True; assert x）。"""
    counts, values = {}, {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    counts[target.id] = counts.get(target.id, 0) + 1
                    values[target.id] = node.value
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and isinstance(node.target, ast.Name):
            name = node.target.id
            counts[name] = counts.get(name, 0) + 1
            # AnnAssign 可能有 value；AugAssign 一定係重新計算，唔算常量
    consts = set()
    for name, count in counts.items():
        value = values.get(name)
        if count == 1 and value is not None and _is_constant_expr(value):
            consts.add(name)
    return consts


def _swallows_exceptions(func):
    """函數內有 try/except 食掉例外（handler 只有 pass／continue）。"""
    for node in ast.walk(func):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                body = [n for n in handler.body
                        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
                if not body or all(isinstance(n, (ast.Pass, ast.Continue)) for n in body):
                    return True
    return False


def check_assertions(binding):
    """靜態檢查綁定測試函數有實質斷言；拒絕空測試／總是成功測試。

    拒絕：
      - 冇任何 assert／pytest.raises／unittest assert；
      - 只有常量斷言（`assert True`、`assert 1 == 1`、`x = True; assert x`）；
      - 吞掉例外（try/except pass）令測試可能永遠成功；
      - unittest 風格 `assertTrue(常量)` 等。
    只係 E1 輔助；行為證據仍然由 pytest 實際執行提供。回傳 (ok, reason)。
    """
    file_part, node_part = binding.split('::', 1)
    parts = node_part.split('::')
    path = os.path.join(BASE, file_part)
    if not os.path.exists(path):
        return False, f'檔案唔存在：{file_part}'
    try:
        tree = ast.parse(open(path, encoding='utf-8').read(), filename=file_part)
    except (OSError, SyntaxError) as e:
        return False, f'無法解析：{e}'
    func_name = parts[-1]
    class_name = parts[-2] if len(parts) >= 2 else None
    candidates = []

    def visit(node, in_class=None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name == func_name and (class_name is None or in_class == class_name):
                    candidates.append(child)
                visit(child, in_class)

    visit(tree)
    if not candidates:
        return False, f'搵唔到測試函數 {node_part}'

    def call_name(node):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                return f.attr
            if isinstance(f, ast.Name):
                return f.id
        return ''

    for func in candidates:
        consts = _local_constant_names(func)
        meaningful = constant_only = raises_like = False
        for node in ast.walk(func):
            if isinstance(node, ast.Assert):
                if _is_constant_expr(node.test, consts):
                    constant_only = True
                else:
                    meaningful = True
            elif isinstance(node, ast.With):
                for item in node.items:
                    if call_name(item.context_expr) in ('raises', 'warns', 'deprecated_call'):
                        raises_like = True
            elif isinstance(node, ast.Call):
                name = call_name(node)
                if name in ('fail', 'raises'):
                    raises_like = True
                elif name.startswith('assert'):
                    args = [*node.args, *(kw.value for kw in node.keywords)]
                    if args and all(_is_constant_expr(a, consts) for a in args):
                        constant_only = True
                    else:
                        meaningful = True
        if _swallows_exceptions(func):
            return False, f'{node_part} 吞掉例外（try/except pass），可能總是成功'
        if meaningful or raises_like:
            return True, ''
        if constant_only:
            return False, f'{node_part} 只有常量斷言（總是成功，唔算有效證據）'
        return False, f'{node_part} 冇實質斷言（空測試唔可以當有效綁定）'
    return False, f'{node_part} 冇實質斷言（空測試唔可以當有效綁定）'


def _binding_targets(reg):
    targets = []
    for f in reg.get('features', []):
        if f.get('protection') != 'required' or f['id'] in KNOWN_UNBOUND:
            continue
        for b in f.get('testBindings', []):
            targets.append((f['id'], b))
    return targets


def _run_pytest(targets, mode, report_path, timeout):
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(p for p in (SCRIPTS, env.get('PYTHONPATH', '')) if p)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['FEATURE_CHECK_INSIDE'] = '1'
    cmd = [sys.executable, '-m', 'pytest', '-p', 'pytest_evidence_plugin',
           '--evidence-report', report_path, '--evidence-mode', mode,
           '-q', '--no-header', '-p', 'no:cacheprovider']
    if mode == 'collect':
        cmd.append('--collect-only')
    cmd.extend(targets)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', env=env, cwd=BASE, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return None, f'pytest {mode} 逾時（>{timeout}s）：{e}'
    report = None
    if os.path.exists(report_path):
        try:
            with open(report_path, encoding='utf-8') as f:
                report = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            return None, f'pytest {mode} 報告讀取失敗：{e}'
    tail = (proc.stdout or '')[-1500:] + (proc.stderr or '')[-1500:]
    return {'proc': proc, 'report': report, 'tail': tail}, None


def _node_outcome(node, tests):
    """由 run 模式報告判斷單一節點結果；任何缺漏都唔會回 passed。"""
    entry = tests.get(node)
    if not isinstance(entry, dict):
        return 'missing', 'run 報告冇此節點'
    phases = entry.get('phases')
    if not isinstance(phases, dict) or not phases:
        return 'unknown', '冇 setup/call/teardown 證據'
    if any(p.get('wasxfail') for p in phases.values() if isinstance(p, dict)):
        return 'xfail', 'wasxfail（XPASS/XFAIL 唔可以當通過）'
    for required in ('setup', 'call', 'teardown'):
        phase = phases.get(required)
        if not isinstance(phase, dict):
            return 'unknown', f'缺少 {required} 階段'
        if phase.get('outcome') != 'passed':
            return phase.get('outcome') or 'unknown', f'{required}={phase.get("outcome")}'
    return 'passed', ''


def run_evidence(reg, run_tests, report_path, timeout_collect, timeout_run):
    """回傳 (報告 dict, 錯誤列表)。任何 subprocess 非零、collection 錯誤、
    未完成 session、缺階段、skip/xfail/XPASS、空 selection 都 fail-closed。
    """
    errors = []
    targets = _binding_targets(reg)
    files = sorted({b.split('::', 1)[0] for _, b in targets})
    report = {
        'generatedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'mode': 'run' if run_tests else 'static',
        'bindings': {},
        'collection': {'files': files, 'collected': []},
        'run': {},
        'failures': [],
    }

    def fail(msg):
        errors.append(msg)
        report['failures'].append(msg)

    # 0) 必須有 required 綁定目標，否則係空 selection
    if not targets:
        fail('冇任何 required 測試綁定目標（空 selection）')

    # 1) collection：解析實際 node ids；subprocess／collection error／未完成一律阻斷
    collect_report = report_path + '.collect.json'
    if os.path.exists(collect_report):
        os.remove(collect_report)
    if targets:
        res, err = _run_pytest(files, 'collect', collect_report, timeout_collect)
        if err:
            fail(err)
        elif res['report'] is None:
            fail('pytest collection 冇產生證據報告：' + res['tail'][-800:])
        else:
            rpt = res['report']
            report['collection']['exitStatus'] = rpt.get('exitStatus')
            report['collection']['collectionErrors'] = rpt.get('collectionErrors', [])
            report['collection']['deselected'] = rpt.get('deselected', [])
            collected = list(rpt.get('collected', []))
            report['collection']['collected'] = collected
            if rpt.get('phase') != 'finished':
                fail('pytest collection session 未完成（報告 phase 唔係 finished）')
            if res['proc'].returncode != 0:
                fail('pytest collection subprocess 退出非零（%s）：%s'
                     % (res['proc'].returncode, res['tail'][-800:]))
            if rpt.get('collectionErrors'):
                fail('pytest collection 有錯誤：%s' % (rpt['collectionErrors'][:3],))
            if not collected:
                fail('pytest collection 冇任何 node（空 selection 或 collection 失敗）')
            if rpt.get('deselected'):
                fail('pytest collection 有 deselected 節點：%s' % rpt['deselected'][:10])
            matched_nodes = set()
            for fid, binding in targets:
                entry = report['bindings'].setdefault(binding, {'features': [], 'matched': []})
                if fid not in entry['features']:
                    entry['features'].append(fid)
                if binding in collected:
                    entry['matched'] = [binding]
                else:
                    prefix = binding + '['
                    entry['matched'] = sorted(n for n in collected if n.startswith(prefix))
                if not entry['matched']:
                    fail(f'{fid} → 綁定 node 唔存在（假 node 或拼錯）：{binding}')
                    entry['status'] = 'missing'
                else:
                    entry['status'] = 'collected'
                matched_nodes.update(entry['matched'])
                ok, reason = check_assertions(binding)
                entry['assertion'] = {'ok': ok, 'reason': reason}
                if not ok:
                    fail(f'{fid} → {reason}')
            report['collection']['matchedNodes'] = sorted(matched_nodes)

    # 2) 實際執行（--run-tests）：任何非零、缺階段、skip/xfail/XPASS 都阻斷
    if run_tests and not errors:
        run_nodes = sorted(report['collection'].get('matchedNodes', []))
        if not run_nodes:
            fail('run 模式冇可執行嘅匹配節點（空 selection）')
        else:
            run_report = report_path + '.run.json'
            if os.path.exists(run_report):
                os.remove(run_report)
            res2, err2 = _run_pytest(run_nodes, 'run', run_report, timeout_run)
            if err2:
                fail(err2)
            elif res2['report'] is None:
                fail('pytest 執行冇產生證據報告：' + res2['tail'][-800:])
            else:
                rpt2 = res2['report']
                tests = rpt2.get('tests') or {}
                report['run'] = {
                    'exitStatus': rpt2.get('exitStatus'),
                    'collectionErrors': rpt2.get('collectionErrors', []),
                    'deselected': rpt2.get('deselected', []),
                    'testscollected': rpt2.get('testscollected'),
                    'testsfailed': rpt2.get('testsfailed'),
                    'tests': tests,
                }
                if rpt2.get('phase') != 'finished' or rpt2.get('exitStatus') is None:
                    fail('pytest 執行 session 未完成（缺 exitStatus／phase）')
                if res2['proc'].returncode != 0:
                    fail('pytest 執行 subprocess 退出非零（%s）：%s'
                         % (res2['proc'].returncode, res2['tail'][-800:]))
                if rpt2.get('collectionErrors'):
                    fail('pytest 執行有 collection 錯誤：%s' % (rpt2['collectionErrors'][:3],))
                if rpt2.get('deselected'):
                    fail('pytest 執行有 deselected 節點：%s' % rpt2['deselected'][:10])
                for binding, entry in report['bindings'].items():
                    if not entry.get('matched'):
                        continue
                    outcomes = []
                    bad = []
                    for node in entry['matched']:
                        status, detail = _node_outcome(node, tests)
                        outcomes.append((node, status, detail))
                        if status == 'passed':
                            continue
                        # 已批准 skip 只可以逐節點豁免 skipped；fail/缺階段永不豁免
                        if status == 'skipped' and node in APPROVED_SKIP:
                            continue
                        bad.append((node, status, detail))
                    entry['outcomes'] = outcomes
                    if bad:
                        entry['status'] = 'failed'
                        fids = ','.join(entry.get('features', [])) or '(unknown)'
                        fail(f'{fids} → {binding} 實測未通過：{bad}')
                    else:
                        entry['status'] = 'passed'

    # 3) 報告寫入：失敗都要非零，且 report.ok 同 errors 一致
    report['ok'] = not errors
    try:
        os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
        with open(report_path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except OSError as e:
        fail(f'證據報告寫入失敗：{e}')
        report['ok'] = False
    return report, errors


def main(argv=None):
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, 'reconfigure'):
            _stream.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Feature Check（Registry + pytest 證據）')
    ap.add_argument('--run-tests', action='store_true',
                    help='實際執行綁定測試並收集 setup/call/teardown、skip/xfail/fail 證據')
    ap.add_argument('--report', default=os.environ.get(
        'FEATURE_CHECK_REPORT', os.path.join(tempfile.gettempdir(), 'aircon-feature-check.json')),
        help='機器報告輸出路徑（預設系統 temp；唔會寫入 repo）')
    ap.add_argument('--timeout', type=int, default=None, help='執行超時秒數（run 模式）')
    args = ap.parse_args(argv)

    with open(GOV_FILE, encoding='utf-8') as fh:
        text = fh.read()
    try:
        blocks = extract_blocks(text)
    except BlockError as e:
        print(f'❌ {e}', file=sys.stderr)
        return 1
    reg = blocks['AIRCON_FEATURE_REGISTRY_V1']

    errors = check_registry_schema(reg)
    if errors:
        print('❌ Registry Schema 驗證失敗：', file=sys.stderr)
        for e in errors:
            print('  -', e, file=sys.stderr)
        return 1

    missing, missing_files = check_test_bindings(reg)
    total = len(reg['features'])
    required = [f for f in reg['features'] if f['protection'] == 'required']
    print(f'✅ Registry 結構 + 內嵌 Schema 有效：{total} 項功能（required {len(required)} 項）')
    if KNOWN_UNBOUND:
        print(f'⚠️ 已知測試綁定缺口（等人類決定實現或降級）：{", ".join(sorted(KNOWN_UNBOUND))}',
              file=sys.stderr)
    if missing:
        print(f'❌ 未申報嘅測試綁定缺口（不得偽造測試名）：{", ".join(missing)}', file=sys.stderr)
        return 1
    if missing_files:
        print('❌ 綁定檔案／格式問題：', file=sys.stderr)
        for m in missing_files:
            print('  -', m, file=sys.stderr)
        return 1

    report, ev_errors = run_evidence(reg, args.run_tests, args.report,
                                     DEFAULT_TIMEOUT_COLLECT,
                                     args.timeout or DEFAULT_TIMEOUT_RUN)
    if ev_errors:
        print('❌ 測試綁定證據失敗：', file=sys.stderr)
        for e in ev_errors:
            print('  -', e, file=sys.stderr)
        print(f'📄 證據報告：{args.report}', file=sys.stderr)
        return 1
    print(f'✅ required 功能綁定全部有實際 pytest collection node id'
          f'（{len(report["collection"].get("matchedNodes", []))} 個節點）')
    if args.run_tests:
        print(f'✅ 綁定測試實際執行全部 passed（無 skip／xfail／fail）')
    else:
        print('ℹ️ 未加 --run-tests：今次只做 collection 與靜態斷言證據，'
              '受測行為證據由 pytest 步驟負責')
    print(f'📄 證據報告：{args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
