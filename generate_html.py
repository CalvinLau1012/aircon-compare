#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「網頁式互動對比報告」→ 空調對比報告.html
- Self-contained（內嵌 CSS/JS，可轉發 email/WhatsApp）
- 響應式（手機 / 平板 / 桌面）
- 互動比較器（勾選型號 → 對比面板）
"""
import json
import os
import markdown
import re

BASE = r'd:\香港窗口式空調查找'

# ============================================================
# 型號資料庫（整合報告 + EMSD 官方）
# 欄位: brand, model, hp, btu, type, energy, wifi, price, kwh,
#       cspf, kw, gas, noise, size, weight, warranty, note
# ============================================================
MODELS = [
    # ---- 1 匹 定頻 ----
    dict(brand='CANOPUS 肯特', model='TA-09EOG', hp='1匹', btu='~9,000', type='定頻', energy='3級', wifi='➖',
         price='$1,850-2,500', kwh='394', cspf='3.4011', kw='2.73', gas='R32', noise='待查',
         size='346×450×585', weight='~30kg', warranty='3/5年', note='💰 全場最平'),
    dict(brand='TOSOT 大松', model='W09R5A', hp='1匹', btu='~9,000', type='定頻', energy='4級', wifi='➖',
         price='$2,000-2,400', kwh='429', cspf='3.0396', kw='2.68', gas='R32', noise='待查',
         size='350×450×535', weight='~30kg', warranty='3/5年', note='格力副廠 R32'),
    dict(brand='Carrier 開利', model='CHK09BE', hp='1匹', btu='~9,000', type='定頻', energy='3級', wifi='➖',
         price='$2,150-2,600', kwh='394', cspf='3.4011', kw='2.73', gas='R32', noise='45-49dB',
         size='346×450×585', weight='~31kg', warranty='3/5年', note='雙重金鑽防銹'),
    dict(brand='Midea 美的', model='MW-09CR8C', hp='1匹', btu='~9,000', type='定頻', energy='3級', wifi='➖',
         price='$2,100-2,500', kwh='396', cspf='3.3693', kw='2.75', gas='R32', noise='45-49dB',
         size='346×450×585', weight='~30kg', warranty='3/5年', note='銀離子抗菌網'),
    dict(brand='HITACHI 日立', model='RA-10RF', hp='1匹', btu='9,120', type='定頻', energy='3級', wifi='➖',
         price='$2,140-3,380', kwh='422', cspf='3.2045', kw='2.62', gas='R32', noise='44-48dB',
         size='345×470×600', weight='~32kg', warranty='2/5年', note='⭐ 38人認證極靜'),
    dict(brand='Rasonic 樂信', model='RC-XG9', hp='1匹', btu='8,803', type='定頻', energy='4級', wifi='➖',
         price='$2,500-2,900', kwh='435', cspf='2.9978', kw='2.65', gas='R32', noise='待查',
         size='346×450×640', weight='~29kg', warranty='3/5年', note='Panasonic 旗下'),
    # ---- 1.5 匹 定頻 ----
    dict(brand='CANOPUS 肯特', model='TA-12EOG', hp='1.5匹', btu='~12,000', type='定頻', energy='3級', wifi='➖',
         price='$2,300-3,100', kwh='542', cspf='3.2809', kw='3.52', gas='R32', noise='待查',
         size='375×560×668', weight='~40kg', warranty='3/5年', note='⚠️ 無遙控'),
    dict(brand='Midea 美的', model='MW-12CR8C', hp='1.5匹', btu='~12,000', type='定頻', energy='3級', wifi='➖',
         price='$2,700-3,200', kwh='549', cspf='3.2432', kw='3.56', gas='R32', noise='47-51dB',
         size='375×560×668', weight='~39kg', warranty='3/5年', note='金鑽塗層'),
    dict(brand='Carrier 開利', model='CHK12BE', hp='1.5匹', btu='~12,000', type='定頻', energy='3級', wifi='➖',
         price='$2,800-3,300', kwh='542', cspf='3.2809', kw='3.52', gas='R32', noise='47-51dB',
         size='375×560×668', weight='~40kg', warranty='3/5年', note='獨立抽濕'),
    dict(brand='TOSOT 大松', model='W12R5A', hp='1.5匹', btu='11,601', type='定頻', energy='4級', wifi='➖',
         price='$2,600-3,000', kwh='560', cspf='3.0725', kw='3.55', gas='R32', noise='待查',
         size='375×560×668', weight='~38kg', warranty='3/5年', note='🚨 噪音投訴'),
    dict(brand='Rasonic 樂信', model='RC-XG12', hp='1.5匹', btu='11,601', type='定頻', energy='4級', wifi='➖',
         price='$3,100-3,600', kwh='558', cspf='3.0835', kw='3.57', gas='R32', noise='待查',
         size='375×560×710', weight='~38kg', warranty='3/5年', note='⭐ 3+5年保養'),
    # ---- 2 匹 定頻 ----
    dict(brand='CANOPUS 肯特', model='TA-18EOG', hp='2匹', btu='~18,000', type='定頻', energy='3級', wifi='➖',
         price='$3,500-4,800', kwh='814', cspf='3.2764', kw='5.34', gas='R32', noise='待查',
         size='428×660×770', weight='~58kg', warranty='3/5年', note='💰 最平 2匹'),
    dict(brand='Carrier 開利', model='CHK18BE', hp='2匹', btu='~18,000', type='定頻', energy='3級', wifi='➖',
         price='$3,920-4,900', kwh='814', cspf='3.2764', kw='5.34', gas='R32', noise='50-54dB',
         size='428×660×770', weight='~56kg', warranty='3/5年', note='定頻中 3 級'),
    dict(brand='TOSOT 大松', model='W18R5A', hp='2匹', btu='17,572', type='定頻', energy='4級', wifi='➖',
         price='$4,100-4,600', kwh='861', cspf='3.0237', kw='5.37', gas='R32', noise='待查',
         size='428×660×770', weight='~55kg', warranty='3/5年', note='香港製 R32'),
    dict(brand='FUJI 富士', model='RFR18FNTN', hp='2匹', btu='17,400', type='定頻', energy='4級', wifi='➖',
         price='$4,000-4,600', kwh='850', cspf='3.0323', kw='5.21', gas='R410A', noise='待查',
         size='428×660×770', weight='~56kg', warranty='2/5年', note='日系抗菌防霉'),
    # ---- 2.5 匹 定頻 ----
    dict(brand='TOSOT 大松', model='W24R5A', hp='2.5匹', btu='22,314', type='定頻', energy='4級', wifi='➖',
         price='$4,618-5,800', kwh='1,057', cspf='3.1288', kw='6.63', gas='R32', noise='待查',
         size='428×660×770', weight='~65kg', warranty='3/5年', note='唯一 2.5 匹'),
    # ---- 1 匹 變頻 ----
    dict(brand='COMFEE', model='CWF-09CRFN8-AD5', hp='1匹', btu='~9,000', type='變頻', energy='1級', wifi='✅',
         price='$3,100-3,890', kwh='290', cspf='4.5994', kw='2.57', gas='R32', noise='42-46dB',
         size='346×450×585', weight='~28kg', warranty='3/5年', note='🔌 最平 WiFi 智能機'),
    dict(brand='Carrier 開利', model='CHK09EAVXP', hp='1匹', btu='~9,000', type='變頻', energy='1級', wifi='✅',
         price='$2,800-4,200', kwh='272', cspf='4.7797', kw='2.63', gas='R32', noise='41-45dB',
         size='346×450×585', weight='~29kg', warranty='3/5年', note='⭐ 性價比變頻'),
    dict(brand='Midea 美的', model='MW-09CRF8B', hp='1匹', btu='~9,000', type='變頻', energy='1級', wifi='✅',
         price='$2,700-4,300', kwh='290', cspf='4.5994', kw='2.57', gas='R32', noise='41-45dB',
         size='346×450×585', weight='~29kg', warranty='3/5年', note='UV Pro 殺菌'),
    dict(brand='Gree 格力', model='GWF09P', hp='1匹', btu='~9,000', type='變頻', energy='1級', wifi='✅',
         price='$2,800-4,499', kwh='280', cspf='4.5066', kw='2.50', gas='R32', noise='待查',
         size='350×450×535', weight='~28kg', warranty='3/5年', note='消委會高評分'),
    dict(brand='Panasonic 樂聲', model='CW-HU90AA', hp='1匹', btu='~9,000', type='變頻', energy='1級', wifi='✅',
         price='$4,570-6,449', kwh='269', cspf='5.0130', kw='2.62', gas='R32', noise='39-44dB',
         size='346×450×640', weight='~30kg', warranty='3/5年', note='🏆 旗艦 nanoe X'),
    # ---- 1.5 匹 變頻 ----
    dict(brand='COMFEE', model='CWF-12CRFN8-AD5', hp='1.5匹', btu='~12,000', type='變頻', energy='1級', wifi='✅',
         price='$2,900-4,990', kwh='380', cspf='4.6849', kw='3.50', gas='R32', noise='44-48dB',
         size='375×560×668', weight='~35kg', warranty='3/5年', note='🔌 最平 1.5匹 WiFi'),
    dict(brand='Carrier 開利', model='CHK12EAVXP', hp='1.5匹', btu='~12,000', type='變頻', energy='1級', wifi='✅',
         price='$3,500-5,300', kwh='380', cspf='4.6849', kw='3.50', gas='R32', noise='43-47dB',
         size='375×560×668', weight='~36kg', warranty='3/5年', note='⭐ 約樂聲 6 折'),
    dict(brand='Gree 格力', model='GWF12DB', hp='1.5匹', btu='~12,000', type='變頻', energy='1級', wifi='✅',
         price='$3,600-5,600', kwh='365', cspf='4.8539', kw='3.62', gas='R32', noise='待查',
         size='375×560×668', weight='~36kg', warranty='3/5年', note='G-Diamond 抗腐蝕'),
    dict(brand='General 珍寶', model='AMWB12NID', hp='1.5匹', btu='~12,000', type='變頻', energy='1級', wifi='➖',
         price='$4,500-6,100', kwh='359', cspf='4.9263', kw='3.56', gas='R32', noise='43-48dB',
         size='375×560×705', weight='~38kg', warranty='2/5年', note='UV-C 殺菌；⚠️ 無 WiFi'),
    dict(brand='Panasonic 樂聲', model='CW-HU120AA', hp='1.5匹', btu='~12,000', type='變頻', energy='1級', wifi='✅',
         price='$5,200-7,900', kwh='362', cspf='5.0219', kw='3.54', gas='R32', noise='41-45dB',
         size='375×560×710', weight='~39kg', warranty='3/5年', note='🏆 大房機王'),
    # ---- 2 匹 變頻 ----
    dict(brand='COMFEE', model='CWF-18CRFN8-AD5', hp='2匹', btu='~18,000', type='變頻', energy='1級', wifi='✅',
         price='$4,500-6,200', kwh='578', cspf='4.6392', kw='5.35', gas='R32', noise='46-50dB',
         size='428×660×770', weight='~50kg', warranty='3/5年', note='🔌 最平 2匹 WiFi'),
    dict(brand='Carrier 開利', model='CHK18EAVX', hp='2匹', btu='~18,000', type='變頻', energy='1級', wifi='✅',
         price='$5,200-7,200', kwh='546', cspf='4.7763', kw='5.28', gas='R32', noise='45-49dB',
         size='428×660×770', weight='~52kg', warranty='3/5年', note='⭐ 解決 2匹電費痛點'),
    dict(brand='Rasonic 樂信', model='RC-TS18UV', hp='2匹', btu='~18,000', type='變頻', energy='1級', wifi='✅',
         price='$5,800-7,500', kwh='548', cspf='4.7795', kw='5.36', gas='R32', noise='待查',
         size='428×660×770', weight='~52kg', warranty='3/5年', note='R32 + WiFi + 抽濕'),
]

COMPARE_FIELDS = [
    ('hp', '匹數'), ('btu', '製冷量 (BTU)'), ('type', '類型'), ('energy', '能源級別'),
    ('wifi', 'WiFi'), ('price', '參考價'), ('kwh', '年耗電 (kWh)'), ('cspf', 'CSPF'),
    ('kw', '製冷量 (kW)'), ('gas', '雪種'), ('noise', '噪音'), ('size', '尺寸 (mm)'),
    ('weight', '重量'), ('warranty', '保養 (全機/壓縮機)'), ('note', '特點'),
]


def md_to_html(md_text):
    html = markdown.markdown(md_text, extensions=['tables', 'fenced_code', 'sane_lists'])
    return html


def build_html():
    with open(os.path.join(BASE, '空調對比報告.md'), encoding='utf-8') as f:
        md_text = f.read()
    content_html = md_to_html(md_text)

    models_json = json.dumps(MODELS, ensure_ascii=False)
    fields_json = json.dumps(COMPARE_FIELDS, ensure_ascii=False)

    html = HTML_TEMPLATE.replace('__CONTENT__', content_html) \
                        .replace('__MODELS_JSON__', models_json) \
                        .replace('__FIELDS_JSON__', fields_json)
    out = os.path.join(BASE, '空調對比報告.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print('已生成：', out, f'（{os.path.getsize(out)/1024:.0f} KB）')


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>香港窗口式空調對比報告</title>
<meta property="og:title" content="香港窗口式空調對比報告 · 29 型號 EMSD 官方核實">
<meta property="og:description" content="窗口式淨冷型遙控空調全面對比：能源級別、價格、噪音、耗電 15 項屬性，互動比較器自己揀機比較。">
<meta property="og:type" content="website">
<meta name="description" content="香港窗口式淨冷型遙控空調對比報告：29 型號 · EMSD 官方能源標籤核實 · 互動比較器">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>❄️</text></svg>">
<style>
:root{
  --primary:#0F3D5C; --primary2:#1B5E8A; --accent:#C9A227;
  --bg:#F5F8FB; --text:#22303C; --muted:#6E7E8E;
  --line:#D8E1EB; --alt:#EEF4FA; --warn:#B03A2E; --ok:#1E8E5A;
}
*{box-sizing:border-box; margin:0; padding:0;}
html{scroll-padding-top:64px;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif;
  background:var(--bg); color:var(--text); line-height:1.7;}
.wrap{max-width:1080px; margin:0 auto; padding:0 16px;}

/* ===== Hero 封面 ===== */
.hero{background:linear-gradient(135deg,#0F3D5C 0%,#1B5E8A 100%); color:#fff;
  text-align:center; padding:64px 20px 56px; position:relative; overflow:hidden;}
.hero::after{content:""; position:absolute; bottom:0; left:0; right:0; height:5px;
  background:linear-gradient(90deg,transparent,var(--accent),transparent);}
.hero h1{font-size:2.2em; letter-spacing:1px; margin-bottom:8px;}
.hero .sub{color:var(--accent); font-size:1.15em; margin-bottom:28px;}
.stats{display:flex; justify-content:center; gap:40px; flex-wrap:wrap; margin-bottom:26px;}
.stats .n{font-size:2.1em; font-weight:700; color:var(--accent);}
.stats .l{font-size:.85em; opacity:.9;}
.hero .src{font-size:.8em; opacity:.75; line-height:1.8;}
.hero .date{color:var(--accent); font-size:.95em; margin-top:10px;}

/* ===== 頂部導覽 ===== */
.topnav{position:sticky; top:0; z-index:100; background:rgba(15,61,92,.97);
  backdrop-filter:blur(6px); border-bottom:2px solid var(--accent);}
.topnav .wrap{display:flex; align-items:center; gap:4px; overflow-x:auto; padding:0 8px;}
.topnav a{color:#DCE6F0; text-decoration:none; font-size:.86em; padding:14px 12px;
  white-space:nowrap; flex:0 0 auto; transition:color .2s;}
.topnav a:hover{color:var(--accent);}
.topnav .brand{font-weight:700; color:#fff; font-size:.95em; margin-right:6px;}

/* ===== 章節 ===== */
section{margin:40px 0;}
h2.sec{display:flex; align-items:center; gap:10px; font-size:1.35em; color:var(--primary);
  border-left:6px solid var(--accent); padding-left:12px; margin-bottom:16px; scroll-margin-top:70px;}
h2.sec .tag{font-size:.6em; background:var(--primary); color:#fff; padding:2px 10px;
  border-radius:20px; letter-spacing:1px;}
h3{color:var(--primary2); margin:18px 0 8px; font-size:1.1em;}
p{margin:8px 0;}

/* ===== 卡片 ===== */
.card{background:#fff; border:1px solid var(--line); border-radius:12px;
  padding:18px; margin:12px 0; box-shadow:0 1px 4px rgba(15,61,92,.06);}

/* ===== 表格 ===== */
.table-scroll{overflow-x:auto; -webkit-overflow-scrolling:touch; margin:12px 0;
  border:1px solid var(--line); border-radius:10px; background:#fff;}
table{width:100%; border-collapse:collapse; font-size:.9em; min-width:600px;}
th{background:var(--primary); color:#fff; padding:9px 10px; text-align:center;
  font-weight:600; border-bottom:3px solid var(--accent); white-space:nowrap;}
td{padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top;}
tbody tr:nth-child(even){background:var(--alt);}
tbody tr:hover{background:#E8F1F9;}
blockquote{margin:12px 0; padding:10px 16px; border-left:5px solid var(--accent);
  background:#FFF7E3; border-radius:0 8px 8px 0; font-size:.92em;}
blockquote p{margin:2px 0;}
code{background:#E8F1F9; padding:2px 6px; border-radius:4px; font-size:.9em;}
pre{background:#fff; border:1px solid var(--line); border-radius:8px; padding:12px;
  overflow-x:auto; margin:12px 0;}
ul,ol{margin:8px 0 8px 24px;}

/* ===== 比較器 ===== */
.compare{position:sticky; top:58px; z-index:90; background:#fff;
  border:1px solid var(--line); border-bottom:3px solid var(--accent);
  border-radius:0 0 12px 12px; box-shadow:0 4px 12px rgba(15,61,92,.12);}
.compare .head{display:flex; align-items:center; justify-content:space-between;
  padding:10px 16px; background:var(--primary); color:#fff; border-radius:0 0 0 0;}
.compare .head b{font-size:1em;}
.compare .head .sel{font-size:.8em; color:var(--accent);}
.compare-tools{display:flex; gap:8px; flex-wrap:wrap; padding:8px 12px;
  border-bottom:1px solid var(--line);}
.compare-tools button{background:var(--primary2); color:#fff; border:none;
  padding:6px 14px; border-radius:20px; font-size:.85em; cursor:pointer;}
.compare-tools button:hover{background:var(--accent); color:var(--primary);}
.compare-tools .filters{display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-left:auto;}
.compare-tools select,.compare-tools input[type=search]{border:1px solid var(--line);
  border-radius:20px; padding:5px 12px; font-size:.85em; background:var(--bg);}
.model-list{display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
  gap:8px; padding:10px 12px; max-height:340px; overflow-y:auto;}
.mitem{display:flex; align-items:center; gap:10px; padding:9px 12px;
  border:1px solid var(--line); border-radius:8px; background:#fff; cursor:pointer;
  transition:border-color .15s, box-shadow .15s;}
.mitem:hover{border-color:var(--primary2); box-shadow:0 2px 8px rgba(15,61,92,.12);}
.mitem.checked{border-color:var(--accent); background:#FFFDF2; box-shadow:0 0 0 2px rgba(201,162,39,.35);}
.mitem input{width:18px; height:18px; accent-color:var(--accent); cursor:pointer; flex:0 0 auto;}
.mitem .info{flex:1; min-width:0;}
.mitem .info .name{font-weight:600; font-size:.92em; color:var(--primary);}
.mitem .info .tag{font-size:.75em; color:var(--muted);}
.mitem .badge{font-size:.72em; background:var(--primary); color:#fff; padding:2px 8px;
  border-radius:12px; white-space:nowrap; flex:0 0 auto;}

/* 比較面板 */
.panel{display:none; position:fixed; bottom:0; left:0; right:0; z-index:200;
  background:#fff; border-top:3px solid var(--accent);
  box-shadow:0 -6px 20px rgba(15,61,92,.25); max-height:62vh; display:none; flex-direction:column;}
.panel.open{display:flex;}
.panel .phead{display:flex; align-items:center; justify-content:space-between;
  padding:10px 16px; background:var(--primary); color:#fff;}
.panel .phead button{background:var(--accent); color:var(--primary); border:none;
  padding:4px 12px; border-radius:16px; cursor:pointer; font-size:.85em;}
.panel .pscroll{overflow:auto; padding:12px 16px;}
.panel table{min-width:420px;}
.panel td:first-child{font-weight:600; color:var(--primary2); background:var(--alt);
  white-space:nowrap; width:120px;}
.panel .phint{text-align:center; color:var(--muted); padding:14px; font-size:.9em;}

/* ===== 返回頂部 ===== */
#backTop{position:fixed; right:16px; bottom:20px; width:44px; height:44px;
  background:var(--primary); color:#fff; border:2px solid var(--accent);
  border-radius:50%; font-size:1.2em; cursor:pointer; z-index:150;
  display:none; align-items:center; justify-content:center;
  box-shadow:0 3px 10px rgba(15,61,92,.35);}
#backTop.show{display:flex;}
#backTop:hover{background:var(--accent); color:var(--primary);}

/* ===== 頁腳 ===== */
footer{background:var(--primary); color:#BFD0DE; text-align:center;
  padding:26px 16px; margin-top:50px; font-size:.85em;}
footer b{color:#fff;}
footer .line{color:var(--accent);}

/* ===== 響應式 ===== */
@media (max-width:720px){
  .hero h1{font-size:1.6em;}
  .hero .sub{font-size:1em;}
  .stats{gap:20px;}
  .stats .n{font-size:1.6em;}
  section{margin:28px 0;}
  h2.sec{font-size:1.15em;}
  .model-list{grid-template-columns:1fr;}
  .compare-tools .filters{margin-left:0; width:100%;}
  .panel table{font-size:.8em;}
  .topnav a{padding:12px 10px; font-size:.8em;}
}
@media print{
  .topnav,.compare,.panel{display:none !important;}
  body{background:#fff;}
  .card{border:none; box-shadow:none; padding:0;}
  .hero{background:var(--primary); padding:30px;}
  a{color:inherit; text-decoration:none;}
}
</style>
</head>
<body>

<!-- ===== 封面 ===== -->
<header class="hero">
  <div class="wrap">
    <h1>香港窗口式淨冷型遙控空調</h1>
    <div class="sub">統合對比報告 · 29 型號全面剖析</div>
    <div class="stats">
      <div><div class="n">29</div><div class="l">型號收錄</div></div>
      <div><div class="n">16</div><div class="l">定頻機型</div></div>
      <div><div class="n">13</div><div class="l">變頻機型</div></div>
      <div><div class="n">1,927</div><div class="l">EMSD 官方核實</div></div>
    </div>
    <div class="src">資料來源：機電署 EMSD 能源標籤資料庫（1,927 型號全量核實）· Price.com.hk · 豐澤 · 電器幫 · 百老匯 · Gemini 交叉驗證</div>
    <div class="date">📅 2026 年 8 月 12 日 更新版</div>
  </div>
</header>

<!-- ===== 導覽 ===== -->
<nav class="topnav">
  <div class="wrap">
    <span class="brand">❄️ 空調報告</span>
    <a href="#compare">⚖️ 揀機比較</a>
    <a href="#table">📊 統合總表</a>
    <a href="#verify">✅ 官方驗證</a>
    <a href="#energy">⚡ 能源分析</a>
    <a href="#rank">📈 排名</a>
    <a href="#recommend">🏆 推薦</a>
    <a href="#price">💰 價格</a>
    <a href="#source">📝 來源</a>
  </div>
</nav>

<main class="wrap">

<!-- ===== 比較器 ===== -->
<section id="compare">
  <h2 class="sec">⚖️ 互動比較器 <span class="tag">揀機比較</span></h2>
  <p>喺下方揀 2 個或以上型號，底部會即時出現對比表。可以喺手機 / 平板 / 電腦用。</p>

  <div class="compare">
    <div class="head">
      <b>🛒 型號選擇區</b>
      <span class="sel" id="selCount">已選 0 個（最少 2 個）</span>
    </div>
    <div class="compare-tools">
      <button onclick="clearAll()">清除選擇</button>
      <button onclick="selectInverter()">選全部變頻</button>
      <button onclick="selectFixed()">選全部定頻</button>
      <div class="filters">
        <input type="search" id="q" placeholder="🔍 搜尋品牌/型號" oninput="renderList()">
        <select id="sortBy" onchange="renderList()">
          <option value="">預設排序</option>
          <option value="price">價格 低→高</option>
          <option value="energy">能源級別 優→劣</option>
          <option value="kwh">年耗電 低→高</option>
          <option value="cspf">CSPF 高→低</option>
        </select>
        <select id="fHp" onchange="renderList()">
          <option value="">全部匹數</option><option>1匹</option><option>1.5匹</option><option>2匹</option><option>2.5匹</option>
        </select>
        <select id="fType" onchange="renderList()">
          <option value="">全部類型</option><option>變頻</option><option>定頻</option>
        </select>
      </div>
    </div>
    <div class="model-list" id="modelList"></div>
  </div>

  <!-- 比較面板 -->
  <div class="panel" id="panel">
    <div class="phead">
      <b>📋 型號對比</b>
      <span style="display:flex;gap:6px;">
        <button onclick="clearAll()" style="background:#fff;color:var(--primary);">🗑 清除</button>
        <button onclick="closePanel()">✕ 關閉</button>
      </span>
    </div>
    <div class="pscroll" id="panelBody"></div>
  </div>
</section>

<!-- ===== 詳細報告 ===== -->
<div class="md-content">
__CONTENT__
</div>

</main>

<footer>
  <b>香港窗口式空調對比報告</b><br>
  能源/雪種/耗電：機電署 EMSD 官方資料庫全量核實（2026-08-12）<br>
  <span class="line">──────</span><br>
  本報告僅供選購參考，不構成購買建議 · 價格隨時變動
</footer>

<button id="backTop" onclick="window.scrollTo({top:0,behavior:'smooth'})" title="返回頂部">↑</button>

<script>
const MODELS = __MODELS_JSON__;
const FIELDS = __FIELDS_JSON__;
let selected = new Set();

function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function matches(m){
  const q=document.getElementById('q').value.trim().toLowerCase();
  const hp=document.getElementById('fHp').value;
  const ty=document.getElementById('fType').value;
  if(hp && m.hp!==hp) return false;
  if(ty && m.type!==ty) return false;
  if(q && !(m.brand.toLowerCase().includes(q)||m.model.toLowerCase().includes(q))) return false;
  return true;
}

function priceMin(m){
  const ms=String(m.price).match(/\$([\d,]+)/g)||['$999999'];
  return parseInt(ms[0].replace(/[$,]/g,''));
}
function energyVal(m){return parseInt(m.energy)||9;}

function sortModels(arr){
  const v=document.getElementById('sortBy').value;
  const a=[...arr];
  if(v==='price') a.sort((x,y)=>priceMin(x)-priceMin(y));
  else if(v==='energy') a.sort((x,y)=>energyVal(x)-energyVal(y));
  else if(v==='kwh') a.sort((x,y)=>(parseInt(x.kwh)||9999)-(parseInt(y.kwh)||9999));
  else if(v==='cspf') a.sort((x,y)=>(parseFloat(y.cspf)||0)-(parseFloat(x.cspf)||0));
  return a;
}

function renderList(){
  const list=document.getElementById('modelList');
  let arr=sortModels(MODELS.filter(matches));
  list.innerHTML=arr.map((m)=>{
    const id=m.brand+'|'+m.model;
    const on=selected.has(id);
    return `<label class="mitem ${on?'checked':''}">
      <input type="checkbox" ${on?'checked':''} onchange="toggle('${esc(id)}',this)">
      <span class="info">
        <span class="name">${esc(m.brand)} ${esc(m.model)}</span><br>
        <span class="tag">${esc(m.hp)} · ${esc(m.btu)} BTU · ${esc(m.type)} · ${esc(m.energy)} · ${esc(m.price)}</span>
      </span>
      <span class="badge">${m.type==='變頻'?'🔷':'🔶'} ${esc(m.energy)}</span>
    </label>`;
  }).join('')||'<div class="phint">冇符合嘅型號</div>';
}

function toggle(id,el){
  if(el.checked) selected.add(id); else selected.delete(id);
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  const lab=el.closest('.mitem');
  if(lab) lab.classList.toggle('checked', el.checked);
  const panel=document.getElementById('panel');
  if(selected.size>=2){ buildPanel(); panel.classList.add('open'); }
  else panel.classList.remove('open');
}

function updateUI(){
  document.getElementById('selCount').textContent=`已選 ${selected.size} 個（最少 2 個）`;
  const panel=document.getElementById('panel');
  if(selected.size>=2){ buildPanel(); panel.classList.add('open'); }
  else panel.classList.remove('open');
  renderList();
}

function selectedModels(){
  return MODELS.filter(m=>selected.has(m.brand+'|'+m.model));
}

function buildPanel(){
  const list=selectedModels();
  const body=document.getElementById('panelBody');
  const rows=[['<b>屬性</b>', ...list.map(m=>`<b>${esc(m.brand)}<br>${esc(m.model)}</b>`)]];
  for(const [key,label] of FIELDS){
    rows.push([label, ...list.map(m=>esc(m[key]))]);
  }
  body.innerHTML=`<table><thead><tr>${rows[0].map(h=>`<th>${h}</th>`).join('')}</tr></thead>
    <tbody>${rows.slice(1).map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

function clearAll(){selected.clear(); updateUI();}
function selectInverter(){selected=new Set(MODELS.filter(m=>m.type==='變頻').map(m=>m.brand+'|'+m.model)); updateUI();}
function selectFixed(){selected=new Set(MODELS.filter(m=>m.type==='定頻').map(m=>m.brand+'|'+m.model)); updateUI();}
function closePanel(){document.getElementById('panel').classList.remove('open');}

// 將 markdown 表格包裝成可橫向捲動容器（手機友好）
document.addEventListener('DOMContentLoaded', ()=>{
  document.querySelectorAll('.md-content table').forEach(t=>{
    if(!t.closest('.table-scroll')){
      const w=document.createElement('div');
      w.className='table-scroll';
      t.parentNode.insertBefore(w,t);
      w.appendChild(t);
    }
  });
  // GitHub 風格 slug（同 md 目錄錨點一致）：移除非文字/emoji，空格→-
  function slugify(t){
    return t.toLowerCase()
      .replace(/[^\p{Script=Han}A-Za-z0-9\s-]/gu, '')
      .replace(/\s+/g, '-');
  }
  // 導覽用 id（頂部導覽列）
  const navMap={'統合對比總表':'table','官方驗證結果':'verify','能源標籤分析':'energy',
    '排名':'rank','最終推薦':'recommend','獨立價格驗證':'price','資料來源':'source',
    '定頻 vs 變頻':'compare-base'};
  // 為所有章節標題加 slug id（md 目錄超鏈接用）+ 導覽 id（雙錨點並存）
  const used = new Set();
  document.querySelectorAll('.md-content h1,.md-content h2,.md-content h3').forEach(h=>{
    const t=h.textContent.trim();
    let slug = slugify(t);
    let base = slug, k = 1;
    while(used.has(slug)){ slug = base + '-' + (k++); }
    used.add(slug);
    // 搵導覽 id
    let navId = null;
    for(const [kw,id] of Object.entries(navMap)){
      if(t.includes(kw)){ navId = id; }
    }
    if(navId && navId !== slug){
      // 標題用導覽 id，前面加 slug 錨點 span（md 目錄可跳）
      h.id = navId;
      const sp = document.createElement('span');
      sp.id = slug;
      sp.style.cssText = 'display:block; position:relative; top:-70px; height:0;';
      h.parentNode.insertBefore(sp, h);
    } else {
      h.id = slug;
    }
    h.style.scrollMarginTop = '70px';
  });
  // 修正 md 目錄連結：手寫 anchor 同實際 slug 有出入時自動匹配最接近 id
  function findCloseId(hashId){
    const key = hashId.replace(/-/g, '').toLowerCase();
    const allIds = [...document.querySelectorAll('[id]')].map(e=>e.id).filter(Boolean);
    const exact = allIds.find(id => id.replace(/-/g,'').toLowerCase() === key);
    if(exact) return exact;
    // 反向前綴：實際 id 以連結 key 開頭（連結文字係標題簡寫）
    const pre = allIds.filter(id => id.replace(/-/g,'').toLowerCase().startsWith(key));
    if(pre.length) return pre[0];
    return null;
  }
  document.querySelectorAll('.md-content a[href^="#"]').forEach(a=>{
    const id=(a.getAttribute('href')||'').slice(1);
    if(id && !document.getElementById(id)){
      const fixed = findCloseId(id);
      if(fixed) a.setAttribute('href', '#' + fixed);
    }
  });
  // 自製平滑捲動（rAF 動畫，所有環境可靠）
  function smoothScrollTo(y){
    const start = window.scrollY;
    const diff = y - start;
    if(Math.abs(diff) < 2) return;
    const dur = Math.min(600, Math.max(250, Math.abs(diff) * 0.3));
    const t0 = performance.now();
    function step(t){
      const p = Math.min(1, (t - t0) / dur);
      const e = p < .5 ? 2*p*p : 1 - Math.pow(-2*p+2, 2)/2;
      window.scrollTo({top: start + diff * e, behavior:'instant'});
      if(p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }
  // 錨點連結平滑定位（md 內容內嘅 # 連結統一處理，確保任何環境都可跳轉）
  document.addEventListener('click', (e)=>{
    const a = e.target.closest('.md-content a[href^="#"]');
    if(!a) return;
    const id = (a.getAttribute('href')||'').slice(1);
    let el = document.getElementById(id);
    if(!el) return;
    e.preventDefault();
    // 若係零高度錨點 span，改為捲動到後面實際標題
    if(el.offsetHeight === 0 && el.nextElementSibling) el = el.nextElementSibling;
    const y = el.getBoundingClientRect().top + window.scrollY - 70;
    smoothScrollTo(Math.max(0, y));
    try{ history.replaceState(null, '', '#'+id); }catch(_){}
  });
  // 頂部導覽列：同樣用自製平滑捲動
  document.querySelectorAll('.topnav a').forEach(a=>{
    a.addEventListener('click', (e)=>{
      const href = a.getAttribute('href')||'';
      if(!href.startsWith('#')) return;
      const el = document.getElementById(href.slice(1));
      if(!el) return;
      e.preventDefault();
      const y = el.getBoundingClientRect().top + window.scrollY - 58;
      smoothScrollTo(Math.max(0, y));
      document.querySelectorAll('.topnav a').forEach(x=>x.style.color='');
      a.style.color='var(--accent)';
    });
  });
});

// 初始化
renderList();

// 返回頂部按鈕顯示
window.addEventListener('scroll',()=>{
  const b=document.getElementById('backTop');
  if(b) b.classList.toggle('show', window.scrollY>500);
});
</script>
</body>
</html>
"""


if __name__ == '__main__':
    build_html()
