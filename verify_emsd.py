#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
對比 EMSD 官方能源標籤數據（用型號關鍵詞搜尋，唔理品牌名稱格式）
"""
import csv
import re

CSV = r'd:\香港窗口式空調查找\emsd_空調能源標籤.csv'

# 型號關鍵詞 → (品牌, 報告型號)
KEYWORDS = [
    # 定頻
    ('TA-09EOG', 'CANOPUS 肯特 TA-09EOG'), ('TA-12EOG', 'CANOPUS 肯特 TA-12EOG'), ('TA-18EOG', 'CANOPUS 肯特 TA-18EOG'),
    ('W09R5A', 'TOSOT 大松 W09R5A'), ('W12R5A', 'TOSOT 大松 W12R5A'), ('W18R5A', 'TOSOT 大松 W18R5A'), ('W24R5A', 'TOSOT 大松 W24R5A'),
    ('CHK09BE', 'Carrier 開利 CHK09BE'), ('CHK12BE', 'Carrier 開利 CHK12BE'), ('CHK18BE', 'Carrier 開利 CHK18BE'),
    ('MW-09CR8C', 'Midea 美的 MW-09CR8C'), ('MW-12CR8C', 'Midea 美的 MW-12CR8C'),
    ('RA-10RF', 'HITACHI 日立 RA-10RF'), ('RC-XG9', 'Rasonic 樂信 RC-XG9'), ('RC-XG12', 'Rasonic 樂信 RC-XG12'),
    ('RFR18FNTN', 'FUJI 富士 RFR18FNTN'),
    # 變頻
    ('CWF-09CRFN8-AD5', 'COMFEE CWF-09CRFN8-AD5'), ('CWF-12CRFN8-AD5', 'COMFEE CWF-12CRFN8-AD5'),
    ('CWF-18CRFN8-AD5', 'COMFEE CWF-18CRFN8-AD5'),
    ('CHK09EAVXP', 'Carrier CHK09EAVXP'), ('CHK12EAVXP', 'Carrier CHK12EAVXP'), ('CHK18EAVXP', 'Carrier CHK18EAVXP'),
    ('MW-09CRF8B', 'Midea MW-09CRF8B'),
    ('GWF09P', 'Gree 格力 GWF09P'), ('GWF12DB', 'Gree 格力 GWF12DB'),
    ('AMWB12NID', 'General 珍寶 AMWB12NID'), ('CW-HU90AA', 'Panasonic CW-HU90AA'), ('CW-HU120AA', 'Panasonic CW-HU120AA'),
    ('RC-TS18UV', 'Rasonic 樂信 RC-TS18UV'),
]

# 遺漏型號
EXTRA = [
    ('CW-HU180AA', 'Panasonic CW-HU180AA'), ('RC-HU90A', 'Rasonic RC-HU90A'), ('RC-HU120A', 'Rasonic RC-HU120A'),
    ('RC-HU180A', 'Rasonic RC-HU180A'), ('W3-Q0918D0', 'LG W3-Q0918D0'), ('W3-Q1218D0', 'LG W3-Q1218D0'),
    ('GWF18DB', 'Gree GWF18DB'), ('AWB090CR', 'Whirlpool AWB090CR'), ('AWB120CR', 'Whirlpool AWB120CR'),
    ('CW-N921JA', 'Panasonic CW-N921JA'), ('CW-N1221VA', 'Panasonic CW-N1221VA'),
]


def norm(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def main():
    with open(CSV, encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))[1:]
    rows = [r for r in rows if len(r) >= 15]
    # 過濾表頭重複行
    rows = [r for r in rows if r[1] != '型號']

    def search(kw):
        nk = norm(kw)
        return [r for r in rows if nk in norm(r[1])]

    def fmt(r):
        return (f"品牌={r[0]} | 型號={r[1]} | 級別={r[4]} | 年耗電={r[5]}kWh | 製冷量={r[6]}kW | "
                f"CSPF={r[7]} | 雪種={r[8]} | 變頻={r[14]} | 年份={r[3]} | 編號={r[2]}")

    print('### 報告 29 型號 vs EMSD 官方 ###\n')
    n = 0
    for kw, label in KEYWORDS:
        res = search(kw)
        if res:
            n += 1
            print(f'✅ {label}')
            for r in res:
                print(f'   {fmt(r)}')
        else:
            print(f'❌ {label} — EMSD 搵唔到')
    print(f'\n搵到 {n}/{len(KEYWORDS)}')

    print('\n### 遺漏/待查型號 ###\n')
    for kw, label in EXTRA:
        res = search(kw)
        if res:
            print(f'✅ {label}')
            for r in res:
                print(f'   {fmt(r)}')
        else:
            print(f'❌ {label} — EMSD 搵唔到')


if __name__ == '__main__':
    main()
