import urllib.request, ssl, re
CTX = ssl.create_default_context()
html = urllib.request.urlopen('https://calvinlau1012.github.io/aircon-compare/index.html', timeout=30, context=CTX).read().decode('utf-8','ignore')
print('LEN', len(html))
print('RA-10RF 遙控:', '遙控 · R32' if False else '')
# 檢查 RC-TS18UV price
i = html.find('RC-TS18UV')
m = re.search(r'RC-TS18UV.*?price.{0,20}?(\$[\d,]+(?:-\$?[\d,]+)?)', html[i:i+2000] if i>=0 else '', re.S)
print('TS18 price:', m.group(1) if m else 'N/A')
# 檢查 warranty 垃圾
print('垃圾 warranty:', html.count('保用 5 年壓縮機保用 體積'))
print('清洗後壓縮機:', html.count('壓縮機5年'))
# 檢查 deep merge 後 HU90AA
j = html.find('CW-HU90AA')
m2 = re.search(r'"price":\s*"(\$[\d,]+)"', html[j:j+1500] if j>=0 else '')
print('HU90AA price:', m2.group(1) if m2 else 'N/A')
