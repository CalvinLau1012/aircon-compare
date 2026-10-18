#!/usr/bin/env python3
import json, sys
p=sys.argv[1] if len(sys.argv)>1 else ''
try: d=json.load(open(p,encoding='utf-8'))
except Exception:
    print('## Completed BigGo receive\n- report: missing or invalid'); sys.exit(0)
print('## Completed BigGo receive')
for k in ('sourceRunId','headSha','cycleId','stage','ok','applied','reason','postStateVerified','bundleHash','newSnapshotHash','bundleErrorCode'):
    print(f'- {k}: {d.get(k)}')
bi=d.get('bundleImport') or {}
print('- stateRefsPresent:',bi.get('stateRefsPresent'))
print('- configuredSnapshotDirMatchesState:',bi.get('configuredSnapshotDirMatchesState'))
print('- provider request by receiver: no')
