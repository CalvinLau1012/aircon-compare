#!/usr/bin/env python3
import json, os, sys
p = sys.argv[1] if len(sys.argv) > 1 else ''
try:
    d = json.load(open(p, encoding='utf-8'))
except Exception:
    print('## Freshness / source monitor\n- report: missing or invalid')
    sys.exit(0)
c = d.get('check') or {}
print('## Freshness / source monitor')
print('- action:', d.get('action'))
print('- freshness reason:', c.get('reason'))
print('- age stale:', bool(c.get('stale')))
print('- postdeploy ok:', c.get('postdeployOk'))
print('- source health ok:', c.get('healthSummaryOk'))
print('- source health reason:', c.get('healthReason'))
print('- provider request by monitor: no')
print('- mode: scheduled/manual freshness monitor; provider not triggered')
