import re, ssl, urllib.request, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/148.0 Safari/537.36','Accept-Language':'zh-Hant,zh;q=0.9'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
h = get('https://www.general-aircon.com/zh-hant/product/amwb12nid-1-5-hp-r32-refrigerant-inverter-window-cooling-type-wireless-r-c')
t = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', h, flags=re.S)
t = re.sub(r'<[^>]+>', '\n', t)
lines = [l.strip() for l in t.split('\n') if l.strip()]
print('\n'.join(lines[60:]))
