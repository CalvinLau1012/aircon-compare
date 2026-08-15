import re, ssl, urllib.request, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36','Accept-Language':'zh-HK,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
u = 'https://www.shew.com.hk/chinese/brand/rasonic/ventilation-and-air-conditioning/air-conditioner/window-air-conditioner/rc-ts18uv.aspx'
try:
    html = get(u)
    print('OK', len(html))
    txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt)
    for kw in ['尺寸','體積','淨重','重量','保用','保養','雪種','毫米']:
        i = txt.find(kw)
        if i >= 0: print(kw, '->', txt[i:i+80])
except Exception as e:
    print('ERR', str(e)[:80])
