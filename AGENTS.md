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
11. （2026-09-24 觀察補充）規則 9 嘅「只改 docs 就零紅 run」有前提：payload 內嘅 md 生成物（`index.html`／PDF）必須已經同 md 同步（上一次 daily 已跑完、md 之後未再改）。一旦 md 有新改動而 daily 未跑，之後任何 push（包括純 docs）都會 GATE-08 `payload.pdf_matches_metadata` 紅，直到下一次 daily 全量重生；證據：run 35952357474（純文檔追加）同 35952373427。

## UI / 深色模式

- 顏色變數同深色 override 全部在 `generate_html.py` 的 `HTML_TEMPLATE` `<style>` 內。
- 深色模式主要靠 `@media (prefers-color-scheme: dark)`；新增 UI 元件時要同時提供深色配色，不要用寫死的淺色背景（會重現對比度問題）。

## 生成物

- `index.html`：GitHub Pages 直接發佈。
- `空調對比報告.html`：本地生成，`.gitignore` 不入庫。

## 2026-10-01 文件／網頁日誌維護補充（追加；D32）

- 現行版本與部署索引見 README／CHANGELOG 最新入口；候選記錄的「未部署」屬歷史，最新事實見 docs/STATUS §29 及後續追加。
- 報告 md／HTML_TEMPLATE 修改先在隔離目錄生成明示 fixture HTML／PDF／metadata，做手機及深色瀏覽器驗證；不要手改 production metadata 或沿用舊 payload hash。
- 完成可信 PR CI 後，經 D31 `rebuild_verified_snapshot=true`、`force_price_batch=false` 在 master 重建，再核對 exact source/artifact 的 Pages 與 GATE-08。無須資料來源或 BigGo 呼叫；72h acquisition 年齡不能因重建而延長。
- 正常 daily 不變：00:30 HKT；價格 stage inactive 應零 token／search。Price.com 抓取仍放棄，私人 server 工作仍暫停。
- pytest 的 snapshot preflight 正向測試需乾淨 checkout；有使用者未提交修改時，用隔離 worktree，不覆蓋／stage／stash 該修改。新部署不能用上一輪測試數字冒充驗收。

## 2026-10-12 現況與操作補充（追加；承 D35）

- **最新狀態入口**：README 頂部 2026-10-12 追加、`docs/STATUS.md` §31、`CHANGELOG.md` 部署索引；2026-10-01 及之前嘅「未部署／暫停／disabled」字句屬歷史。
- **部署事實**：PR #24 merge `742404b`；不可變 payload v1.2.9／build `B20261010.142.1`／artifact 來源 `7994e7b`；Pages run `38185435570` success、GATE-08 37 checks／14 browser。`artifact commit` 唔等於 merge commit，亦唔可以自行修改 `metadata.json`。
- **排程語義**：GitHub Actions schedule 係預定觸發；官方明示高負載可延遲甚至丟棄（[events](https://docs.github.com/en/actions/reference/events-that-trigger-workflows#schedule)、[troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)）。00:30 HKT 唔係準時保證；本項目未有延遲量測（UNKNOWN）。**唔可以**因為延遲而自動補跑、重複查價或改 cron。
- **daily 現況**：workflow `335052362` active；首次恢復後定時完整批次尚未觀察。GitHub Actions 同維護者私人部署係**獨立排程**；共用 coordinator 只協調 BigGo stage，非整體主備、不保證先後。公開文檔只泛稱私人部署，唔寫 host／domain／path／私人 repo 標識／token 標籤／期限。
- **BigGo 受控路徑**：daily 只可經 `scripts/biggo_stage_runner.py`；inactive／完成＝零 provider、零 coordinator；active 經 lease／budget／cooldown／冪等＋write-ahead intent；smoke 候選由 `biggo_candidate.select_candidate()` 動態揀（非固定清單）。手動診斷預設零流量；`--check-coordinator`（唯讀，≤1 GET）同 `--execute`（provider 測試）互斥、語義不可互換；合法 `no-price` 唔等於網絡故障。
- **文件規則不變**：純文件改動只 commit 文檔；報告 md／HTML_TEMPLATE 文案改動先做隔離 fixture 驗證（D32），再由 D31 `rebuild_verified_snapshot=true`、`force_price_batch=false` 全量重生 `index.html`＋PDF＋metadata 並核對 Pages／GATE-08。metadata 日期唔會因文件更新而刷新。
- **2026-10-12 更正**：上面 2026-10-01 節「私人 server 工作仍暫停」已過期；私人部署已恢復（獨立排程 00:30 HKT 運行中），原文保留作歷史。
