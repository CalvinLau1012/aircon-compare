# -*- coding: utf-8 -*-
"""生成 Gemini 人手查價用嘅 CSV（含要求註釋 + 全量型號）

用法：
  python make_gemini_input.py          # 核心 29 型號
  python make_gemini_input.py --all    # 全量 EMSD 型號（約 1,900 個，分次貼比較穩）
"""
import csv
import sys

import generate_html

OUT_ALL = r'd:\香港窗口式空調查找\gemini_input_all.csv'
OUT_CORE = r'd:\香港窗口式空調查找\gemini_input.csv'

RULES = [
    '# 要求（必須遵守）：',
    '# 1. price_low / price_high 只填整數港元，唔好有 $ 或逗號（例：2500）',
    '# 2. 只搜「冷氣機」本體價格；遙控器、濾網、安裝費一律忽略',
    '# 3. 得一個價錢時 price_low = price_high',
    '# 4. 搜唔到可靠資料就留空——嚴禁估計、嚴禁填舊資料當新價',
    '# 5. source 填實際參考網站名（BigGo / Price.com.hk / 豐澤 / HKTVmall 等），搜唔到留空',
    '# 6. 輸出 CSV：brand,model,price_low,price_high,source（唔好省略任何一行）',
]


def main():
    all_mode = '--all' in sys.argv
    if all_mode:
        rows = list(csv.reader(open(r'd:\香港窗口式空調查找\emsd_空調能源標籤.csv', encoding='utf-8-sig')))[1:]
        rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']
        seen = set()
        items = []
        for r in rows:
            m = r[1].strip()
            if not m or m in seen:
                continue
            seen.add(m)
            items.append((generate_html.normalize_brand(r[0]), m))
        out_path = OUT_ALL
    else:
        items = [(m['brand'], m['model']) for m in generate_html.MODELS]
        out_path = OUT_CORE

    with open(out_path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        for line in RULES:
            w.writerow([line])
        w.writerow(['brand', 'model'])
        for b, m in items:
            w.writerow([b, m])
    print(f'已生成 {out_path}：{len(items)} 個型號')
    if all_mode:
        print('提示：Gemini 一次過貼 1,900 行可能回唔晒，建議每次貼 200-300 行（分批）')


if __name__ == '__main__':
    main()
