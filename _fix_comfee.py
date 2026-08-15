import json, re, ssl, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
d = json.load(open(r'd:\香港窗口式空調查找\official_specs.json', encoding='utf-8'))
for k, v in list(d.items()):
    url = v.get('url') or ''
    if 'feelcomfee' not in url:
        continue
    try:
        html = get(url)
        txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
        txt = re.sub(r'<[^>]+>', ' ', txt)
        txt = re.sub(r'\s+', ' ', txt)
        m = re.search(r'尺寸\s*\(([^)]*)\)\s*(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)', txt)
        if m:
            label, a, b, c = m.group(1), m.group(2), m.group(3), m.group(4)
            hi, wi, di = label.find('高'), label.find('闊'), label.find('深')
            if hi >= 0 and wi >= 0 and hi < wi:        # 高x闊x深 → 直接
                h, w, dd = a, b, c
            elif wi >= 0 and di >= 0 and wi < di:      # 闊x深x高 → 高×闊×深
                h, w, dd = c, a, b
            elif wi >= 0 and hi >= 0 and wi < hi:      # 闊x高x深 → 高×闊×深
                h, w, dd = b, a, c
            else:
                h, w, dd = a, b, c
            d[k]['size'] = f'{h}\u00d7{w}\u00d7{dd}'
            print(k, '->', d[k]['size'])
    except Exception as e:
        print(k, 'ERR', str(e)[:40])
json.dump(d, open(r'd:\香港窗口式空調查找\official_specs.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
