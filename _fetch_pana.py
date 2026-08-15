import json, re, ssl, sys, io, urllib.request, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36','Accept-Language':'zh-HK,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
urls = json.load(open(r'd:\香港窗口式空調查找\pana_urls.json', encoding='utf-8'))
out = {}
for url in urls:
    try:
        html = get(url)
        name = price = ''
        for ld in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            try:
                j = json.loads(ld)
                if j.get('@type') == 'Product':
                    name = j.get('name', '')
                    off = j.get('offers') or {}
                    if isinstance(off, list): off = off[0] if off else {}
                    price = str(off.get('price', '') or off.get('lowPrice', ''))
            except Exception: pass
        m = re.search(r'\b(CW-[A-Z0-9]+)', name.upper())
        model = m.group(1) if m else 'UNK'
        out[model] = {'name': name.strip(), 'price': 'HK$'+price if price else '', 'url': url}
        print(model, price, name[:55])
    except Exception as e:
        print('ERR', str(e)[:40])
    time.sleep(0.2)
json.dump(out, open(r'd:\香港窗口式空調查找\pana_official.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('done', len(out))
