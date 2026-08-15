import json, re, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
d = json.load(open(r'd:\香港窗口式空調查找\official_specs.json', encoding='utf-8'))
fixed = 0
for k, v in d.items():
    # 重新從原始 snippet 攞唔到（已刪）。用頁面重抓能源級別——搵官方能源級
    # COMFEE 官方能源級已知：CWF-CRFN8 係 1 級、CFW-FF-M 係 3 級
    pass
# 直接用 EMSD 已知能源級（唔再從 official_specs 覆蓋 energy，見 generate_html.py 修復）
print('done')
