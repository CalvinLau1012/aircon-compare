import json
ids = [214,215,216,217,218,232,233,234,235,227,228,229,230,223,224,225,226,197,199,200,201,236,237,238,239,265,266,267,268,146,213,170,149,171,151,152,153,154,156,157,158,159,176,177,178,179,172,173,174,175,292,291,290,293,294,41,42,44,45,37,38,39,40,43,50,51,52,138,53,139,140,141,46,47,48,49,54,55,83]
urls = [f'https://www.century-carrier.com/product/info/id/{i}/b_id/1/m_id/1.html' for i in ids]
json.dump(urls, open(r'd:\香港窗口式空調查找\carrier_urls.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('saved', len(urls))
