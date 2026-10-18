#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure shared report facts for HTML and PDF.

This module reads no files, clock, network or environment.  It only converts
already-supplied metadata values into deterministic HKT text and safe script
JSON.  Environment-specific wording is passed by the thin HTML adapters.
"""
from __future__ import annotations

import datetime as _dt
import json
import re

_UTC_RE = re.compile(
    r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z$')
_SHA_RE = re.compile(r'^sha256:[0-9a-f]{64}$')

PUBLIC_SCHEDULE_TEXT = (
    '🔄 每日 00:30 偵測新機（EMSD 官方資料庫；GitHub Actions 排程屬預定觸發；'
    '高負載可延遲或丟棄，非準時保證）· 有新機先分批核實更新 · '
)


def parse_utc(value):
    """Strict UTC Z parser; returns an aware datetime or None.

    Regex rejects offsets/trailing text; datetime construction validates real
    calendar dates, leap days and clock ranges.  Fractional seconds are
    accepted but not needed for minute display.
    """
    if not isinstance(value, str):
        return None
    m = _UTC_RE.fullmatch(value)
    if not m:
        return None
    y, mo, d, h, mi, se = (int(x) for x in m.groups()[:6])
    if not (1 <= y <= 9999 and 1 <= mo <= 12 and 1 <= d <= 31
            and 0 <= h <= 23 and 0 <= mi <= 59 and 0 <= se <= 59):
        return None
    try:
        return _dt.datetime(y, mo, d, h, mi, se, tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def hkt_from_datetime(value):
    """Format a datetime as HKT minute text; malformed input returns None."""
    if not isinstance(value, _dt.datetime):
        return None
    if value.tzinfo is None or value.utcoffset() != _dt.timedelta(0):
        return None
    try:
        return (value + _dt.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M')
    except (OverflowError, ValueError):
        return None


def utc_to_hkt(value):
    """Return HKT minute text for a strict UTC Z value, otherwise None."""
    return hkt_from_datetime(parse_utc(value))


def _dataset_date(value):
    return value if isinstance(value, str) and value.strip() else None


def _biggo_text(source):
    """Render the existing BigGo provenance wording without inventing success."""
    biggo_text = '🛒 BigGo 更新時間未確認（分批快照）'
    try:
        b = (source or {}).get('biggo') or {}
        status = b.get('status')
        acquired = utc_to_hkt(b.get('lastAcquiredAt'))
        applied = utc_to_hkt(b.get('lastAppliedAt'))
        if status == 'ok' and acquired:
            biggo_text = f'🛒 BigGo 最近成功更新 {acquired} HKT（分批快照）'
            if applied:
                biggo_text += f'；已套用 {applied} HKT'
        elif status == 'ok' and applied:
            biggo_text = (f'🛒 BigGo 取得時間未確認；已套用 {applied} HKT'
                          '（分批快照，非全部同時查價）')
        elif status in ('degraded', 'blocked', 'pending'):
            prefix = {'degraded': '⚠️ BigGo 價格來源降級',
                      'blocked': '⛔ BigGo 價格來源受阻',
                      'pending': '⏳ BigGo 本輪待更新'}[status]
            if acquired:
                prefix += f'；先前快照取得 {acquired} HKT'
            elif applied:
                prefix += f'；已套用 {applied} HKT（取得時間未確認）'
            biggo_text = prefix + ' · 價格更新未完成，沿用先前已驗證快照'
        elif acquired or applied:
            biggo_text = f'🛒 BigGo 更新時間 {acquired or applied} HKT（分批快照）'
    except (AttributeError, TypeError):
        pass
    return biggo_text


def format_status(meta_dict, version, *, schedule_text=None):
    """Pure metadata -> (hero status line, footer status line).

    Acquisition and apply are separate: a deployment/package timestamp never
    becomes an acquisition time.  A missing/invalid time is unknown.  Pending,
    degraded and blocked are not rendered as success.  The caller supplies the
    environment-specific schedule sentence; the default is the public wording.
    """
    meta = meta_dict if isinstance(meta_dict, dict) else {}
    schedule = schedule_text if isinstance(schedule_text, str) else PUBLIC_SCHEDULE_TEXT
    dataset_date = _dataset_date(meta.get('datasetDate'))
    v = meta.get('version') or version
    deploy_hkt = utc_to_hkt(meta.get('deployTime'))
    date_text = (f'📅 EMSD 資料日期 {dataset_date}' if dataset_date
                 else '📅 EMSD 資料日期暫不可用')
    source = meta.get('sourceStatus')
    publish = (source or {}).get('publish') if isinstance(source, dict) else {}
    publish = publish if isinstance(publish, dict) else {}
    deployed_actual = bool(publish.get('deployedAt'))
    deploy_label = '最後部署' if (source is None or deployed_actual) else '最後封裝'
    deploy_text = (f'📦 {deploy_label} {deploy_hkt} HKT' if deploy_hkt
                   else '🔄 每日偵測新機 · 有機先更新')
    line1 = f'{date_text} · v{v} · {deploy_text}'
    biggo_text = _biggo_text(source if isinstance(source, dict) else {})
    line2 = (schedule
             + (f'EMSD 資料日期 {dataset_date}' if dataset_date else 'EMSD 資料日期暫不可用')
             + ' · ' + biggo_text
             + ' · 部署時間為封包時間（HKT 顯示）· 價錢為快照僅供參考')
    return line1, line2


def script_safe_json(obj):
    """Compact JSON that cannot close an HTML script element."""
    text = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    return (text.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
