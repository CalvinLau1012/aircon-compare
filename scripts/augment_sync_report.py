#!/usr/bin/env python3
"""Bind a completed-sync machine report to this source run/head."""
import argparse, json, os, re, sys
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--report',required=True)
    ap.add_argument('--run-id',required=True); ap.add_argument('--head',required=True)
    ap.add_argument('--event',required=True); a=ap.parse_args(argv)
    if not re.fullmatch(r'[0-9]+',a.run_id) or not re.fullmatch(r'[0-9a-f]{40}',a.head) \
            or a.event not in ('schedule','workflow_dispatch'):
        print('❌ invalid source identity',file=sys.stderr); return 2
    try: d=json.load(open(a.report,encoding='utf-8'))
    except (OSError,ValueError): print('❌ sync report missing/invalid',file=sys.stderr); return 2
    if not isinstance(d,dict): print('❌ sync report not object',file=sys.stderr); return 2
    d.update({'sourceRunId':int(a.run_id),'sourceEvent':a.event,'headSha':a.head})
    tmp=a.report+'.tmp'
    with open(tmp,'w',encoding='utf-8',newline='\n') as f:
        json.dump(d,f,ensure_ascii=False,sort_keys=True,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,a.report); return 0
if __name__=='__main__': sys.exit(main())
