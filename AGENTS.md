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
python scripts/validate_metadata.py      # metadata.json Schema 驗證
```

## 項目簡介

EMSD 官方空調資料 + 品牌官網規格 + BigGo 市場價，生成單一 `index.html` 互動比較器，每日由 GitHub Actions 自動更新。

## AI 協作角色

- **OpenAI Codex**：規劃、治理約束、整合設計、獨立安全審閱、最終驗收、返修決策。
- **DeepSeek `deepseek-flash`（thinking max）**，經 Pi coding-agent／`ds-exec`：本地診斷、實作、測試、證據整理、分支整合。
- **人類維護者**：批准 R2/R3 決策、在自己的 SSH 終端輸入 sudo、最終 merge／發布。

AI 輔助唔等於官方資料已由 AI 證實；數據以 EMSD／品牌官網／可重跑測試證據為準。「DeepSeek 鯨魚娘」係歷史設計主題，與工程協作角色分開。

## 主要命令

```bash
python -m pytest tests/ -q        # 必須全 pass
python validate_data.py           # 數據驗證閘門
python generate_html.py           # 生成 空調對比報告.html（能源分佈等動態區塊由實際資料生成）
python fetch_emsd.py              # 抓 EMSD CSV（有安全閘門，抓不齊不會覆寫）
python fetch_biggo.py --smoke     # BigGo 連線測試（多候選，首個有價即通過）
python fetch_biggo.py --price-batch
python fetch_biggo.py --force-batch [N]
python model_lifecycle.py         # 顯示停售黑名單
```

> 自建／私人部署線（容器、持久發佈、sandbox）已移離公開 repo，詳見私人包；
> 公開候選只保留 CI 每日更新與 Pages 發佈路徑。正式部署／回滾仍由維護者喺自己環境執行。

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
| `docs/AIRCON_COMPARE_GOVERNANCE.md` | 唯一治理源（內嵌功能註冊表/metadata Schema/成功標準） |
| `docs/DECISIONS.md` | 決策記錄（人類決策與技術轉向，按模板追加） |
| `scripts/prepare_runtime_data.py` | runtime 資料準備（黑名單 canonical 遷移守衛，只跑一次） |
| `scripts/gen-metadata.py` | 兩階段 metadata；`--emsd-receipt` 收據事實（hash-bound、實際 UTC→HKT 日期） |
| `scripts/validate_metadata.py` | 完整 Draft 2020-12 metadata 驗證；`--core` 驗未 finalize 核心事實 |
| `scripts/feature-check.py` | GATE-03 功能契約：pytest collection node id＋靜態斷言＋`--run-tests` 執行證據 |
| `scripts/run_official_batch.py` | 官網批次推進閘門（有實際輸出證據先 `advance_queue`） |
| `scripts/postdeploy_check.py` | GATE-08 部署後核對（線上 metadata／payload hash／瀏覽器 runtime 顯示） |
| `scripts/archive_release.py` | GATE-09 長期歸檔（CHECKSUMS＋PROVENANCE；同 tag 唔可 clobber） |
| `.github/workflows/pages-deploy.yml` | D2-A：PR build／gates 不 deploy；master deploy `needs` build，`github-pages` environment＋最小 Pages 權限 |
| `scripts/build_pages_artifact.py` | D2-A：Pages artifact 只可含 `deploy_payload.json` 公開檔；拒 symlink／缺檔／traversal／額外私人檔 |
| `scripts/check_public_history.py` | D4-A：全 reachable Git history credential audit；credential findings 必須 0；報告寫 repo 外 |
| `fetch_emsd.py` raw receipt | D7-A：逐頁原始 bytes hash＋公開 `emsd_raw_receipt.json`；private sink 失敗阻斷；90 日 retention |
| `.github/workflows/freshness-monitor.yml` / `scripts/check_freshness.py` | D8-A：`age > 72h` monitor；issue 去重、狀態改變 update、恢復 close |
| `scripts/run_official_batch.py` pending | D1-B：純 coverage 缺口可 `queue-kept-pending-coverage` 保留 queue；硬失敗仍非零阻斷 |
| `tests/test_restore_drill.py` | 公開 fixture-only 應用＋數據恢復演練（無 sudo／docker／生產） |
| `CHANGELOG.md` | 版本變更記錄（Keep a Changelog 風格；發布時歸檔 Unreleased） |
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
9. 純更新日誌／文檔改動（`空調對比報告.md` 更新日誌、`README.md`、`需求摘要.md`、`docs/*`、`CHANGELOG.md`）只 commit 文檔本身，**唔好**順手重生 `index.html`：`index.html` 屬 `deploy_payload.json` payload，手動重生會令 `metadata.json` 的 `releasePayloadHash` 對唔上，master push 的 Pages build 會 fail-closed；留返 `index.html` 由下一次 daily 流水線連同 metadata 一併重生（見 D24）。注意 `空調對比報告.md` 同時生成 `index.html` 同 `空調對比報告.pdf`：改 md 更新日誌預期會有一次紅 run（唔重生檔案 → GATE-08 `payload.pdf_matches_metadata` 紅；手動重生 → build gate `releasePayloadHash` 紅），下一次 daily 全量重生後恢復；只改 README／需求摘要／CHANGELOG／docs 就零紅 run。真正改 UI／CSS／報告內容仍按規則 8 重生。
10. 文檔只可追加、不可刪除或改寫既有記述（含更新日誌、STATUS、MATRIX、ADR、runbook）：歷史快照一律保留，更新以新增節／新條目記錄，並標明日期同證據；用戶日後嘅改變亦只可追加。需求以 `需求摘要.md`（元文件）為準。格式／錯字修正唔算狀態改寫，但必須喺 commit message 標明。見 D25。

## UI / 深色模式

- 顏色變數同深色 override 全部在 `generate_html.py` 的 `HTML_TEMPLATE` `<style>` 內。
- 深色模式主要靠 `@media (prefers-color-scheme: dark)`；新增 UI 元件時要同時提供深色配色，不要用寫死的淺色背景（會重現對比度問題）。

## 生成物

- `index.html`：GitHub Pages 直接發佈。
- `空調對比報告.html`：本地生成，`.gitignore` 不入庫。
