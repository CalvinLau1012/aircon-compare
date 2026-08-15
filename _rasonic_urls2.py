import json
base = json.load(open(r'd:\香港窗口式空調查找\rasonic_urls.json', encoding='utf-8'))
extra = [
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-hz120a-inverter-ultra-inverter-window-type-heat-pump-air-conditionerwith-remote-control15hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-hz180a-inverter-ultra-inverter-window-type-heat-pump-air-conditionerwith-remote-control20hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-hz240a-inverter-ultra-inverter-window-type-heat-pump-air-conditionerwith-remote-control25hp",
"https://www.rasonicshop.hk/products/rasonic-%E6%A8%82%E4%BF%A1%E7%89%8C-rc-hu70a-inverter-ultra-%E8%AE%8A%E9%A0%BB%E6%B7%A8%E5%86%B7%E7%AA%97%E5%8F%A3%E6%A9%9F%E7%84%A1%E7%B7%9A%E9%81%99%E6%8E%A7%E5%9E%8B34%E5%8C%B9",
"https://www.rasonicshop.hk/products/rasonic-rc-hu90a-inverter-window-type-cooling-only-air-conditionerwith-remote-control1hp",
"https://www.rasonicshop.hk/products/rasonic-rc-hu120a-inverter-ultra-inverter-window-type-cooling-only-air-conditionerwith-remote-control15hp",
"https://www.rasonicshop.hk/products/rasonic-rc-hu180a-inverter-ultra-inverter-window-type-cooling-only-air-conditionerwith-remote-control20hp",
"https://www.rasonicshop.hk/products/rasonic-rc-hu240a-inverter-ultra-inverter-window-type-cooling-only-air-conditionerwith-remote-control25hp",
"https://www.rasonicshop.hk/products/rasonic-rc-n721j",
"https://www.rasonicshop.hk/products/rasonic-rc-n921j",
"https://www.rasonicshop.hk/products/rasonic-rc-n1221v",
"https://www.rasonicshop.hk/products/rasonic-rc-n1821e",
"https://www.rasonicshop.hk/products/rc-n2421e",
"https://www.rasonicshop.hk/products/frostar-fr-ks7-r32-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control-34hp",
"https://www.rasonicshop.hk/products/frostar-fr-ks9-r32-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control-34hp",
"https://www.rasonicshop.hk/products/frostar-fr-ks12-r32-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control-34hp",
"https://www.rasonicshop.hk/products/frostar-fr-ks18-r32-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control-34hp",
]
all_urls = base + extra
json.dump(all_urls, open(r'd:\香港窗口式空調查找\rasonic_urls.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('total', len(all_urls))
