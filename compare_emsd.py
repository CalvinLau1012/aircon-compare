#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""輸出報告 vs EMSD 官方對比總表"""
import csv, os, re

BASE = os.path.dirname(os.path.abspath(__file__))
rows = list(csv.reader(open(os.path.join(BASE, 'emsd_空調能源標籤.csv'), encoding='utf-8-sig')))[1:]
rows = [r for r in rows if len(r) >= 15 and r[1] != '型號']


def norm(s):
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def find(kw):
    nk = norm(kw)
    for r in rows:
        if nk in norm(r[1]):
            return r
    return None


report = [
    ('CANOPUS TA-09EOG', '4級', 'R32'), ('CANOPUS TA-12EOG', '4級', 'R32'), ('CANOPUS TA-18EOG', '4級', 'R32'),
    ('TOSOT W09R5A', '3級', 'R32'), ('TOSOT W12R5A', '4級', 'R32'), ('TOSOT W18R5A', '4級', 'R32'), ('TOSOT W24R5A', '4級', 'R32'),
    ('Carrier CHK09BE', '3級', 'R32'), ('Carrier CHK12BE', '3級', 'R32'), ('Carrier CHK18BE', '3級', 'R32'),
    ('Midea MW-09CR8C', '3級', 'R32'), ('Midea MW-12CR8C', '3級', 'R32'),
    ('HITACHI RA-10RF', '3級', 'R410A'), ('Rasonic RC-XG9', '4級', 'R32'), ('Rasonic RC-XG12', '4級', 'R32'), ('FUJI RFR18FNTN', '4級', 'R32'),
    ('COMFEE CWF-09CRFN8-AD5', '1級', 'R32'), ('COMFEE CWF-12CRFN8-AD5', '1級', 'R32'), ('COMFEE CWF-18CRFN8-AD5', '1級', 'R32'),
    ('Carrier CHK09EAVXP', '1級', 'R32'), ('Carrier CHK12EAVXP', '1級', 'R32'), ('Carrier CHK18EAVXP', '1級', 'R32'),
    ('Midea MW-09CRF8B', '1級', 'R32'), ('Gree GWF09P', '1級', 'R32'), ('Gree GWF12DB', '1級', 'R32'),
    ('General AMWB12NID', '1級', 'R32'), ('Panasonic CW-HU90AA', '1級', 'R32'), ('Panasonic CW-HU120AA', '1級', 'R32'), ('Rasonic RC-TS18UV', '1級', 'R32'),
]

hdr = f"{'型號':30}{'報告級':>6}{'EMSD級':>7}{'報告雪種':>8}{'EMSD雪種':>9}{'EMSD年耗電':>10}  標記"
print(hdr)
print('-' * len(hdr))
for m, rep_g, rep_r in report:
    r = find(m.split(' ')[-1])
    if r:
        em_g, em_r = r[4], r[8]
        g_ok = 'OK' if rep_g == em_g + '級' else '!!'
        r_ok = 'OK' if rep_r.upper() == em_r.upper() else '!!'
        print(f"{m:30}{rep_g:>6}{em_g+'級':>7}{rep_r:>8}{em_r:>9}{r[5]:>10}  {g_ok}/{r_ok}")
    else:
        print(f"{m:30}{rep_g:>6}{'??':>7}{rep_r:>8}{'??':>9}{'?':>10}  ??/??")
