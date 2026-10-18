#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render the monitor's safe three-axis summary; never writes provider state."""
import json
import sys

from monitor_presentation import render_monitor_summary, valid_report_shape


def _unknown():
    print('## 資料新鮮度、部署與來源健康監控')
    print('- report: missing or invalid')
    print('- 資料過期 (freshness): unknown — 未確認（無有效 monitor 報告）')
    print('- 部署一致性 (postdeploy): unknown — 未確認（無有效 monitor 報告）')
    print('- 來源健康 (source health): unknown — 未確認（無有效 monitor 報告）')
    print('- provider request by monitor: no')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    path = argv[0] if argv else ''
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        _unknown()
        return 1
    if not valid_report_shape(data):
        _unknown()
        return 1
    print(render_monitor_summary(data), end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
