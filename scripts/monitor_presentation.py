#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure human presentation for the freshness/deployment/source-health monitor.

This module deliberately imports no monitor/runtime module.  It only formats
already-validated facts; it never decides whether an alert may be closed.
"""
from __future__ import annotations

import datetime as _dt
import re

_SAFE_CODE = re.compile(r'^health:[a-z0-9_-]+$')


def _safe_health_details(reason):
    if not isinstance(reason, str) or not reason:
        return []
    out = []
    for token in reason.split(','):
        token = token.strip()
        if _SAFE_CODE.fullmatch(token) and token not in out:
            out.append(token)
    return out


def _health_known(token):
    return token in {
        'health:biggo', 'health:official', 'health:emsd', 'health:integrity',
        'health:fetch', 'health:unconfigured', 'health:unhealthy',
        'health:unknown', 'health:identity-invalid', 'health:identity-mismatch',
        'health:invalid',
    }


def _freshness_axis(result):
    ok = result.get('ok')
    stale = result.get('stale')
    reason = str(result.get('reason') or '')
    if ok is not True or reason.startswith(('missing_or_invalid', 'future_', 'fetch_error')):
        return 'unknown', '未確認（缺少或無效的取得時間）'
    if stale is True:
        return 'yes', '已超過 72 小時（資料過期）' if reason == 'stale_over_72h' else '資料新鮮度失敗'
    if result.get('deploymentMismatch'):
        return 'no', '資料取得時間仍新鮮；部署版本未一致（不歸類為資料過期）'
    return 'no', '未超過 72 小時（資料新鮮）'


def _deployment_axis(result):
    post = result.get('postdeployOk')
    mismatch = bool(result.get('deploymentMismatch'))
    if post is True and not mismatch:
        return 'no', '部署核對通過'
    if post is False or mismatch:
        return 'yes', '部署核對失敗／未上線版本不一致'
    return 'unknown', '未確認（缺少或無效的部署核對收據）'


def _source_axis(result):
    health = result.get('healthSummaryOk')
    if health is True:
        return 'no', '來源健康；沒有來源失敗'
    if health is False:
        return 'yes', '來源部分失敗；BigGo／official 未健康時沿用先前已驗證快照'
    return 'unknown', '未確認（缺少或無效的來源健康收據）'


def _health_details(result):
    tokens = _safe_health_details(result.get('healthReason'))
    labels = {
        'health:biggo': 'BigGo 來源未健康（沿用先前已驗證快照）',
        'health:official': 'official 來源未健康／待核（沿用先前已驗證快照）',
        'health:emsd': 'EMSD 來源未健康',
        'health:integrity': '來源完整性／manifest 驗證未通過',
        'health:fetch': '來源健康摘要無法取得',
        'health:unconfigured': '來源健康摘要未配置',
        'health:unhealthy': '來源整體 unhealthy',
        'health:identity-invalid': '來源健康報告身份無效',
        'health:identity-mismatch': '來源健康報告身份不符',
        'health:invalid': '來源健康報告格式無效',
    }
    return [labels.get(t, '來源健康狀態 unknown') for t in tokens if _health_known(t)] or ['來源健康狀態 unknown']


_SOURCE_NAMES = ('emsd', 'biggo', 'official')
_SOURCE_STATUSES = {'ok', 'pending', 'degraded', 'blocked', 'unknown'}
_SAFE_REASONS = {
    'fresh', 'no-fetch-needed', 'acquired', 'applied', 'legacy-unknown',
    'awaiting-source', 'sources-degraded', 'price-snapshot-preserved',
    'price-apply-failed', 'needs-review', 'quota-or-lease-blocked',
    'receipt-missing', 'receipt-invalid', 'metadata-missing',
    'official-snapshot-untrusted', 'official-success-unverified',
    'official-no-verified-snapshot', 'completed-sync-missing-history',
    'completed-sync-unavailable', 'completed-sync-invalid',
    'completed-sync-remote-not-completed', 'baseline-accepted',
}
_STATUS_LABEL = {'ok': '健康', 'pending': '待更新', 'degraded': '降級／沿用先前快照',
                 'blocked': '受阻', 'unknown': '未確認'}
_REASON_LABEL = {
    'awaiting-source': '等待來源', 'sources-degraded': '來源部分失敗',
    'price-snapshot-preserved': '沿用先前已驗證價格快照',
    'quota-or-lease-blocked': '配額／lease 受阻',
    'completed-sync-remote-not-completed': '远端尚未完成新一輪',
    'completed-sync-missing-history': '缺少完成歷史',
    'completed-sync-unavailable': '完成 bundle 暫時不可用',
    'completed-sync-invalid': '完成 bundle 無效',
    'legacy-unknown': '歷史狀態未確認', 'unknown': '未確認',
}

def _source_state_lines(result):
    raw = result.get('sourceStates')
    if raw is None:
        return []
    if not isinstance(raw, dict) or not raw or not set(raw).issubset(_SOURCE_NAMES):
        return ['來源狀態：unknown（來源摘要格式未確認）']
    lines = []
    for name in _SOURCE_NAMES:
        block = raw.get(name)
        if block is None:
            continue
        if not isinstance(block, dict) or set(block) - {'status', 'safeReason'}:
            lines.append(f'{name}: unknown（來源摘要格式未確認）')
            continue
        st = block.get('status')
        reason = block.get('safeReason')
        if st not in _SOURCE_STATUSES or reason not in _SAFE_REASONS:
            lines.append(f'{name}: unknown（來源狀態或原因未確認）')
            continue
        lines.append(f'{name}: {_STATUS_LABEL[st]}；{_REASON_LABEL.get(reason, "已記錄安全原因")}')
    return lines


def axes(result):
    f = _freshness_axis(result)
    d = _deployment_axis(result)
    s = _source_axis(result)
    details = _health_details(result)
    details += _source_state_lines(result)
    return {'freshness': f, 'postdeploy': d, 'sourceHealth': s,
            'sourceDetails': details}


def render_issue_body(result, fingerprint):
    """Compatibility issue body plus clear three-axis human summary."""
    f, fd = _freshness_axis(result)
    d, dd = _deployment_axis(result)
    s, sd = _source_axis(result)
    details = _health_details(result) + _source_state_lines(result)
    return (
        f'<!-- aircon-freshness-fingerprint: {fingerprint} -->\n'
        '\n'
        'Freshness／部署一致性／來源健康 monitor alert.\n'
        '\n'
        f'- category: `{fingerprint}`\n'
        f'- 資料過期 (freshness): {f}\n'
        f'- 待發布版本未上線／部署核對 (postdeploy): {d}\n'
        f'- postdeploy failure: {d}\n'
        f'- 來源部分失敗 (source health): {s}\n'
        f'- freshness detail: {fd}\n'
        f'- deployment detail: {dd}\n'
        f'- source health detail: {sd}\n'
        f'- source health components: {", ".join(details)}\n'
        f'- reason: `{str(result.get("reason") or "unknown")[:120]}`\n'
        f'- ageSeconds: {result.get("ageSeconds")}\n'
        f'- age stale: {bool(result.get("stale"))}\n'
        f'- deployment mismatch: {bool(result.get("deploymentMismatch"))}\n'
        f'- deployment failures: {result.get("deploymentFailures") or []}\n'
        f'- source health reason: `{str(result.get("healthReason") or "unknown")[:120]}`\n'
        '- threshold: age > 72h\n'
    )


def render_monitor_summary(report):
    """Safe, human-readable summary used by the monitor workflow CLI."""
    report = report if isinstance(report, dict) else {}
    check = report.get('check') if isinstance(report.get('check'), dict) else {}
    a = axes(check)
    lines = [
        '## 資料新鮮度、部署與來源健康監控',
        f"- action: `{report.get('action') or 'unknown'}`",
        f"- planned action: `{report.get('plannedAction') or 'unknown'}`",
        f"- applied action: `{report.get('appliedAction') or 'none'}`",
        f"- source run: `{report.get('sourceRunId') or 'unknown'}`",
        f"- head: `{report.get('headSha') or 'unknown'}`",
        f"- 資料過期 (freshness): **{a['freshness'][0]}** — {a['freshness'][1]}",
        f"- 部署一致性 (postdeploy): **{a['postdeploy'][0]}** — {a['postdeploy'][1]}",
        f"- 來源健康 (source health): **{a['sourceHealth'][0]}** — {a['sourceHealth'][1]}",
    ]
    for detail in a['sourceDetails']:
        lines.append('- ' + detail)
    lines.append('- provider request by monitor: no')
    return '\n'.join(lines) + '\n'
