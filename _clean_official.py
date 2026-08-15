import json, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
d = json.load(open(r'd:\香港窗口式空調查找\official_specs.json', encoding='utf-8'))
# 用原始 snippet 重新判斷——但已經洗咗。改用記錄嘅 url 重抓? 太慢。
# 直接修已知錯誤: CWF 係 闊x深x高 → 高×闊×深 = [3,1,2]
def fix_size(v):
    s = v.get('size') or ''
    m = re.search(r'(\d+(?:\.\d+)?)\u00d7(\d+(?:\.\d+)?)\u00d7(\d+(?:\.\d+)?)', s)
    if not m: return v
    a,b,c = m.groups()
    k = v.get('model','')
    if k.startswith('CWF'):
        v['size'] = f'{c}\u00d7{a}\u00d7{b}'
    elif k.startswith(('CAF','CF-','CFS','CAFB','CAFC')):
        # 分體: 闊x深x高 → 高×闊×深
        v['size'] = f'{c}\u00d7{a}\u00d7{b}'
    return v
for k in d:
    if k.startswith('CWF') or k.startswith('CAF') or k.startswith('CF') or k.startswith('CFS'):
        fix_size(d[k])
        print(k, '->', d[k].get('size'))
json.dump(d, open(r'd:\香港窗口式空調查找\official_specs.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
