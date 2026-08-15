import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
d = json.load(open(r'd:\香港窗口式空調查找\midea_official.json', encoding='utf-8'))
# 闊x高x深 → H×W×D
specs = {
 'MW-07CRF8E': ('451x350x675', '31.9kg'),
 'MW-09CRF8E': ('451x350x675', '31.9kg'),
 'MW-12CRF8E': ('451x350x675', '33.3kg'),
 'MW-18CRF8E': ('660x428x780', '52.3kg'),
 'MW-22CRF8E': ('660x428x780', '53.0kg'),
 'MW-07CM8C': ('451x350x675', '31.3kg'),
 'MW-09CM8C': ('451x350x675', '34.7kg'),
 'MW12CM8C': ('600x380x560', '37.5kg'),
 'MW-18CM8C': ('660x428x680', '53.1kg'),
 'MW-07HRF8F': ('560x375x695', '38.9kg'),
 'MW-09HRF8F': ('560x375x695', '38.9kg'),
 'MW-12HRF8F': ('560x375x695', '38.9kg'),
}
def conv(s):
    w, h, dd = s.split('x')
    return f'{h}\u00d7{w}\u00d7{dd}'
for k, (sz, wt) in specs.items():
    d[k]['size'] = conv(sz)
    d[k]['weight'] = wt
    print(k, d[k]['size'], d[k]['weight'])
json.dump(d, open(r'd:\香港窗口式空調查找\midea_official.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
