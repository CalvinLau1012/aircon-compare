# 2026-10-26 網站更新日誌、GitHub 文件與私人站失效連結修復（候選報告）

> 狀態：**候選完成、未正式部署**。本報告只追加，不刪改舊記錄。公開端 source commit `f320d3beb721f172b8474cf6aa1e5ee5a509a36a`（base `a445c0d1fb0b9b6a86550dc78bdf513d9f39dd4a`）；私人端候選 base `365b783d72eb5679eee8b6b0be96019e722b05a9`，候選 commits `b47c07c622022d56a25e2fc14d8e1bdeb2b1add6`（導覽／日誌）及 `3c71cfa4cb436b0732efef12c659f2c8c7be6e5c`（mobile／PDF 連結）。私人候選 build 識別不在此公開文件記錄。

## 1. 已完成候選

### 1.1 網站更新日誌

- 唯一內容來源仍是 `空調對比報告.md`；HTML／PDF 由 `generate_html.py`／`generate_pdf.py` 重建，沒有手改 `/app/web` 或生成物 bytes。
- 已追加 **2026-10-26** 使用者版日誌，覆蓋：
  - 公開／私人兩端資料日期 2026-10-26，日期由成功收據 UTC 換算 HKT，不硬編；
  - 當次 1,782 筆登記／1,721 個型號，及「以頁面 metadata 為準」；
  - 官網可信 pending 不令 Actions 紅／不發故障 email，真正抓取／收據／hash／驗證錯誤仍告警；
  - BigGo 歷史原始取得時間 unknown，只顯示「最近成功套用」，不以套用／發布時間補造；
  - 兩端獨立排程、共享 BigGo 只接收已完成結果、零重複請求；
  - 原子發布／候選驗收隔離（PR #33／`d18a1bd`）；
  - 公開 2026-10-24／10-25 daily、Pages、freshness 及私人自然 daily 的當次證據與界線。
- 公開候選 HTML／PDF 及私人候選 HTML／PDF 均已生成；日誌首條同為 2026-10-26。

### 1.2 GitHub 文件同步

- 更新：`README.md`、`CHANGELOG.md`、`AGENTS.md`、`CONTRIBUTING.md`、`.github/copilot-instructions.md`、`需求摘要.md`、`空調對比報告.md`。
- 現況／決策：`docs/STATUS.md` §32、`docs/DECISIONS.md` D42、`docs/GOVERNANCE_MATRIX.md`、`docs/README.md`。
- 部署／來源邊界：`docs/adr/ADR-002-official-batch-advance-policy.md`、`docs/adr/ADR-003-pages-envelope-and-daily-handoff.md`、`docs/architecture/2026-10-18-reviewed-models.md`、`docs/architecture/2026-10-18-public-private.md`。
- Machine-readable inventory：`docs/DOCS_SYNC_INVENTORY_20261026.json`，共 47 項：updated 16／added 7／no-change 12／historical-only 11／updated-append-only 1。
- 權威層級：治理＝`docs/AIRCON_COMPARE_GOVERNANCE.md`（本輪無 Schema／Registry／GATE 變更，故 no-change）；現況＝`docs/STATUS.md` §32；決策＝D42；需求＝`需求摘要.md`；變更＝`CHANGELOG.md`；網站日誌＝`空調對比報告.md`。ADR、`2026-10-18-*`／`2026-10-19-*` architecture、blacklist migration 保留 historical-only。

### 1.3 私人站失效連結

由私人首頁同源爬查（`a[href]`、`link[href]`、`script[src]`、`img[src]`），實際失效為 r21 文件導覽五個同源 `.md`：

| 來源頁 | 原 href | 正規 URL | 狀態 |
| --- | --- | --- | --- |
| `/` | `README.md` | `/README.md` | 404 |
| `/` | `CHANGELOG.md` | `/CHANGELOG.md` | 404 |
| `/` | `docs/STATUS.md` | `/docs/STATUS.md` | 404 |
| `/` | `docs/AIRCON_COMPARE_GOVERNANCE.md` | `/docs/AIRCON_COMPARE_GOVERNANCE.md` | 404 |
| `/` | `空調對比報告.md` | `/空調對比報告.md` | 404 |

原因：私人 web root 只服務四檔及 runtime JSON，不提供 repo `.md`。修正在共同報告來源把五個連結改為公開 GitHub `blob/master/...` URL；不是 patch 當前 `/app/web` bytes。候選私人站重爬：內部連結 21 個、0 個 4xx/5xx、0 個缺 fragment、0 個缺本地資產；四個下載檔全部 200。

### 1.4 PDF 連結與 mobile

- `generate_pdf.py` 只把絕對 HTTP(S) 連結輸出成 ReportLab 可點 annotation；相對 repo 路徑／fragment-only 只保留顯示文字。純文字 URL 會 linkify，並移除 URL 尾隨中文标點。
- 公開候選 PDF：10 個可點 URI、0 個相對 URL；私人候選 PDF：16 個可點 URI、0 個相對 URL。
- `candidate-consistency-final.json` 驗證：兩端 source／HTML／PDF 的 9 個日誌類別全部一致；兩端 metadata `releasePayloadHash` 與重算四檔 payload hash 完全相等；`datasetDate=2026-10-26`、`biggo.lastAcquiredAt=null`、`official.status=pending`；HTML 無同源 `.md` 連結。
- 私人 PDF changelog 兩欄表加最小類別欄寬，避免逐字斷行；公開 PDF 亦保持可讀。
- Firefox 候選瀏覽器檢查（375／768／1280 × light／dark，共 6 組）：公開及私人均無水平溢出、無 console/pageerror；changelog 首條 2026-10-26；比較器可選兩項、開啟 panel、關閉。截圖在 evidence 目錄。

## 2. 實際測試證據

### 2.1 公開候選

| 檢查 | 結果 |
| --- | --- |
| 完整 pytest（候選生成頁；排除 `test_verify_snapshot_rebuild.py`） | **1385 passed、7 skipped、0 failed／0 error**；JUnit tests=1392 |
| `test_verify_snapshot_rebuild.py`（clean source commit，未改 payload） | **25 passed** |
| 含 preflight 的完整 pytest（候選生成頁） | 1407 passed、7 skipped、3 failed；3 個失敗只係候選 payload 未 finalize／worktree 非 clean 的 preflight precondition；同測試 clean commit 25/25 pass |
| 針對性 docs/site/PDF/changelog/hero | 17 passed |
| `feature-check.py --run-tests`（候選生成頁，Firefox fixture） | 15 required、18 nodes 全 passed，無 skip／xfail |
| `extract_governance.py` | rc=0，6 canonical + 3 extensions |
| `validate_data.py` | rc=0，1,782 登記／1,721 型號 |
| `validate_metadata.py metadata.json` | rc=0，v1.2.9／datasetDate 2026-10-26 |
| `check_docs_links.py` | 24 Markdown、71 links、16 fragments、0 broken；52 external 另列 |
| `check_public_privacy.py --mode worktree` | 0 命中 |
| `check_site_links.py` 公開候選 | 內部 21、broken 0、四檔 200 |
| `check_site_links.py` 私人候選 | 內部 21、broken 0、四檔 200 |
| `check_site_links.py` 私人 live 修復前鏡像 | 內部 26、broken 5（五個 `.md` 404），重現實際壞鏈 |

### 2.2 私人候選

| 檢查 | 結果 |
| --- | --- |
| `tests/test_private_site_links_20261026.py` + `tests/test_changelog_presentation.py` | 7 passed |
| `tests/browser_smoke.py` responsive/tooltip（Firefox） | 2 passed |
| 私人候選 PDF links | 16 absolute URI、0 relative |
| 私人候選 site checker | 0 broken；四檔 200 |
| 私人候選 browser 6 組合 | 0 overflow、0 JS error、panel open |
| 私人完整 pytest（Firefox） | 1377 passed、7 skipped、17 failed；失敗主要是舊 fixture 硬編 1,816／hero 高度（Firefox 量測）、metadata twostage 對候選資料的假設、preflight dirty／payload 未 finalize，以及 `test_pdf_presentation_r24` 仍期望舊「最後部署」字眼。與本輪連結／日誌修正無直接關係；未把這些失敗隱藏。 |

### 2.3 證據位置與 hash

- 本地 evidence：`本地 evidence 目錄（路徑不公開）`。
- 主要檔：`public-pytest-final.xml`／`.log`（`3f327c712e9c82ae9dfbf21f91038ec11c4990a343c14d0bb6ef9bea7166fd8c`／`6b8aaac49c366a5f9c62c5716700810f2937ed7e707c3fd13dc578678c4867d5`）、`private-pytest.xml`／`.log`、`private-live-before-links.json`（`1ed95d7c92bfa7a0bbd5e88cac63ac3bc66848bc8ca5bd54113069bcb459bb10`）、`private-candidate-links.json`（`af314f90c9cb44ac8183cae7090793eaf7e685737cb96b523b24ee8f154e8275`）、`public-candidate-links.json`（`a15f8f3eb9bd9c1bfe79b83cd42819f147567e35ab636f41771a2efce00fdc9d`）、`candidate-consistency-final.json`（`04c77b32f9355bfba6a6d0030528067a282e86d9c928017688931caf236bab2c`）、6×2 screenshots、`docs-links-after-commit.json`（`1259080239a77a522ccdb24f07de65f7735aecc36dd0a1ffc69782de2b46cb81`）。
- 公開候選四檔：`public-candidate-site/`：
  - `index.html` `1e5bcd61649999e778f6af0ea6401f207fda29c78128b7a69ffe6eb3aa8fcf1b`
  - `空調對比報告.pdf` `9a32f24571d62be9b98dd787541852a2f58749869544bc013f6337bde1345eea`
  - `emsd_空調能源標籤.csv` `dcbe0ca2d4e9e25f7f62b6fcff54afd40d280cb7679e77a660b67e132c45ed00`
  - `metadata.json` `69efc6e51fe8e56ceaae21a5d4a92f67413d9fc83cf57844737e2223e7dddba7`
- 私人候選四檔：`private-candidate-site/`：
  - `index.html` `1895d57a4977b5c6039a39627ab679f3de820efa89a94fd08e351ff2a0b801ee`
  - `空調對比報告.pdf` `4329fb1011458d19ae05ff366ee09f172b90436271e9e88ba74aeafe09e430c5`
  - `emsd_空調能源標籤.csv` `dcbe0ca2d4e9e25f7f62b6fcff54afd40d280cb7679e77a660b67e132c45ed00`
  - `metadata.json` `320adaaba352055f91cf29ca8efc19d17bdc55d146e34d50c57e7b79ee94784d`
  兩者均未部署。

## 3. 生產前後資料與 BigGo protected state

- 本輪零 BigGo／EMSD／品牌 provider 請求；零 coordinator cycle／lease／budget／usage／history 寫入；零 Secrets／Variables／排程變更；零 Docker／正式容器操作；零 daily 補跑。
- 公開 protected 檔案 before hash 已記錄（`protected-pre.sha256`）：`price-policy.json` `53e336f1…`、`price_demand.json` `f773ac15…`、`prices_meta.json` `a7eedb4b…`、`official_batch_status.json` `916cb0eb…`、`update_queue.json` `cacfac61…`、`deploy_payload.json` `3daa0974…`、`metadata.json` `b17ea166…`、`emsd_receipt.json` `3066450e…`、`emsd_raw_receipt.json` `c7726b47…`、`biggo_prices.json` `fad1e8c3…`。本輪未改這些檔案。
- BigGo 共享 baseline 仍為 completed cycle 2026-10-10 stage 7、token1/search145、無 active lease。公開現行 metadata：`lastAcquiredAt=null`、`lastAppliedAt=2026-10-20T20:15:41Z`；私人現行 metadata：`lastAcquiredAt=null`、`lastAppliedAt=2026-10-21T16:30:05Z`。舊包原始取得時間繼續 unknown，沒有用套用／發布時間補造。
- 私人 container／receipt 未直接讀取（Docker／SSH 管理邊界）；只讀 live HTTPS metadata。此限制已在 `docs/STATUS.md` §32 明列。

## 4. 仍需 Root 做的正式操作

1. **公開 GitHub**：審核 source commit `f320d3b`，決定 merge／PR；用 D31 `rebuild_verified_snapshot=true`、`force_price_batch=false` 重建 `index.html`＋PDF＋metadata，再核對 Pages／GATE-08。不要把本候選 commit 直接當已部署。
2. **私人站**：以私人候選 commits（base `365b783` + `b47c07c` + `3c71cfa`）套用到正式源，使用正式 runtime 資料重建四檔；部署前後核對四檔、metadata、PDF 重建、crawl 正式 HTTPS 及 BigGo protected state。回退：保留現行四檔及上一 image／資料備份，先設 receive-only，再回切兼容程式；不刪 volume／image。
3. **權威元文件**：`docs-snapshot/ROOT-需求摘要.md` 已以 append-only 追加本輪摘要；Root 只讀核對，不要改寫舊記錄。
4. **正式驗收**：下一次自然 schedule 仍需重新觀察；不要用本輪當次 run 冒充下一次成功。

## 5. 風險／未決

- **Chromium 環境限制**：本機 Chromium／headless shell 對 `<select>` 會 renderer crash；本輪 browser 測試用 Firefox fixture（`AIRCON_TEST_BROWSER=firefox` / `AIRCON_LINK_BROWSER=firefox`）。CI 的 Chromium 結果仍以 GitHub run `40038875496`（1,410 tests）為當次證據；本輪未重跑 CI。
- **私人完整 pytest 17 failed**：主要為舊 fixture 硬編數字、Firefox 量測、metadata twostage 對候選資料的假設及 preflight dirty；不是全部與本輪改動無關，已在 §2.2 如實列出，未當成全綠。
- **外部連結**：site checker 只列清單，沒有把 LIHKG／品牌官網等反爬或暫時錯誤當內部壞鏈；正式上線後仍應由 Root 抽查。
- **私人資料 baseline**：私人候選四檔使用 current public data snapshot + 私人 runtime metadata 作視覺／連結驗證；正式部署必須用私人正式 runtime 資料重建，不能直接把候選 CSV／JSON 當生產資料。
- **正式網站仍未更新**：公開 Pages 及私人站目前仍是上一個產物；本輪只交付候選與證據。

## 6. 修改檔案（公開候選）

- 網站來源：`空調對比報告.md`、`frontend/public/style.css`、`generate_pdf.py`。
- 文件：`README.md`、`CHANGELOG.md`、`AGENTS.md`、`CONTRIBUTING.md`、`.github/copilot-instructions.md`、`需求摘要.md`、`docs/README.md`、`docs/STATUS.md`、`docs/DECISIONS.md`、`docs/GOVERNANCE_MATRIX.md`、`docs/adr/ADR-002-official-batch-advance-policy.md`、`docs/adr/ADR-003-pages-envelope-and-daily-handoff.md`、`docs/architecture/2026-10-18-reviewed-models.md`、`docs/architecture/2026-10-18-public-private.md`。
- 新增：`docs/DOCS_SYNC_INVENTORY_20261026.json`、`scripts/check_docs_links.py`、`scripts/check_site_links.py`、`tests/test_docs_links.py`、`tests/test_site_links.py`、`tests/test_pdf_links.py`、`reports/DOCS_CHANGELOG_LINKS_20261026.zh-Hant.md`。
- 未改：`metadata.json`、`deploy_payload.json`、`price-policy.json`、`price_demand.json`、`prices_meta.json`、`official_batch_status.json`、`update_queue.json`、`biggo_prices.json`、`emsd_receipt.json`、`emsd_raw_receipt.json`、`index.html`（保留 production payload；由 D31 重建）。
