# AGENTS.md — Coding Agent 指引

呢個 repo 係「香港空調對比報告」自動更新項目。Agent 修改前請先讀呢份指引。

## 項目一句話

EMSD 官方空調資料 + 品牌官網規格 + BigGo 市場價，生成單一 `index.html` 互動比較器，每日由 GitHub Actions 自動更新。

## 主要命令

```bash
python -m pytest tests/ -q        # 必須全 pass
python validate_data.py           # 數據驗證閘門
python generate_html.py           # 生成 空調對比報告.html
python fetch_emsd.py              # 抓 EMSD CSV（有安全閘門，攞唔齊唔會覆寫）
python fetch_biggo.py --smoke     # BigGo 線上連線測試
python fetch_biggo.py --price-batch
python fetch_biggo.py --full-scan
python fetch_biggo.py --blacklist
```

## 檔案地圖

| 檔案 | 作用 |
| --- | --- |
| `fetch_emsd.py` | 每日抓 EMSD CSV + 新機偵測 |
| `fetch_biggo.py` | BigGo 主力價錢批次 |
| `fetch_pricesapi.py` | PricesAPI 核心 29 後備 |
| `batch_utils.py` | 價錢批次 meta / 進度共用工具 |
| `models_data.py` | 核心 29 型號資料庫（`MODELS` / `VERSION`） |
| `model_lifecycle.py` | 淘汰/停售黑名單管理 |
| `generate_html.py` | 報告 + 互動網頁生成 |
| `validate_data.py` | 上線前數據安全檢查 |
| `crawl_utils.py` / `price_utils.py` | 重用工具 |
| `model_blacklist.json` | 停售模型清單 |
| `biggo_prices.json` | BigGo 價錢快照 |
| `prices.json` | Price 舊快照後備 |
| `空調對比報告.md` / `README.md` / `需求摘要.md` | 報告與說明文件（改動要同步更新日誌） |

## 必須遵守

1. 唔好直接改 `index.html` 後唔跑 `generate_html.py`；index 係生成物。
2. 數據驗證唔過唔好 push。
3. API key 只放 GitHub Secrets，唔好寫入 repo。
4. BigGo 喺 GitHub Actions IP 可能被 429／timeout；批次前一定要 `--smoke`。
5. 淘汰黑名單只記錄「乾淨無市售報價」，網絡錯誤唔可以當淘汰。
6. 核心 29 型號同官方網店價型號受保護，唔可以自動淘汰。
7. 唔好編造規格或價格；搵唔到就保留「待查」。
8. 改 UI／CSS（尤其深色模式）要改 `generate_html.py` 嘅 `HTML_TEMPLATE`，之後必須跑 `python generate_html.py` + `cp 空調對比報告.html index.html`，並同步 `空調對比報告.md` / `README.md` / `需求摘要.md` 嘅更新日誌。

## UI / 深色模式

- 顏色變數同深色 override 全部喺 `generate_html.py` 嘅 `HTML_TEMPLATE` `<style>` 入面。
- 深色模式主要靠 `@media (prefers-color-scheme: dark)`；新增 UI 元件時要同時提供深色配色，唔好用寫死嘅淺色背景（會重現對比度問題）。

## 生成物

- `index.html`：GitHub Pages 直接發佈。
- `空調對比報告.html`：本地生成，`.gitignore` 唔入庫。
