import json
urls = [
"https://www.rasonicshop.hk/products/rasonic-rc-sul120b-inverter-plus-inverter-window-type-cooling-only-air-conditionerleft-flow-with-remote-control15hp",
"https://www.rasonicshop.hk/products/rasonic-rc-sul180b-inverter-lite-inverter-window-type-cooling-only-left-flow-air-conditioner-2-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-sul240b-inverter-lite-inverter-window-type-cooling-only-left-flow-air-conditioner-25-hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-su70a-inverter-plus-window-type-air-conditionercooling-only-remote-control-type34hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-su90a-inverter-plus-window-type-air-conditionercooling-only-remote-control-type10hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-su120a-inverter-plus-window-type-air-conditionercooling-only-remote-control-type15hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-su180a-inverter-plus-window-type-air-conditionercooling-only-remote-control-type20hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-su240a-inverter-plus-window-type-air-conditionercooling-only-remote-control-type25hp",
"https://www.rasonicshop.hk/products/rasonic-rc-h7hr-r32-inverter-heat-pump-remote-type-window-air-conditioner-%E2%80%93-with-dry-mode-and-wireless-remote-control-34-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-h9hr-r32-inverter-heat-pump-remote-type-window-air-conditioner-%E2%80%93-with-dry-mode-and-wireless-remote-control-34-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-h12hr-r32-inverter-heat-pump-remote-type-window-air-conditioner-%E2%80%93-with-dry-mode-and-wireless-remote-control-34-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-h18hr-r32-inverter-heat-pump-remote-type-window-air-conditioner-%E2%80%93-with-dry-mode-and-wireless-remote-control-34-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-h24hr-r32-inverter-heat-pump-remote-type-window-air-conditioner-%E2%80%93-with-dry-mode-and-wireless-remote-control-34-hp",
"https://www.rasonicshop.hk/products/rasonic-rc-ts7uv-r32-wi-fi-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control",
"https://www.rasonicshop.hk/products/rasonic-rc-ts9uv-r32-wi-fi-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control",
"https://www.rasonicshop.hk/products/rasonic-rc-ts12uv-r32-wi-fi-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control",
"https://www.rasonicshop.hk/products/rasonic-rc-ts18uv-r32-wi-fi-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control",
"https://www.rasonicshop.hk/products/rasonic-rc-ts24uv-r32-wi-fi-inverter-cooling-window-air-conditioner-with-dry-mode-and-wireless-remote-control",
"https://www.rasonicshop.hk/products/rasonic-rc-xg18-window-type-air-conditionercooling-only-remote-control-type-20hp",
"https://www.rasonicshop.hk/products/rasonic-rc-xg12-window-type-air-conditionercooling-only-remote-control-type-15hp",
"https://www.rasonicshop.hk/products/rasonic-rc-xg9-window-type-air-conditionercooling-only-remote-control-type-10hp",
"https://www.rasonicshop.hk/products/rasonic-rc-xg7-window-type-air-conditionercooling-only-remote-control-type-34hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-hz70a-inverter-ultra-inverter-window-type-heat-pump-air-conditionerwith-remote-control34hp",
"https://www.rasonicshop.hk/products/%E3%80%90pre-order%E3%80%91rasonic-rc-hz90a-inverter-ultra-inverter-window-type-heat-pump-air-conditionerwith-remote-control1hp",
]
json.dump(urls, open(r'd:\香港窗口式空調查找\rasonic_urls.json','w',encoding='utf-8'), ensure_ascii=False, indent=1)
print('saved', len(urls))
