#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure human presentation for the freshness/deployment/source-health monitor.

This module deliberately imports no monitor/runtime module.  It formats only
closed-allowlist facts and never decides whether an alert may be closed.
"""
from __future__ import annotations

import re

_ACTIONS = {'noop', 'create', 'update', 'close', 'pending', 'blocked-identity',
            'alert', 'recovered', 'none', 'unknown'}
_EVENTS = {'schedule', 'workflow_dispatch', 'workflow_run', 'repository_dispatch'}
_HEALTH_CODES = {
    'health:biggo', 'health:official', 'health:emsd', 'health:integrity',
    'health:fetch', 'health:unconfigured', 'health:unhealthy',
    'health:unknown', 'health:identity-invalid', 'health:identity-mismatch',
    'health:invalid',
}
_REASON_CODES = {
    'fresh', 'stale_over_72h', 'missing_or_invalid_datasetRetrievedAt',
    'future_datasetRetrievedAt', 'metadata_not_object',
    'postdeploy_report_missing', 'postdeploy_report_invalid',
    'health_report_missing', 'health_report_invalid',
    'invalid-source-identity', 'fetch_error', 'health-summary-not-wired',
    'unknown',
}
_HEALTH_LABELS = {
    'health:biggo': 'BigGo 來源未健康；本輪未完成／請查來源收據',
    'health:official': 'official 來源未健康／待核；本輪未完成／請查來源收據',
    'health:emsd': 'EMSD 來源未健康；本輪未完成／請查來源收據',
    'health:integrity': '來源完整性／manifest 驗證未通過',
    'health:fetch': '來源健康摘要無法取得',
    'health:unconfigured': '來源健康摘要未配置',
    'health:unhealthy': '來源整體 unhealthy',
    'health:unknown': '來源狀態未確認',
    'health:identity-invalid': '來源健康報告身份無效',
    'health:identity-mismatch': '來源健康報告身份不符',
    'health:invalid': '來源健康報告格式無效',
}
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
_PRESERVE_REASONS = {
    # These describe retention, not a current integrity-verification witness.
    # A reason can survive alongside a corrupt/stale health summary.
    'price-snapshot-preserved', 'completed-sync-remote-not-completed',
}
_STATUS_LABEL = {'ok': '健康', 'pending': '待更新', 'degraded': '降級',
                 'blocked': '受阻', 'unknown': '未確認'}
_REASON_LABEL = {
    'awaiting-source': '等待來源', 'sources-degraded': '來源部分失敗',
    'price-snapshot-preserved': '先前價格快照保留',
    'quota-or-lease-blocked': '配額／lease 受阻',
    'completed-sync-remote-not-completed': '遠端尚未完成新一輪',
    'completed-sync-missing-history': '缺少完成歷史',
    'completed-sync-unavailable': '完成 bundle 暫時不可用',
    'completed-sync-invalid': '完成 bundle 無效',
    'coverage-pending': '官網規格待核（已驗證來源收據；舊快照保留）',
    'official-coverage-pending-untrusted': '官網待核收據未通過驗證',
    'official-source-failed': '官網來源失敗',
    'legacy-unknown': '歷史狀態未確認', 'unknown': '未確認',
}


def _safe_action(value):
    return value if type(value) is str and value in _ACTIONS else 'unknown'


def _safe_event(value):
    return value if type(value) is str and value in _EVENTS else 'unknown'


def _safe_positive_int(value):
    return value if type(value) is int and value > 0 else 'unknown'


def _safe_head(value):
    return value if type(value) is str and re.fullmatch(r'[0-9a-f]{40}', value) else 'unknown'


def _safe_bool(value):
    return value if type(value) is bool else None


def _safe_fingerprint(value):
    if type(value) is str and re.fullmatch(r'[a-z0-9_:+.-]{1,200}', value) \
            and value.startswith(('fresh', 'stale_over_72h', 'invalid:')):
        return value
    return 'unknown'


def _safe_reason(value):
    if type(value) is not str:
        return 'unknown'
    if value in _REASON_CODES:
        return value
    if value.startswith('fetch_error:'):
        return 'fetch_error'
    return 'unknown'


def _health_tokens(reason):
    if type(reason) is not str or not reason:
        return []
    out = []
    for token in reason.split(','):
        token = token.strip()
        if token.startswith('sources:'):
            token = 'health:' + token.split(':', 1)[1]
        if token in _HEALTH_CODES and token not in out:
            out.append(token)
    return out


def sanitize_source_states(raw):
    """Return only producer-safe status/safeReason fields; never raw extras."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name in _SOURCE_NAMES:
        block = raw.get(name)
        if not isinstance(block, dict):
            continue
        status = block.get('status')
        reason = block.get('safeReason')
        if type(status) is not str or status not in _SOURCE_STATUSES:
            continue
        if type(reason) is not str or reason not in _SAFE_REASONS:
            continue
        out[name] = {'status': status, 'safeReason': reason}
    return out


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
        if not isinstance(block, dict) or set(block) != {'status', 'safeReason'}:
            lines.append(f'{name}: unknown（來源摘要格式未確認）')
            continue
        status = block.get('status')
        reason = block.get('safeReason')
        if type(status) is not str or status not in _SOURCE_STATUSES:
            lines.append(f'{name}: unknown（來源狀態未確認）')
            continue
        if type(reason) is not str or reason not in _SAFE_REASONS:
            lines.append(f'{name}: unknown（來源原因未確認）')
            continue
        detail = _REASON_LABEL.get(reason, '安全原因已記錄')
        line = f'{name}: {_STATUS_LABEL[status]}；{detail}'
        if status in ('pending', 'degraded') and reason in _PRESERVE_REASONS:
            line += '；先前快照保留，核驗狀態見來源收據'
        lines.append(line)
    return lines


def _freshness_axis(result):
    ok = _safe_bool(result.get('ok'))
    stale = _safe_bool(result.get('stale'))
    reason = _safe_reason(result.get('reason'))
    if ok is not True or reason in {'missing_or_invalid_datasetRetrievedAt',
                                    'future_datasetRetrievedAt', 'fetch_error'}:
        return 'unknown', '未確認（缺少或無效的取得時間）'
    if stale is True:
        return 'yes', '已超過 72 小時（資料過期）' if reason == 'stale_over_72h' else '資料新鮮度失敗'
    if result.get('deploymentMismatch') is True:
        return 'no', '資料取得時間仍新鮮；部署版本未一致（不歸類為資料過期）'
    return 'no', '未超過 72 小時（資料新鮮）'


def _deployment_axis(result):
    post = _safe_bool(result.get('postdeployOk'))
    mismatch = _safe_bool(result.get('deploymentMismatch'))
    if post is False and result.get('postdeployKnown') is False:
        return 'unknown', '未確認（缺少或無效的部署核對收據）'
    if post is True and mismatch is not True:
        return 'no', '部署核對通過'
    if post is False or mismatch is True:
        return 'yes', '部署核對失敗／未上線版本不一致'
    return 'unknown', '未確認（缺少或無效的部署核對收據）'


def _source_axis(result):
    health = _safe_bool(result.get('healthSummaryOk'))
    if health is False and result.get('healthKnown') is False:
        return 'unknown', '未確認（缺少或無效的來源健康收據）'
    if health is True and result.get('healthSummaryPartial') is True:
        count = result.get('healthPendingCount')
        count = count if type(count) is int and count > 0 else 'unknown'
        return 'warn', f'官網規格待核 {count} 個（已驗證收據；非資料過期）'
    if health is True:
        return 'no', '來源健康；沒有來源失敗'
    if health is False:
        return 'yes', '來源部分失敗；本輪未完成／請查來源收據'
    return 'unknown', '未確認（缺少或無效的來源健康收據）'


def _health_details(result):
    if result.get('healthSummaryOk') is True:
        if result.get('healthSummaryPartial') is True:
            count = result.get('healthPendingCount')
            count = count if type(count) is int and count > 0 else 'unknown'
            return [f'官網規格待核 {count} 個：來源收據／舊 manifest 已驗證，'
                    'queue 保留，不是資料過期或來源硬失敗']
        return []
    tokens = _health_tokens(result.get('healthReason'))
    return [_HEALTH_LABELS[t] for t in tokens] or ['來源健康狀態 unknown']


def axes(result):
    if not isinstance(result, dict):
        result = {}
    f = _freshness_axis(result)
    d = _deployment_axis(result)
    s = _source_axis(result)
    details = _health_details(result) + _source_state_lines(result)
    return {'freshness': f, 'postdeploy': d, 'sourceHealth': s,
            'sourceDetails': details}


def _safe_age(value):
    return value if type(value) in (int, float) and not isinstance(value, bool) else 'unknown'


def render_issue_body(result, fingerprint):
    """Compatibility issue body plus clear three-axis human summary."""
    result = result if isinstance(result, dict) else {}
    f, fd = _freshness_axis(result)
    d, dd = _deployment_axis(result)
    s, sd = _source_axis(result)
    details = _health_details(result) + _source_state_lines(result)
    fp = _safe_fingerprint(fingerprint)
    failures = result.get('deploymentFailures')
    failure_count = len(failures) if isinstance(failures, list) else 0
    health_tokens = ','.join(_health_tokens(result.get('healthReason'))) or 'unknown'
    return (
        f'<!-- aircon-freshness-fingerprint: {fp} -->\n'
        '\n'
        'Freshness／部署一致性／來源健康 monitor alert.\n'
        '\n'
        f'- category: `{fp}`\n'
        f'- 資料過期 (freshness): {f}\n'
        f'- 待發布版本未上線／部署核對 (postdeploy): {d}\n'
        f'- postdeploy failure: {d}\n'
        f'- 來源部分失敗 (source health): {s}\n'
        f'- 官網規格待核 (warning): {_safe_bool(result.get("healthSummaryPartial"))}\n'
        f'- freshness detail: {fd}\n'
        f'- deployment detail: {dd}\n'
        f'- source health detail: {sd}\n'
        f'- source health components: {", ".join(details)}\n'
        f'- reason: `{_safe_reason(result.get("reason"))}`\n'
        f'- ageSeconds: {_safe_age(result.get("ageSeconds"))}\n'
        f'- age stale: {_safe_bool(result.get("stale"))}\n'
        f'- deployment mismatch: {_safe_bool(result.get("deploymentMismatch"))}\n'
        f'- deployment failure count: {failure_count}\n'
        f'- source health reason: `{health_tokens}`\n'
        '- threshold: age > 72h\n'
    )


def render_monitor_summary(report):
    """Safe, human-readable summary; unknown actions/identity are never echoed."""
    report = report if isinstance(report, dict) else {}
    check = report.get('check') if isinstance(report.get('check'), dict) else {}
    a = axes(check)
    lines = [
        '## 資料新鮮度、部署與來源健康監控',
        f"- action: `{_safe_action(report.get('action'))}`",
        f"- planned action: `{_safe_action(report.get('plannedAction'))}`",
        f"- applied action: `{_safe_action(report.get('appliedAction'))}`",
        f"- source run: `{_safe_positive_int(report.get('sourceRunId'))}`",
        f"- head: `{_safe_head(report.get('headSha'))}`",
        f"- source event: `{_safe_event(report.get('sourceEvent'))}`",
        f"- 資料過期 (freshness): **{a['freshness'][0]}** — {a['freshness'][1]}",
        f"- 部署一致性 (postdeploy): **{a['postdeploy'][0]}** — {a['postdeploy'][1]}",
        f"- 來源健康 (source health): **{a['sourceHealth'][0]}** — {a['sourceHealth'][1]}",
    ]
    for detail in a['sourceDetails']:
        lines.append('- ' + detail)
    lines.append('- provider request by monitor: no')
    return '\n'.join(lines) + '\n'


def valid_report_shape(report):
    """Minimum machine-report shape for the summary CLI; no identity trust."""
    if not isinstance(report, dict):
        return False
    check = report.get('check')
    return (isinstance(check, dict) and bool(check)
            and type(check.get('ok')) is bool
            and type(check.get('stale')) is bool
            and type(check.get('reason')) is str)
