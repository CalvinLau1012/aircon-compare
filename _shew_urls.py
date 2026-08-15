import json
base = 'https://www.shew.com.hk/chinese/brand/rasonic/ventilation-and-air-conditioning/air-conditioner/window-air-conditioner/'
slugs = ['rc-sul120b','rc-sul180b','rc-sul240b','rc-hz70a','rc-hz90a','rc-hz120a','rc-hz180a','rc-hz240a',
'rc-hu70a','rc-hu90a','rc-hu120a','rc-hu180a','rc-hu240a','rc-su70a','rc-su90a','rc-su120a','rc-su180a','rc-su240a',
'rc-n721j','rc-n921j','rc-n1221v','rc-n1821e','rc-n2421e','rc-h7hr','rc-h9hr','rc-h12hr','rc-h18hr','rc-h24hr',
'rc-ts7uv','rc-ts9uv','rc-ts12uv','rc-ts18uv','rc-ts24uv','rc-s7hr','rc-s9hr','rc-s12hr','rc-s18hr','rc-s24hr',
'rc-s701ac','rc-s901ac','rc-s1201ac','rc-s1801ac','rc-xg7','rc-xg9','rc-xg12','rc-xg18','rc-v7ac','rc-v9ac','rc-v12ac','rc-v18ac']
json.dump([base + s + '.aspx' for s in slugs], open(r'd:\香港窗口式空調查找\shew_urls.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('saved', len(slugs))
