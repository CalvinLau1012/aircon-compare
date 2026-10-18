#!/usr/bin/env python3
"""Bind monitor reports to one source run/head/event; preserve invalid evidence safely."""
import argparse, json, os, re, sys

def _one(path, run_id, head, event):
    try:
        d=json.load(open(path,encoding='utf-8'))
    except Exception:
        d={'ok':False,'status':'unknown','reason':'report-missing-or-invalid'}
    if not isinstance(d,dict):
        d={'ok':False,'status':'unknown','reason':'report-not-object'}
    d.update({'sourceRunId':int(run_id),'headSha':head,'sourceEvent':event})
    tmp=path+'.tmp'
    with open(tmp,'w',encoding='utf-8',newline='\n') as f:
        json.dump(d,f,ensure_ascii=False,sort_keys=True,indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp,path)

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--report',action='append',required=True)
    ap.add_argument('--run-id',required=True); ap.add_argument('--head',required=True)
    ap.add_argument('--event',required=True); a=ap.parse_args(argv)
    if not re.fullmatch(r'[0-9]+',a.run_id) or not re.fullmatch(r'[0-9a-f]{40}',a.head) \
            or a.event not in ('schedule','workflow_dispatch'):
        print('❌ invalid monitor identity',file=sys.stderr); return 2
    for p in a.report: _one(p,a.run_id,a.head,a.event)
    return 0
if __name__=='__main__': sys.exit(main())
