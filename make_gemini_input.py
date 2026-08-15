# -*- coding: utf-8 -*-
"""生成 Gemini 網頁版嘅輸入 CSV（核心 29 型號先試水）"""
import csv
import generate_html

with open(r'd:\香港窗口式空調查找\gemini_input.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f)
    w.writerow(['brand', 'model'])
    for m in generate_html.MODELS:
        w.writerow([m['brand'], m['model']])
print('已生成 gemini_input.csv：', len(generate_html.MODELS), '個型號')
