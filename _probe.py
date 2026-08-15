import re, ssl, urllib.request, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
def get(u):
    req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
    return urllib.request.urlopen(req, timeout=15, context=CTX).read().decode('utf-8','ignore')
for slug in ['cfw-07ff-m', 'cfw-12ff-m']:
    html = get('https://www.feelcomfee.com/hk/products/air-conditioner/' + slug)
    txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt)
    i = txt.find('尺寸')
    print(slug, ':', repr(txt[i:i+70]))
