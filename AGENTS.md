# AGENTS.md — Coding Agent 指引

這個 repo 是「香港空調對比報告」自動更新項目。Agent 修改前請先閱讀本指引。

## 治理源（必讀）

**`docs/AIRCON_COMPARE_GOVERNANCE.md`** 是唯一項目治理源（內嵌功能註冊表、metadata Schema、成功標準）。修改前必須完整讀取，並：
- 區分 `REQUIREMENT` / `HISTORICAL_CLAIM` / `TARGET_STATE` / `OBSERVED` / `UNKNOWN`；
- 不得降級 `required` 功能、不得偽造 metadata／測試證據、不得放寬阻斷門禁；
- 版本號只修改 `models_data.py` 的 `VERSION`；部署事實由流水線生成的 `metadata.json` 提供。
- 人類決策與技術轉向的記錄見 `docs/DECISIONS.md`；新增決策須按該文件模板追加並說明原因。

治理檢查命令：
```bash
python scripts/extract_governance.py     # 六個規範區塊提取驗證
python scripts/feature-check.py          # Registry Schema + 測試綁定檢查
python scripts/validate-metadata.py      # metadata.json Schema 驗證
```

## 項目簡介

EMSD 官方空調資料 + 品牌官網規格 + BigGo 市場價，生成單一 `index.html` 互動比較器，每日由 GitHub Actions 自動更新。

## 主要命令

```bash
python -m pytest tests/ -q        # 必須全 pass
python validate_data.py           # 數據驗證閘門
python generate_html.py           # 生成 空調對比報告.html
python fetch_emsd.py              # 抓 EMSD CSV（有安全閘門，抓不齊不會覆寫）
python fetch_biggo.py --smoke     # BigGo 線上連線測試
python fetch_biggo.py --price-batch
python fetch_biggo.py --full-scan
python fetch_biggo.py --blacklist
```

## 檔案地圖

| 檔案 | 作用 |
| --- | --- |
| `fetch_emsd.py` | 每日抓 EMSD CSV + 新機偵測 |
| `fetch_biggo.py` | BigGo 主力價錢批次（官方 JSON API） |
| `fetch_pricesapi.py` | PricesAPI 核心 29 後備 |
| `batch_utils.py` | 價錢批次 meta / 進度共用工具 |
| `models_data.py` | 核心 29 型號資料庫（`MODELS` / `VERSION`） |
| `model_lifecycle.py` | 淘汰／停售黑名單管理 |
| `generate_html.py` | 報告 + 互動網頁生成 |
| `validate_data.py` | 上線前數據安全檢查 |
| `crawl_utils.py` / `price_utils.py` | 重用工具 |
| `model_blacklist.json` | 停售模型清單 |
| `biggo_prices.json` | BigGo 價錢快照 |
| `prices.json` | Price 舊快照後備 |
| `空調對比報告.md` / `README.md` / `需求摘要.md` | 報告與說明文件（改動要同步更新日誌） |

## 必須遵守

1. 不要直接改 `index.html` 後不執行 `generate_html.py`；index 是生成物。
2. 數據驗證不通過不要 push。
3. API key 只放 GitHub Secrets，不要寫入 repo。
4. BigGo 在 GitHub Actions IP 可能被 429／timeout；批次前一定要 `--smoke`。
5. 淘汰黑名單只記錄「確認無市售報價」，網絡錯誤不可以當淘汰。
6. 核心 29 型號同官方網店價型號受保護，不可以自動淘汰。
7. 不要編造規格或價格；找不到就保留「待查」。
8. 改 UI／CSS（尤其深色模式）要改 `generate_html.py` 的 `HTML_TEMPLATE`，之後必須執行 `python generate_html.py` + `cp 空調對比報告.html index.html`，並同步 `空調對比報告.md` / `README.md` / `需求摘要.md` 的更新日誌。

## UI / 深色模式

- 顏色變數同深色 override 全部在 `generate_html.py` 的 `HTML_TEMPLATE` `<style>` 內。
- 深色模式主要靠 `@media (prefers-color-scheme: dark)`；新增 UI 元件時要同時提供深色配色，不要用寫死的淺色背景（會重現對比度問題）。

## 生成物

- `index.html`：GitHub Pages 直接發佈。
- `空調對比報告.html`：本地生成，`.gitignore` 不入庫。
