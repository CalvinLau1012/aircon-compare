# ❄️ 香港窗口式空調對比報告（網頁版）

> 香港市場窗口式空調（含淨冷/冷暖、定頻/變頻、有/無遙控）全面對比
> **能源級別、雪種、年耗電已用機電署 EMSD 官方資料庫（1,927 型號）全量核實**
> **282 個型號已直接經品牌官網/官方網店/總代理逐型號核實（2026-08-15）**

## 🚀 立即使用

**線上版**：https://calvinlau1012.github.io/aircon-compare/ （GitHub Pages）

**離線版**：下載 [`空調對比報告.html`](空調對比報告.html) 或 [`index.html`](index.html)，直接喺瀏覽器打開（單一檔案，可 email / WhatsApp 轉發）。

## ✨ 功能

- ⚖️ **互動比較器**：勾選 2 個或以上型號，即時彈出 18 項屬性對比表
- 🔍 搜尋（品牌/型號）+ 篩選（匹數、類型、能源、機型）+ 排序（價格 / 能源 / 年耗電 / CSPF）
- 📱 手機 / 平板 / 桌面全響應式
- 📊 完整報告：統合總表、官方 EMSD 驗證、品牌官網核實、能源分析、排名、推薦、價格驗證

## 📊 數據

| 項目 | 數量 |
|------|------|
| 收錄型號 | 1,854（核心 29 + EMSD 全量 1,825） |
| 有價格型號 | 1,847（Price.com.hk 實價連結） |
| 有尺寸型號 | 1,676 |
| EMSD 官方核實型號 | 1,927（全量） |
| 品牌官網核實型號 | 282（8 品牌） |
| 對比屬性 | 18 項 |

## 📁 專案檔案

| 檔案 | 說明 |
|------|------|
| `index.html` / `空調對比報告.html` | 網頁版報告（self-contained） |
| `空調對比報告.md` | Markdown 報告 |
| `需求摘要.md` | 需求元文件 |
| `generate_html.py` | 網頁生成器 |
| `emsd_空調能源標籤.csv` | EMSD 官方資料庫快照（1,927 型號） |
| `prices.json` / `specs_emsd.json` | Price 實價 / 規格資料庫 |
| `official_specs.json` 等 `*_official.json` | 品牌官網核實數據（8 品牌） |
| `fetch_emsd.py` / `verify_emsd.py` | EMSD 資料下載 / 驗證腳本 |
| `fetch_prices.py` / `fetch_og_specs.py` | Price.com.hk 抓價 / 規格腳本 |
| `fetch_official.py` / `fetch_shew.py` / `fetch_carrier.py` 等 | 品牌官網核實腳本 |

## ⚠️ 免責聲明

價格及供應隨時間變動；本報告僅供選購參考，不構成購買建議。

**更新日期**：2026-08-15
