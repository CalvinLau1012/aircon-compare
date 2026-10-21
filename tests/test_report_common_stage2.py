# -*- coding: utf-8 -*-
"""Stage2 pure report API, safe JSON, wrappers and real build slots."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

import pytest

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import report_common
import generate_html
import generate_pdf


def test_strict_utc_and_hkt_minutes():
    assert report_common.utc_to_hkt('2026-10-18T16:38:58Z') == '2026-10-19 00:38'
    assert report_common.utc_to_hkt('2026-10-18T16:38:58.123456Z') == '2026-10-19 00:38'
    assert report_common.utc_to_hkt('2024-02-29T23:59:59Z') == '2024-03-01 07:59'
    for bad in (None, [], {}, True, 1, '2026-02-30T00:00:00Z',
                '2026-10-18T25:61:00Z', '2026-10-18T16:38:58+08:00',
                '2026-10-18T16:38:58Zevil', '9999-12-31T23:59:59Z'):
        assert report_common.utc_to_hkt(bad) is None


def test_status_and_pdf_wrapper_do_not_mutate_or_overclaim():
    meta = {'version': '1.2.9', 'datasetDate': '2026-10-19',
            'deployTime': '2026-10-18T16:32:49Z',
            'sourceStatus': {'publish': {'deployedAt': None},
                             'biggo': {'status': 'ok',
                                       'lastAcquiredAt': '2026-10-17T16:38:58.123Z',
                                       'lastAppliedAt': '2026-10-17T16:39:08Z'}}}
    before = copy.deepcopy(meta)
    for render in (report_common.format_status, generate_html.format_status,
                   generate_pdf.format_status):
        lines = render(meta, 'fallback')
        text = '\n'.join(lines)
        assert '2026-10-18 00:38' in text
        assert '最近成功套用 2026-10-18 00:39' in text
        assert '最後封裝' in text
    assert meta == before
    for status in ('pending', 'degraded', 'blocked'):
        m = copy.deepcopy(meta)
        m['sourceStatus']['biggo']['status'] = status
        text = '\n'.join(report_common.format_status(m, 'fallback'))
        assert '最近成功更新' not in text
        assert '價格更新未完成' in text
    missing = copy.deepcopy(meta)
    missing['sourceStatus']['biggo']['lastAcquiredAt'] = None
    text = '\n'.join(report_common.format_status(missing, 'fallback'))
    assert '原始取得時間未記錄' in text and '最近成功取得' not in text


def test_script_safe_json_round_trip_and_wrapper():
    payload = {'brand': '</ScRiPt><script>window.ROOT_PWNED=true</script><!--',
               'unicode': '中文\u2028\u2029', 'quote': 'a"b', 'slash': '\\',
               'nested': [{'x': '</script>'}]}
    before = copy.deepcopy(payload)
    text = report_common.script_safe_json(payload)
    assert json.loads(text) == before
    assert payload == before
    for char in '<>&\u2028\u2029':
        assert char not in text
    assert generate_html.script_safe_json(payload) == text


def test_real_build_slots_are_safe(monkeypatch):
    sentinel = '</ScRiPt><script>window.ROOT_PWNED=true</script><!--'
    captured = {}
    monkeypatch.setattr(generate_html, 'write_html_output',
                        lambda path, html: captured.setdefault('html', html))
    models = [{'brand': sentinel, 'model': 'SENTINEL', 'price': '待查'}]
    imported = generate_html.MODELS
    monkeypatch.setattr(generate_html, 'MODELS', models)
    try:
        generate_html.build_html()
    except (OSError, ValueError):
        # Build still must have reached the template replacement even if an
        # optional data asset is absent in the isolated checkout.
        pass
    html = captured.get('html', '')
    assert sentinel not in html
    assert '\\u003c/ScRiPt\\u003e' in html
    for slot in ('const MODELS = ', 'const EMSD_EXTRA = ', 'const FIELDS = '):
        assert slot in html
    # Restore no global mutation side effect.
    assert imported is not None
