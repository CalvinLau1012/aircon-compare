# ❄️ 香港空調對比報告（網頁版） ![version](https://img.shields.io/badge/version-v1.0.0-2ea44f)

> 香港市場空調（窗口式 / 分體式 / 流動式；淨冷/冷暖、定頻/變頻）全面對比
> **能源級別、雪種、年耗電已用機電署 EMSD 官方資料庫（1,927 型號）全量核實**
> **220 個型號已直接經品牌官網/官方網店/總代理逐型號核實（2026-08-15）**

## 🚀 立即使用

**線上版**：<https://calvinlau1012.github.io/aircon-compare/> （GitHub Pages）

**離線版**：下載 [`空調對比報告.html`](空調對比報告.html) 或 [`index.html`](index.html)，直接喺瀏覽器打開（單一檔案，可 email / WhatsApp 轉發）。

## ✨ 功能

- ⚖️ **互動比較器**：勾選 2 個或以上型號，即時彈出 18 項屬性對比表（自動高亮最平/最慳電/最高 CSPF）
- 🔍 搜尋（品牌/型號）+ 篩選（匹數、機型、能源級別、品牌）+ 排序（價格 / 能源 / 年耗電 / CSPF）
- 🛒 1,847 個型號附價格快照（2026-08-15），點擊 🔍 直接喺你瀏覽器 Google 搜最新價
- 📱 手機 / 平板 / 桌面全響應式（表格可橫向捲動）
- 📊 完整報告：定頻 vs 變頻、統合總表、官方驗證、能源分析、深度分析、排名、推薦、價格驗證、論壇討論精華

## 📊 數據

| 項目 | 數量 |
| ------ | ------ |
| 收錄型號 | 1,854（核心 29 + EMSD 全量 1,825） |
| 有價格型號 | 1,847（2026-08-15 快照，🔍 點擊搜最新價） |
| 有尺寸型號 | 1,676 |
| EMSD 官方核實型號 | 1,927（全量） |
| 品牌官網核實型號 | 220（8 品牌） |
| 對比屬性 | 18 項 |

## 🏭 品牌官網核實（2026-08-15）

| 品牌 | 官方渠道 | 型號數 |
| ------ | --------- | ------- |
| Carrier 開利 / Canopus 肯特 | century-carrier.com 世紀開利 | 79 |
| Rasonic 樂信 | shew.com.hk 信興 + rasonicshop.hk 官方網店 | 50 |
| Panasonic 樂聲 | panasonic.hk + 信興 eShop | 25 |
| COMFEE | feelcomfee.com | 18 |
| GENERAL 珍寶 | general-aircon.com 總代理（第一電業） | 16 |
| HITACHI 日立 | hitachi-homeappliances.com.hk | 16 |
| Midea 美的 | mideahk.com | 12 |
| FROSTAR 霜牌 | rasonicshop.hk（信興姊妹品牌） | 4 |

> ⚠️ Gree/TOSOT 代理官網無窗口機產品頁 → 維持 EMSD/Price 雙源並標註「待查」，**絕不編造**。

## 🔄 自動更新（新機偵測）

- **排程**：GitHub Actions 每日 00:30（香港時間）輕量偵測 EMSD；**有新機先觸發更新**（官網核實分兩日分批進行），冇新機就唔更新內容
- **新機偵測**：比較 EMSD 官方資料庫新舊型號，新上市型號自動入庫並喺網頁「🆕 最近新上市」顯示
- **價錢策略**：價格為 2026-08-15 快照（唔頻繁更新，僅供參考）；規格以 EMSD + 品牌官網為準
- **安全**：無需任何密鑰/Token；權限只限 `contents: write`；Action 版本固定（@v4/@v5）；設 concurrency 防重疊
- **穩定**：抓取後經「數據驗證閘門」(`validate_data.py`) 檢查數量喺安全範圍——唔合格就唔提交，保住現有數據；每次成功提交 = 可回溯快照，壞咗可一鍵還原
- 亦可喺 GitHub Actions 頁面手動觸發（workflow_dispatch）

## 🔧 自行重建

```bash

python generate_html.py        # 讀取 .md + JSON 資料庫 → 生成空調對比報告.html
copy 空調對比報告.html index.html   # 同步 GitHub Pages 入口

```

資料庫重新抓取（可選）：

```bash

python fetch_prices.py         # Price.com.hk 實價快照（1,847 型號，可選）
python fetch_official.py       # Panasonic/HITACHI/COMFEE 官網規格
python fetch_shew.py           # 信興官網 Rasonic 規格
python fetch_carrier.py        # 世紀開利 Carrier/Canopus 規格
python fetch_general.py        # 珍寶總代理規格
python fetch_rasonic.py        # 樂信官方網店價格

```

## 📁 專案檔案

| 檔案 | 說明 |
| ------ | ------ |
| `index.html` / `空調對比報告.html` | 網頁版報告（self-contained，無外部依賴） |
| `空調對比報告.md` | Markdown 報告全文 |
| `需求摘要.md` | 需求元文件 |
| `generate_html.py` | 網頁生成器（md + JSON → HTML） |
| `emsd_空調能源標籤.csv` | EMSD 官方資料庫快照（1,927 型號） |
| `prices.json` / `specs_emsd.json` | Price 實價 / 規格資料庫 |
| `official_specs.json` 等 7 個 `*_official.json` | 品牌官網核實數據 |
| `fetch_*.py` | 各數據源抓取腳本（EMSD/Price/官網） |

## ⚠️ 免責聲明

1. 價格及供應隨時間變動；價格為 2026-08-15 快照，僅供參考（以商戶實時報價為準）
2. 能源級別/雪種/年耗電以 EMSD 官方資料庫為準；尺寸/淨重/保養以品牌官網為準
3. Gree/TOSOT 保養等未能官網核實之規格為零售商交叉核實結果，表中標註「零售商規格」
4. 噪音 dB 為參考級（無官方文件）
5. 「論壇討論精華」為 LIHKG 用戶主觀評價摘錄，不代表本報告立場
6. 本報告由 AI 輔助製作，關鍵數據已盡量經官方核實，惟仍可能有錯漏
7. 本報告僅供選購參考，不構成購買建議

## 🏷️ 版本記錄

| 版本 | 日期 | 重點 |
| --- | --- | --- |
| **v1.0.0** | 2026-08-15 | 正式版：全量 1,854 型號 + 官網核實 220 + 互動比較器 + 論壇討論精華 + GitHub Pages |
| v0.2.0 | 2026-08-12 | EMSD 全量 1,927 型號核實（能源/雪種/耗電） |
| v0.1.0 | 2026-08-11 | 報告初版（29 型號統合對比） |

## 📅 更新日誌

| 日期 | 重點 |
| ------ | ------ |
| 2026-08-16 | 策略調整：價格保持快照、每週一自動更新（新機偵測 + 規格核實）；網站自動顯示更新狀態 |
| 2026-08-15 | 防禦性修補（禮貌爬蟲防封）+ 免責聲明全面更新 + markdownlint 全清 |
| 2026-08-15 | 官網核實 220 型號；EMSD 1,854 型號 + Price 1,847 實價；GitHub Pages 上線；「論壇討論精華」章節；機型篩選/導覽列/頁腳多輪優化；Gree/TOSOT 零售規格核實 + GWF12P/GWF18P 替換補完 |
| 2026-08-15 | v1.0.0 版本號；GitHub Actions 每日自動更新（驗證閘門 + 低權限 + 無密鑰） |
| 2026-08-12 | EMSD 官方資料庫全量 1,927 型號核實 |
| 2026-08-11 | 報告初版（29 型號對比） |

**更新日期**：2026-08-15 · **版本**：v1.0.0
