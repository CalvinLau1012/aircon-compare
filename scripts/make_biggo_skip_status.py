#!/usr/bin/env python3
"""Write a run-bound, zero-provider BigGo status for repair/rebuild modes."""
import argparse, json, os, re, sys

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',required=True); ap.add_argument('--mode',required=True)
    ap.add_argument('--run-id',required=True); ap.add_argument('--head',required=True)
    ap.add_argument('--event',required=True)
    a=ap.parse_args(argv)
    if a.mode not in ('repair_release','rebuild_verified_snapshot'):
        print('❌ invalid skip mode',file=sys.stderr); return 2
    if not re.fullmatch(r'[0-9]+',a.run_id) or not re.fullmatch(r'[0-9a-f]{40}',a.head):
        print('❌ invalid run identity',file=sys.stderr); return 2
    if a.event not in ('schedule','workflow_dispatch'):
        print('❌ invalid event',file=sys.stderr); return 2
    status={'schemaVersion':1,'status':'skipped-no-provider','alert':False,
            'mode':a.mode,'sourceRunId':int(a.run_id),'sourceEvent':a.event,
            'headSha':a.head,'providerPhaseEntered':False,'leaseAcquired':False,
            'published':False,'imported':False,'localApply':'skipped',
            'requests':{'token':0,'search':0},
            'reason':'mode explicitly skips provider/lease'}
    tmp=a.out+'.tmp'
    with open(tmp,'w',encoding='utf-8',newline='\n') as f:
        json.dump(status,f,ensure_ascii=False,sort_keys=True,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,a.out); print('✅ provider-skip status:',a.mode); return 0
if __name__=='__main__': sys.exit(main())
