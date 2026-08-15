import json, re, ssl, sys, urllib.request
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
out = json.load(open(r'd:\香港窗口式空調查找\official_specs.json', encoding='utf-8'))
pat = re.compile(r'(\d+)\s*\(闊\)\s*x\s*(\d+)\s*\(高\)\s*x\s*(\d+)\s*\(深\)')
for k, v in list(out.items()):
    url = v.get('url') or ''
    if 'hitachi' not in url or 'html' not in url:
        continue
    try:
        html = get(url)
        m = pat.search(html)
        if m:
            w, h, d = m.groups()
            out[k]['size'] = f'{h}x{w}x{d}'
            print(k, '->', out[k]['size'])
        else:
            print(k, '-> NO MATCH')
    except Exception as e:
        print(k, 'ERR', str(e)[:40])
json.dump(out, open(r'd:\香港窗口式空調查找\official_specs.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
