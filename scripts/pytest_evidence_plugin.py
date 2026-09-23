# -*- coding: utf-8 -*-
"""pytest 證據收集外掛（feature-check 專用；唔會執行額外測試）

用法（由 scripts/feature-check.py 呼叫）：
  PYTHONPATH=scripts python -m pytest -p pytest_evidence_plugin \
      --evidence-report <path> --evidence-mode collect|run <targets>

- collect 模式：只收集 node ids 同 collection 錯誤（配合 `--collect-only`）。
- run 模式：喺 pytest_runtest_logreport 收集 setup/call/teardown 三階段結果，
  包括 passed / failed / skipped / xfail / xpass / error。
- 兩種模式都喺 pytest_sessionfinish 寫入 exitStatus、collectionErrors、
  deselected、testscollected、testsfailed；collection 失敗唔會被當成功。
- 報告路徑由 CLI 指定；唔寫入 repo。任何未預期例外只會令報告缺漏，唔會
  靜默當成功（feature-check 見唔到報告會直接失敗）。
"""
import json
import os
import time

_REPORT_PATH = None
_MODE = 'run'
_STATE = {
    'mode': None,
    'phase': 'start',
    'startedAt': None,
    'finishedAt': None,
    'collected': [],
    'collectionErrors': [],
    'deselected': [],
    'testscollected': 0,
    'testsfailed': 0,
    'exitStatus': None,
    'tests': {},
}


def pytest_addoption(parser):
    parser.addoption('--evidence-report', action='store', default=None,
                     help='證據 JSON 輸出路徑（feature-check 專用）')
    parser.addoption('--evidence-mode', action='store', choices=['collect', 'run'],
                     default='run', help='collect=只收集 node ids；run=收集執行結果')


def pytest_configure(config):
    global _REPORT_PATH, _MODE
    _REPORT_PATH = config.getoption('--evidence-report') or os.environ.get('EVIDENCE_REPORT')
    _MODE = config.getoption('--evidence-mode')
    _STATE['mode'] = _MODE
    _STATE['startedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def pytest_collectreport(report):
    """收藏集錯誤（import error、語法錯、目錄問題等）。"""
    if report.failed and report.when == 'collect':
        _STATE['collectionErrors'].append({
            'nodeid': getattr(report, 'nodeid', ''),
            'longrepr': str(getattr(report, 'longrepr', ''))[:1000],
        })


def pytest_collection_finish(session):
    _STATE['collected'] = sorted({item.nodeid for item in session.items})
    _STATE['phase'] = 'collected'


def pytest_deselected(items):
    _STATE['deselected'].extend(
        sorted({getattr(item, 'nodeid', str(item)) for item in items}))


def pytest_runtest_logreport(report):
    if _MODE != 'run':
        return
    entry = _STATE['tests'].setdefault(report.nodeid, {'phases': {}})
    phase = entry['phases'].setdefault(report.when, {})
    phase['outcome'] = report.outcome
    if getattr(report, 'wasxfail', None):
        phase['wasxfail'] = report.wasxfail
    if report.outcome == 'failed':
        phase['longrepr'] = str(report.longrepr)[:2000]
    if report.outcome == 'skipped':
        phase['reason'] = str(getattr(report, 'longrepr', ''))[:500]
    phase['duration'] = round(getattr(report, 'duration', 0.0), 4)


def pytest_sessionfinish(session, exitstatus):
    _STATE['phase'] = 'finished'
    _STATE['finishedAt'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    _STATE['exitStatus'] = int(exitstatus)
    _STATE['testscollected'] = int(getattr(session, 'testscollected', len(_STATE['collected'])))
    _STATE['testsfailed'] = int(getattr(session, 'testsfailed', 0))
    for entry in _STATE['tests'].values():
        outcomes = [p.get('outcome') for p in entry['phases'].values()]
        if 'failed' in outcomes or 'error' in outcomes:
            entry['result'] = 'failed'
        elif 'skipped' in outcomes:
            entry['result'] = 'skipped'
        elif 'passed' in outcomes:
            entry['result'] = 'passed'
        else:
            entry['result'] = 'unknown'
    _write_report()


def _write_report():
    if not _REPORT_PATH:
        return
    os.makedirs(os.path.dirname(os.path.abspath(_REPORT_PATH)), exist_ok=True)
    tmp = _REPORT_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(_STATE, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _REPORT_PATH)
