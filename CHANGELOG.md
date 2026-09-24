# Changelog

本文件記錄 aircon-compare 的所有顯著變更（Keep a Changelog 風格）。
版本號遵循 SemVer（`MAJOR.MINOR.PATCH`），唯一手動來源為 `models_data.py` 的 `VERSION`。

## [Unreleased]

### Node.js 24 官方 Actions／bounded smoke／ubuntu-24.04 runner hotfix（未發布）

> 2026-09-24 使用者要求：修復 GitHub Actions deprecation 警告，但唔可以為消除警告
> 降低任何門禁。PR #14 已合併並完成 production daily／Pages 驗收；不改產品版本，
> 本節仍列於 Unreleased 維護記錄。

- **官方 Actions 升級 Node.js 24**：`.github/workflows` 全部 `actions/*` 由 node20
  runtime 升到官方 node24 release，維持完整 40-hex SHA pin（唔用 mutable major tag）：
  checkout `v7.0.1`、setup-python `v7.0.0`、upload-artifact `v7.0.1`、
  download-artifact `v8.0.1`、configure-pages `v6.0.0`、upload-pages-artifact
  `v5.0.0`（composite 內 upload-artifact 已固定 node24）、deploy-pages `v5.0.1`。
  SHA 由 GitHub refs API 回讀；已核對各 `action.yml` runtime=node24 及現有 inputs
  （checkout ref／fetch-depth／persist-credentials、setup-python python-version／cache、
  artifact 路徑與 `if-no-files-found` 等）相容。舊 Node.js 20 SHA 加入測試負向清單。
- **Runner 固定 `ubuntu-24.04`**：5 個 workflow 全部 8 個 Linux job 由浮動
  `ubuntu-latest` 改為 `ubuntu-24.04`，避免 2026-10-19 起自動轉 Ubuntu 26；
  job 權限、environment、`if`／`needs`、concurrency 及部署語義不變。
- **BigGo smoke 有界化（只影響 smoke 路徑）**：smoke 改為單次 attempt、token／search
  每個網絡階段約 8 秒 timeout、唔採用 60／90 秒 retry／cooldown；首個 `unreachable`
  或例外立即 False（唔試其餘候選），只有明確 `no-price` 才試下一候選，首個有價即
  True。`--price-batch`／`--force-batch`／正常查詢維持原完整 retry（5 次）＋冷卻＋
  限速；`_get_access_token`／`_api_search` 新增參數向後兼容、預設行為不變。
  2026-09-23 daily run 35911295151 嘅失敗 smoke 曾耗時約 14 分鐘；有界化後 fail
  路徑設計上限約 16 秒（單候選兩階段各 8 秒）。
- **測試**：新增離線 mock 回歸（smoke 參數／呼叫次數／無 sleep 硬碰／例外唔洩漏
  secret；預設批次 retry 與 429 Retry-After 冷卻不變）；workflow 靜態測試加
  Node.js 20 SHA 負向清單、版本註釋對照、`ubuntu-24.04` YAML（parser）檢查。
  全部測試唔打真實 BigGo／EMSD／production。
- **Draft PR #14 E3**：head `2d17a9c99537652627abc53f6275e63d462b75dd` 的
  `pull-request-gates` run 35941034804 與 Pages `build` run 35941034788 均 success；
  實際執行 job 日誌中 Node.js 20 forced-runtime、`ubuntu-latest`／Ubuntu 26 migration、
  `punycode`／`DeprecationWarning` 均為 0，runner 回報 `Image: ubuntu-24.04`。
  PR 路徑的 `update`／`deploy` 及 production-only artifact／deploy steps 正確 skipped，
  所以正式 production 路徑仍須在 merge 後首次執行時觀察。


### 2026-09-24 production 驗收與自動鏈修復

- **PR #14 已合併並完成 production 驗收**：merge commit
  `5783f0d599469aa5ce3b912aa5d44f8454aebf5a`；唯一一次獲授權 full daily
  run 35942488710 success，產生 `b80a1d5`／`B20260924.106.1`；
  Pages run 35942793376 build／deploy success；手動 exact-ref GATE-08 run
  35943298448 全部 PASS。Node.js 20 與 Ubuntu 26 migration 警告為 0。
- **上游殘餘警告**：production `actions/deploy-pages@v5.0.1` 成功但輸出一次
  `[DEP0040] punycode`；已確認為 actions/deploy-pages#434／#413 的上游已知問題，
  不隱藏警告、不降級 action pin。
- **GATE-08 自動閉環**：把 `postdeploy-verify.yml` 改為 reusable＋manual，
  `pages-deploy.yml` 在 production deploy success 後以 exact build commit 直接呼叫；
  移除不可靠的跨 workflow `workflow_run` 鏈。PR 不 deploy／不 postdeploy，
  called workflow 維持 `contents: read`、master 祖先與 HEAD 精確核對。
  PR #15 merge 後 production Pages run 35944781623 已依序完成 build／deploy／GATE-08；
  完整 E4 全 PASS，證實自動鏈閉合。
- **BigGo 按需使用**：daily 非 force 路徑先讀本地 price batch state；inactive／已完成
  時零 BigGo API 請求，只有 active 批次或維護者明確 force 才執行 bounded smoke／批次。

### 2026-09-24 發布路徑 hotfix（PR #11–#13）與更新日誌同步

- **PR #11 `fix/stage-metadata-json`**（merge `aee7d53`）：`stage_artifacts.py` allowlist
  收返流水線生成嘅 `metadata.json`，令 daily 可以精確 stage 最終 metadata 而唔會 fail-closed。
- **PR #12 `fix/raw-receipt-no-private-objectid`**（merge `b57b413`）：公開
  `emsd_raw_receipt.json` 唔再寫私人 sink 嘅 `objectId`／asset 名（可能含 compact
  timestamp），公開收據只保留 hash／計數／adapter 非敏感事實；新增負向測試鎖定。
- **PR #13 `fix/release-archive-feature-report`**（merge `f546e2f`＝tag `v1.2.9` 指向）：
  GATE-09 歸檔前先執行 `feature-check.py --run-tests` 產生機器可讀報告，令
  Release run 35886358387 可以全綠。
- **文件同步（本輪）**：`空調對比報告.md`（＋重新生成 `index.html`）、`README.md`、
  `需求摘要.md` 更新日誌補回 2026-09-24 發布／production 驗收／GATE-08 自動閉環事實；
  本節只係記錄整理，唔改產品版本、唔改 `metadata.json`、唔重跑任何 production run。
- **當前上線 build 校正**：同日後續自動更新 run 35946820790（deployTime 2026-09-24T02:24:19Z）
  以 `0c19ac2` 產生 build `B20260924.110.1`；datasetDate 2026-09-24、1,834 登記／1,773 型號、
  `datasetHash` 均不變。各更新日誌「當前 live build」由 `B20260924.106.1` 校正為
  `B20260924.110.1`；`B20260924.106.1` 仍然係獲授權 workflow_dispatch daily 嘅歷史事實。

### 2026-09-24 發布後文檔同步、fail-closed 事件與「只追加」政策

- **PR #16 `codex/postdeploy-auto-evidence`**（merge `dd6065d`；內容 `8f92ea4`）：記錄
  自動部署後驗收實證。
- **PR #17 `codex/docs-current-status-clarification`**（merge `0c19ac2`；內容 `ae48e1c`）：
  更新 `空調對比報告.md` 更新日誌並重新生成 `index.html`。
- **fail-closed 事件（同 md／payload 未同步有關，唔係資料損壞）**：
  - `35946623750`（PR #17 merge push）、`35950593781`（`f64ec7e`）：build gate
    `releasePayloadHash` 唔一致——手動重生 `index.html`，但 `metadata.json` 仍係上一次
    流水線事實。
  - `35951000298`（`f4795ef`）、`35951377781`（`b2dd592`）：GATE-08
    `payload.pdf_matches_metadata` 唔一致——`index.html` 已回復一致，但 committed PDF
    內容仍係舊 `空調對比報告.md`。本地 A/B 重建：舊 md → `8f13a3c3…`（＝committed／線上），
    新 md → `d548e1d4…`。
  - 復原：下一次 scheduled daily 全量重生 `index.html`＋PDF＋metadata 後以
    `repository_dispatch` 部署（已觀察：`35946820790` → `35947112610` success）。
- **D24 約定**：純更新日誌／文檔改動唔手動重生 `index.html`／PDF，留返由下一次 daily
  重生；本輪同時把 `index.html` 回復到同 `metadata.json` 一致嘅版本
  （`payloadHash=sha256:b2de4e3f…`）。詳見 `docs/DECISIONS.md` D24、`AGENTS.md` 規則 9。
- **D25 文檔只追加政策（用戶指示）**：文檔只可追加、不可刪除或改寫既有記述；歷史快照保留，
  更新以新增節／新條目記錄並標明日期同證據；需求以 `需求摘要.md`（元文件）為準。
- **文件同步（全部以追加方式）**：`docs/README.md`（2026-09-24 狀態更新節）、
  `docs/GOVERNANCE_MATRIX.md`（§10）、`docs/STATUS.md`（§17）、`docs/adr/ADR-003`／
  `ADR-004`（狀態更新節）、`docs/PRIVATE_RAW_SINK_RUNBOOK.md`（§5）、`README.md`
  （資料日期口徑追加條目）、`需求摘要.md`（現況快照追加註＋更新日誌）。
- **觀察補充（append）**：pending 期間任何 push 都 GATE-08 紅——run `35952357474`（純文檔追加
  `5cb4a03`）同 `35952373427`（`04604b6`）都係 build／deploy success、GATE-08
  `payload.pdf_matches_metadata` failure，hash 同 `35951000298` 相同（online `8f13a3c3…`／
  rebuilt `d548e1d4…`）。故「docs-only ⇒ 零紅 run」只喺 pending 清零後成立。

## [1.2.9] - 2026-09-24

> 發布事實（2026-09-24 回讀）：tag `v1.2.9` → commit
> `f546e2fd52d961f489972a0732d114f22d4f7e68`；Release run 35886358387 success，非
> draft／prerelease；assets = `archive-v1.2.9.zip`／`CHECKSUMS.sha256`／
> `PROVENANCE.json`，24 個 CHECKSUMS 已獨立重算全通過。Release archive provenance
> 為初次 v1.2.9 build `B20260923.101.1`（archive commit `f546e2f`、source commit
> `b57b413`、workflow run 35881890400）；發布後首個 production daily run 35911295151
> 以 build `B20260923.103.1`、datasetDate 2026-09-24 上線。以下條目按撰寫日期保留
> 當時（2026-09-21 至 09-23）嘅證據狀態；「候選／未部署／E3、E4 未發生」等字句係
> 撰寫時事實，發布後由本段同 docs/STATUS.md §15 取代，不溯及改寫。

### 2026-09-23 平台治理

- **D3-A release 人工批准關卡**：使用者確認單人維護模式；已建立 GitHub `release`
  environment，以 `CalvinLau1012` 為唯一 required reviewer，`prevent_self_review=false`。
  GitHub API 回讀證實設定；本項沒有 merge、deploy、tag 或建立 Release。
- **D7-A daily raw sink 接線**：`daily-update.yml` 的 `抓取 EMSD + 新機偵測` step 接入
  `AIRCON_EMSD_RAW_REMOTE_REPO`／`AIRCON_EMSD_RAW_REMOTE_TOKEN`／`AIRCON_EMSD_RAW_REMOTE_TAG`／
  `AIRCON_EMSD_RAW_RETENTION_DAYS`（全部只由 `secrets.*` 提供；私人 repo 識別禁止入 repo／
  Variables／公開 log）；require 模式缺配置或上傳失敗維持 fail-closed。live 啟用仍待用戶
  選定 provider 並設定 Secrets；本項沒有建立 Release／上傳資產／merge／deploy。
- **合併前發布路徑核對（本地 E2）**：merge push 會先被 `build_pages_artifact.py` 的
  `metadata.version == models_data.VERSION`（1.2.8 vs 1.2.9）同 payload hash 檢查 fail-closed
  攔住，唔會用舊 metadata 部署；首個 daily 成功 push 後才以 `repository_dispatch` 帶精確
  commit／source run 進入 Pages production。完整順序與證據見 [docs/STATUS.md](docs/STATUS.md) §14。

> 此區分開兩類：(1) v1.2.9 本機候選修復——已實作並通過本機測試，但未建立 Release、未部署、生產 `metadata.json` 仍為 1.2.8；(2) 1.2.8 之後已入 master 但未另立產品版本的維護記錄。精確基準與證據見 [docs/STATUS.md](docs/STATUS.md)。

### 1.2.9 候選（未發布、未部署）— 2026-09-21

> 產品版本只由 `models_data.py` 的 `VERSION` 定義。以下修復已在本機完成並有 E2 測試證據；治理 PR／Code Owner 評審、受信任 CI 與部署後 E3／E4 仍未發生。

#### Added

- **EMSD 收據證據時序**：`fetch_emsd.write_receipt` 加可選 `csv_path`，成功收據在 CSV 原子寫入之後對實際 bytes 計 `datasetHash`；`retrievedAt` 由抓取迴圈收尾時間傳入（唔係寫收據當刻時間）；403/429、0 頁、壞表頭、新機偵測失敗、CSV 寫入失敗都寫失敗收據，唔改舊 CSV。
- **`gen-metadata.py` 收據事實**：新增 `receipt_facts` 驗證 success／aborted／頁數／來源（批准 EMSD https 路徑）／UTC Z／非未來時間／`datasetHash` 對 CSV bytes／CSV 15 欄與行數；`datasetDate` 由實際 `retrievedAt` 轉 UTC+8 香港日期；舊無 hash 收據明確失敗並要求重新成功抓取，唔可以補假時間。
- **部署後核對（GATE-08）**：新增 `scripts/postdeploy_check.py`——以指定發佈 commit 的 metadata／manifest 作 expected，完整 Draft 2020-12 + format 驗證 expected 與線上 metadata、兩者整個 JSON object 等值（唔再只比 8 個欄位），Web／PDF／CSV 可取得、CSV hash 同完整 `releasePayloadHash`（同一 framing 重算）、核心瀏覽器行為同 runtime Version／Last Update／Last Deploy（HKT）；cache bust + 有界重試，錯版本最終失敗；URL 只准官方 Pages／localhost；新增 `postdeploy-verify.yml`（接受 Pages 內建 workflow 嘅 `dynamic` 及 `push` 事件，只接本 repo master 成功部署，contents:read）。
- **長期歸檔（GATE-09）**：新增 `scripts/archive_release.py`（Web／PDF／CSV／metadata／manifest＋測試／部署後報告＋逐檔 CHECKSUMS＋PROVENANCE；`archiveCommit`（checkout）／`sourceCommit`（metadata.commit）／`deploymentCommit` 分開記錄；目錄＋zip 兩者必須一致先算 idempotent，缺一或唔同即拒絕 clobber；嚴格 SemVer tag）及手動 `release-archive.yml`（封裝前必先跑 GATE-08，含 `--payload-dir .`；預設只建歸檔；`publish=true` 且 protected environment 先建立 Release）。本輪未發布 Release。
- **Workflow Actions 固定**：`.github/workflows` 內所有 `actions/*` 固定到 GitHub refs API 核實嘅完整 commit（checkout `11d5960…`、setup-python `a26af69…`、upload-artifact `ea165f8…`、download-artifact `d3f86a1…`），符合治理 §9.3。
- **功能契約證據**：新增 `scripts/pytest_evidence_plugin.py`；`feature-check.py` 由檔案存在升級為 pytest 實際 collection node ids（假 node／拼錯參數阻斷）、靜態斷言檢查，`--run-tests` 收集 setup／call／teardown 同 skip／xfail／fail：required 綁定必須 passed、skip 即失敗；靜態檢查亦拒絕空斷言、只有常量斷言（`assert True`、`x = True; assert x`）及 try/except pass 吞例外嘅總是成功測試；報告寫 repo 外。
- **本地恢復演練**：公開 fixture-only `tests/test_restore_drill.py`（16 斷言）——checksum、schema／MAJOR 兼容性、staging→原子換入、篡改／truncated 失敗時 live good package 完好。

#### Changed

- **EMSD 每日抓取改為 fail-closed**：`daily-update.yml` 移除 `continue-on-error`；部署 metadata 的 `datasetDate`／`datasetRetrievedAt`／`datasetSourceUrl`／`datasetSnapshotId` 一律由成功、hash-bound `emsd_receipt.json` 產生；抓取失敗即中止，唔會用舊收據出新 metadata。重建模式用舊完整 hash-bound 收據時保留舊日期（由收據 retrievedAt 得出），唔會用生成時間改寫。
- **部署腳本（私人線，已移出公開 repo）**：EMSD 失敗中止部署；兩階段 metadata 用收據事實；core 驗證（`validate_metadata.py --core`）通過先出 PDF；官方批次改用 `run_official_batch.py`（有實際輸出證據先推進隊列）；BigGo smoke 失敗跳過保留快照（明確記錄未刷新），真批次失敗中止部署。
- **官網批次推進閘門**：新增 `scripts/run_official_batch.py`；六個 stage 1/2 fetch 腳本改為「任一實際嘗試目標失敗即非零退出」（`batch_failed`），失敗／輸出無效一律唔 `advance_queue`；失敗時只喺記憶體累積、只有全過才以原子替換寫快照，唔會用部分結果覆寫上次完整快照；明確 skip 嘅目標唔算失敗，冇目標時保留既有快照。
- **EMSD 資料交易式提交**：新機偵測改為 `plan_new_models`（純讀計畫）＋`commit_dataset`（CSV／new_models.json／update_queue.json 先寫 tmp 再一次過 replace，任何 replace 失敗即回滾已換入檔案）；三個檔任一寫入失敗都唔會留低半更新狀態，成功收據只在整組提交成功後寫。
- **BigGo 批次可見性**：「真失敗唔可以無條件吞成成功」——workflow 捕捉 return code，smoke 失敗如實報「未刷新、保留快照」，真失敗非零退出；force-batch 腳本內部先 smoke。
- **Metadata 完整驗證**：`validate_metadata.py` 改 Draft 2020-12 + FormatChecker（const／minimum／maxLength／allOf／rollback／真實日期／URI），`--core` 模式驗核心事實（fail-closed）；新增 `jsonschema[format]==4.26.0` 依賴。
- 文件口徑：README／需求摘要／報告／docs 同步為「修復已實作、候選未部署」；治理文檔及決策記錄更新版本記錄與 D16；README 版本記錄加 v1.2.9 候選列。

#### Fixed

- **頁數剛好 50 倍數唔再報錯頁數**：最後一頁 50 行後嘅空確認頁唔計入 `pagesFetched`，收據只報有數據嘅頁。
- **收據與 CSV 綁定**：成功收據永遠對已寫入 CSV 計 hash；新機偵測／CSV 寫入／收據寫入失敗都如實記錄，唔會有「舊收據配新 CSV」或「成功收據配失敗抓取」。
- **core 無效唔會出 PDF**：core 階段先按正式 Schema 約束（placeholder hash）驗證，workflow 再驗一次先生成 PDF；正式 Schema 仍然拒絕無 `releasePayloadHash` 嘅 core。
- **治理區塊提取錯誤可讀**：`--dump` 缺 ID／未知 ID 回退出碼 2 且無 traceback；內嵌 Schema 自身、Registry 與 Success Criteria 完整驗證。
- **GATE-08 workflow 不會再被跳過**：先前只收 `workflow_run.event == 'push'`，但 Pages 內建 workflow 實際 event 係 `dynamic`；已接受 `dynamic` 並保留 `push` 兼容，同時保留成功／master／同 repo 安全條件。
- **GATE-09 歸檔修正**：`_collect_files` 以前錯誤由 `artifacts_dir/reports/...` 揀報告檔（真實來源喺 `reports_dir`），令 `$RUNNER_TEMP/reports` 必定報缺檔；已修正。歸檔亦同時保護目錄＋zip，缺一或唔一致會明確失敗，唔會靜默重寫。
- **EMSD sidecar 半更新**：舊 `detect_new_models` 喺 CSV 寫入前已改 `new_models.json`／`update_queue.json`；CSV 失敗時 sidecar 已前進。現改為交易式提交＋故障時回滾，故障注入測試確認三份檔案 bytes 不變。
- **空斷言／總是成功測試**：舊 AST 只要見到 `assert` 就接受；現拒絕空斷言、常量斷言、`x = True; assert x`、吞例外後常量斷言等。

#### 2026-09-22 第二輪返修（同一 1.2.9 候選）

- **CI／信任**：Chromium 只裝一次且喺第一次 pytest 前；daily 改精確 allowlist
  `scripts/stage_artifacts.py`（allowlist 以外改動 fail-closed）、privacy `--mode index`、
  push fail-closed（失敗即要求新 run 重新建包，不再 `pull --rebase`）；build 加 run attempt；
  手動 dispatch 只限 master；三個 workflow Actions 固定完整 commit。
- **GATE-08**：`workflow_run` 接受 Pages 內建 `dynamic`（唔再被跳過）；postdeploy 完整
  object＋Schema 一致才結束重試、報告寫入失敗非零、expected 無效即停 request、PDF 必須同
  expected metadata 重建逐 bytes 一致；remote 核對仍屬候選（E4 UNKNOWN）。
- **GATE-09**：歸檔重 hash 實際 bytes／CHECKSUMS／zip（重複 entry、多餘、缺漏、損壞都拒），
  provenance 只忽略 `archivedAt`，tag 必須等於 metadata.version，release 等級要求
  feature-check／postdeploy／junit 有效報告，打包前私隱掃描；目錄＋zip 缺一即失敗。
- **官網批次**：六個腳本任一實際目標失敗即非零、失敗唔以部分結果覆寫快照；空白／登入／
  錯誤頁（冇有效 evidence）當失敗；wrapper 只接受 dict/list 且每 entry 有實質 evidence。
- **BigGo**：有 net_err 唔推進批次 idx（聽日重試同一 slice）；網絡錯誤永不當 clean miss；
  只喺完整零錯誤 slice 才 advance＋黑名單復核。
- **EMSD**：CSV＋`new_models.json`＋`update_queue.json` 改交易式提交（crash journal、
  回滾失敗保留 journal 唔聲稱原狀）；sidecar／queue 壞 JSON 不再默默重置；`advance_queue`
  fail-closed；生產 metadata 必須成功 hash-bound 收據、finalize 驗 CSV hash／rawCount。
- **Feature Check**：subprocess 非零、collectionErrors、未完成 session、缺 setup/call/teardown、
  skip／XPASS／deselect、空 selection、報告寫入失敗全部 fail-closed；APPROVED_SKIP 只逐節點
  豁免 skip；靜態拒絕常量斷言／吞例外。
- **資料契約**：`validate_data.py` 加逐行 15 欄、必填、數值、能源級別、sentinel、JSON entry
  型別；`extract_governance.py` 拒 NaN／Infinity／未知 marker。
- **PDF**：invalid／missing metadata 不可出 production PDF；同輸入重建 byte-for-byte；
  表格截斷標明省略號；HTML 同輸入重建一致；`requirements.txt` 固定 `reportlab==5.0.0`
  令重建一致性可跨 CI run 維持。
- **公開／私人線分離**：`docker/`、`release/` 及私人文件段落移出公開工作樹，完整 bytes＋
  `SHA256SUMS`＋還原指引存於私人包；公開新增 fixture-only `tests/test_restore_drill.py`
  維持 SC-009。Git 歷史仍含私人線，待人類私隱評估。
- **治理候選**：新增 `docs/GOVERNANCE_MATRIX.md`、`docs/adr/ADR-001-raw-emsd-snapshot.md`；
  8 個 required 功能新增 testBindings（未降級 protection／Schema／門禁）。

#### 2026-09-22 綜合返修（queue／price／staging／privacy／acceptance／official receipt）

- **queue 單一契約**：新增 `queue_utils`（missing→預設；壞檔／bool／超範圍 stage／
  非字串／空白／canonical 重複 model 全部 reject；stage 0 必須空 models、1/2 必須非空）；
  `fetch_emsd`／`advance_queue`／`run_official_batch`／workflow 四個入口共用；
  workflow 唔再 `2>/dev/null || echo 0`。
- **price 進度**：`advance_queue` 2→0 改為先啟動 price meta（True／False 皆成功語義）
  後清 queue；啟動失敗保留 stage 2；`batch_utils` meta load fail-closed、save 原子＋
  fsync；`price_batch_state.py` 區分 active／inactive／corrupt（corrupt 阻斷）。
- **staging**：`stage_artifacts.py` 拒絕任何 deletion／rename、manifest 用
  `gen-metadata` 路徑契約、stage 前驗 index staged paths、失敗零部分 stage、dry-run 不改 index。
- **privacy gate**：`tracked`=HEAD blobs、`index`=staged blobs、`worktree`=tracked+untracked、
  `tree`=目錄 bytes；cat-file 嚴格解析、unmerged／重複 path／invalid UTF-8 fail-closed；
  PDF 等 binary 只列證據上限。
- **驗收機器證據**：新增 `scripts/run_acceptance.py`（gate ID／exact argv／UTC／真實 rc／
  log sha256＋JUnit，唔經 pipe）；`archive_release` release 等級要求 acceptance gates
  exactly once／rc=0／argv allowlist／log hash，feature／postdeploy 做結構驗證，拒任意文字。
- **官網批次**：每腳本 machine receipt（attempted／succeeded／failed／alreadyVerified／
  covers）＋output before/after hash＋queue hash；zero-attempt 需 alreadyVerified＋evidence；
  queue model 未覆蓋即 fail-closed 保留（ADR-002 待人類決定 A／B／C）。
- **測試**：新增 queue／price／staging／privacy／acceptance／wrapper 負向測試；移除
  永遠通過斷言；本機全套 368 passed。

#### 2026-09-22 第五輪返修（parser／path trust／receipt 綁定）

- `stage_artifacts`：修正 `--name-status -z` NUL parser（M/A/D/T/R/C；truncated／未知／
  重複／invalid UTF-8 拒）；index snapshot＋失敗原子恢復。
- `archive_release`：`safe_report_path` 拒 traversal／absolute／backslash／symlink／NUL；
  required report exactly-one；acceptance verifies schemaVersion／runner／ok／commit／
  gates set／timestamps。
- `run_official_batch`：strict marker schema（counts／lists／covers=union）；所有 listed
  model 必須有 output evidence；ready receipt 先寫再 advance；final 更新失敗如實報。
- `run_acceptance`：移除任意 `--spec`；gate ID 安全 unique；未知 `--only` 非零；machine
  evidence 必須 repo 外。
- `batch_utils`：日期／月份／deploy 時間用真實日曆驗證；`detect_mode` 唔再吞錯。
- 本機全套 399 passed；詳細負向證據見 docs/STATUS §9。

#### 2026-09-22 第六輪：D1-B／D2-A／D4-A／D7-A／D8-A（同一 1.2.9 候選）

- **D1-B coverage pending**：`run_official_batch.py` 分開硬失敗與純 coverage 缺口；
  後者可寫 `decision=queue-kept-pending-coverage`、原樣保留 queue stage／models 並返回
  成功 class；`publish_official_status.py` 投影公開 status，`generate_html.py` build 時顯示
  「官網規格待核」；硬失敗（壞 receipt、failed>0、hash/queue race、腳本非零、輸出無效）
  維持非零阻斷。ADR-002 由提案改為 D1-B 已批准、已實作候選。
- **D2-A Pages Actions 部署**：新增 `pages-deploy.yml`（PR 只 build／跑 7 gates、永不 deploy；
  master push deploy job `needs` build、`environment: github-pages`、`pages:write`＋
  `id-token:write`）；新增 `build_pages_artifact.py`，artifact 只可含 manifest 公開檔案，
  拒 symlink／缺檔／額外私人檔／traversal；`postdeploy-verify.yml` 改綁新 workflow 嘅
  `head_sha`，不再接受舊 Pages `dynamic`／latest master fallback；所有新 Actions 固定
  refs API 核實完整 commit。新 Pages workflow 未存在於 default branch 前，`daily-update.yml`
  另加唯讀 `pull-request-gates` job（只 same-repo PR、contents:read、exact head SHA、不 deploy），
  確保呢個 PR 有 trusted CI checks；merge 後由 `pages-deploy.yml` 接手。
- **D4-A 全歷史秘密審計**：新增 `check_public_history.py`（reachable refs blob 掃描，
  credential 必須 0；self-host path 只列 residual risk，報告不寫 secret 原文）及負向測試；
  Pages workflow 加 `fetch-depth: 0`＋history audit gate。實跑 208 commits／934 blobs：
  credentialFindings=0、selfHostFindings 分類列出。
- **D7-A 原始 EMSD bytes**：`fetch_emsd.py` 保存逐頁實際 HTTP bytes（page／length／sha256／
  Last-Modified／ETag），整批成功＋資料提交後原子寫公開 `emsd_raw_receipt.json`（只含 hash；
  與 CSV datasetHash 綁定，成功 `emsd_receipt.json` 回寫 `rawReceiptHash`）；私人 sink
  介面寫 repo 外目錄、require 失敗即阻斷，90 日 retention 邊界已測；raw HTML／archive
  永不出現在公開 worktree／artifact。真正 private sink Secret／live snapshot 屬 merge 後平台驗收。
- **D8-A 72h 新鮮度**：新增 `check_freshness.py`（age > 72h 才 stale；71:59:59 同 72:00:00
  pass）及 `freshness_issue.py`／`freshness-monitor.yml`；6 小時排程、同一狀態 noop、
  狀態改變才 update、恢復 close；missing／invalid／future timestamp 硬失敗，線上 metadata／
  payload hash 由 postdeploy_check 先驗。
- **D5-A 私人 repo**：私人 repo 已建立並 API 回讀
  `visibility=PRIVATE`；來源 Temp 包 `SHA256SUMS` 先驗，再 push 後 fresh clone 逐檔
  重算核對通過（LF bytes 以 `.gitattributes * -text` 固定）；Temp 原件保留未刪。
- **D3 當時 pending／D6 deferred**：2026-09-22 快照中 `release` environment reviewer
  仍待選；D3 已於 2026-09-23 以 D3-A 解決（見上方平台治理記錄）。公開 PR merge
  後才做私人 self-host 線同步，標記 `DEFERRED BY D6-A`。
- **本輪驗證實數**：全套 pytest **433 passed／1 skipped**（Windows 平台 symlink skip）；note §4.6 focused **69 passed／1 skipped**；feature-check `--run-tests` **18 節點 passed**；machine acceptance 7 gates rc=0（`ok=true`）；history audit 208 commits／934 blobs、credentialFindings=0、selfHostFindings=32。
- **文件**：ADR-001／ADR-002 更新為已批准及實作候選；DECISIONS 新增 D17；GOVERNANCE_MATRIX、
  STATUS、README、需求摘要、報告及治理版本記錄同步；未虛構 E3／E4。

### 1.2.8 之後已部署維護記錄（未另立產品版本）

> 以下改動已入 master（部分已由 Pages 部署），當時沿用 v1.2.8、未另立產品版本；按實際日期／提交保留，唔補虛構發布日期。

- 同步 README／網站來源與日期說明、需求摘要及報告；修正登記與型號計數口徑、排程延遲及離線 metadata 限制。
- 新增 docs 導覽及狀態證據表，修正 D10／D12 過時狀態；黑名單遷移數據標為歷史記錄。
- 治理文檔候選版本 3.1.2：只校正文檔說明；當時 Registry、Schema、成功標準及門禁未變（其後 2026-09-22 嘅 binding 候選更新見上）。治理評審仍待完成。

#### Added

- `pytest.ini`：`python -m pytest tests/` 預設收集 `tests/browser_smoke.py`（12 項瀏覽器 E2 證據）
- 瀏覽器回歸測試：價位邊界、Escape／焦點、tooltip 溢出、明暗對比、metadata 小數秒與載入失敗
- `tests/test_energy_distribution.py`（8 項）：1–5 次序、核心 29 靜態表防漂移、動態全量分佈來源／總和、PDF 展開動態區塊
- `tests/test_biggo_smoke.py`（8 項）：smoke 候選本地證據、首個成功只用一次、no-price fallback、全失敗、例外唔洩漏 secret
- 自建伺服器持久發佈工具（D15）及相關 sandbox／runbook 已於 2026-09-22 移離公開
  repo；完整 bytes 與歷史記錄暫存於 repo 外可恢復副本（未係長期私人儲存；commit／push 前須轉移＋核驗），公開候選唔再包含部署細節。
- **公開 repo 私隱 gate**：`scripts/check_public_privacy.py`（掃描 tracked HEAD 禁止個人／自建環境識別資料；只列規則 ID／檔名，不打印命中內容）＋ `tests/test_public_privacy.py`（合成樣本命中、通用示例值放行、repo HEAD 自掃 0 命中）
- **Runtime 資料準備**：`scripts/prepare_runtime_data.py`（黑名單 canonical 遷移守衛，只跑一次）＋ `tests/test_prepare_runtime_data.py`（8 項）

#### Changed

- **報告當前狀態數字**：`generate_html.py` 建置時同步報告內文數字；`tests/test_dynamic_counts.py` 加守衛

- 治理改善方案 M1（PR-1／PR-2／PR-3，本地分支；決策 D11-D13）：
  - **Canonical 型號鍵**：`crawl_utils.canonical_brand()`（品牌跨平台矯正）+ `canonical_model_key()`（`BRAND|NORM`）；黑名單、model_status、protected set、filter_active、record_results、revive_model 全線統一（D11）
  - **生命週期三態**：`run_price_batch()` 分開有價／乾淨無報價／網絡錯誤（網絡錯誤唔計淘汰，D8）；正常批次照 call `record_results`（batch_id 防同批重跑重複計 miss）；每批次日小額黑名單復核 quota 40（有價自動復活）；並發 3 → 2（回歸 D3）
  - **EMSD ingestion**：每頁表頭按 signature 排除（唔再靠 p==1）；`emsd_receipt.json` 記錄 pagesExpected/pagesFetched/每頁行數/終止原因；中途網絡錯誤即使累積超過下限都唔覆寫 CSV；保留全部 registration + canonical product view（D12）
  - **Metadata Schema**：新增 optional `rawRecordCount`／`registrationCount`／`modelCount`（治理文檔 v3.1.1）
  - **PDF 可重現**：`build_pdf(output_path=...)` 加輸出參數、固定 CreationDate/ModDate/ID，同輸入兩次 build byte-for-byte 相同；`test_pdf_export` 改用 tmp_path（唔再污染受追蹤 PDF），並加測試前後 repo PDF hash 不變斷言
- 比較面板加 `role="dialog"`；搜尋與下拉選單加 `aria-label`
- 比較工具列、頁腳、hero 統計、`--primary2` 等顏色調整至 WCAG AA（≥4.5:1）
- **CI 兩階段 metadata 封裝（D14）**：新增 `--stage core|finalize`；core 出同 run 核心事實（無 hash）→ PDF 用 core → PDF 完成後以 `deploy_payload.json` 明確 manifest finalize `releasePayloadHash`；hash 範圍唔再係 `--payload-dir .`
- **動態型號數字**：Hero／Open Graph／meta description 用建置時實際 `__TOTAL_MODELS__`／`__EMSD_REGISTRATIONS__`；歷史數字（1,927／1,854）只保留喺明確標示歷史嘅文檔段落
- `tests/browser_smoke.py` 移除 `pytest.importorskip`（required browser smoke 唔可以靜靜 skip）；`requirements-dev.txt` 加 `playwright>=1.40`
- **能源分佈資訊架構**：核心 29 表明確標示且固定 1→2→3→4→5 次序（0 都顯示）；新增 `<!-- AIRCON:DYNAMIC:ENERGY_DISTRIBUTION -->` 於 build 時由實際快照動態生成全量分佈（canonical model 同 registration 分兩欄），HTML 同 PDF 一齊展開
- **BigGo smoke 多候選**：`SMOKE_CANDIDATES` 集中管理 3 個跨品牌核心 29／受保護型號，依序探測、首個有價即通過；抽出 `_extract_price` 做共用過濾；`no-price`（個別型號無匹配）同 `unreachable`（網絡／限流／認證粗分類）訊息分開
- **文件透明化**：README／空調對比報告.md／需求摘要.md／AGENTS.md 加 AI 協作角色說明（Codex／DeepSeek `deepseek-flash` via Pi `ds-exec`／人類維護者）；AGENTS.md 修正唔存在嘅命令（`--full-scan`／`--blacklist` → `--force-batch`／`model_lifecycle.py`）
- `.gitignore` 加 `.agents/`：ds-exec 工作記錄同整合 worktree 唔係產品內容，防止主工作區 `git add -A` 誤提交

#### Fixed

- **公開 repo 私隱**：移除／泛化自建環境識別資料（文件、私人部署工具與模板已移出公開 repo）；新增 `scripts/check_public_privacy.py` CI gate 及回歸測試，防止再次寫入私人 host／IP／路徑／build id
- **GitHub Pages hash 鏈**：`fetch_emsd.py` 寫 CSV 改用 `lineterminator='\n'`（原生 LF），令 worktree bytes == git index bytes == 發佈 bytes；之前 CRLF 工作樹經 `.gitattributes eol=lf` 正規化後，線上 `datasetHash`／`releasePayloadHash` 同實際 bytes 唔一致
- 新增 CI 防線 `scripts/check_payload_bytes_vs_index.py`（workflow 在 `git add -A` 之後、commit 之前阻斷任何 worktree/index bytes 不一致）
- 新增回歸測試：`tests/test_emsd_csv_lf.py`（實走 `fetch_emsd.write_csv` 斷言 LF-only + loader 可讀 + 已入庫 CSV 與 metadata.datasetHash 自洽）、`tests/test_payload_bytes_vs_index.py`（LF 通過、CRLF／未 stage／缺檔阻斷）
- 伺服器 preflight 嘅 `grep -q` + `pipefail` SIGPIPE 誤判（改為先列 tarball 清單再檢查）
- 同秒重建時備份目錄碰撞（唯一後綴；pre-image tag 跟備份名）
- 回滾時「部署前不存在」嘅檔案（PDF／CSV／receipt）冇被刪除（完整 volume 還原 + 檔案清單 + hash 比對）
- 報告內文硬編當前狀態數字（建置時動態同步；歷史更新日誌保持不變）
- 舊 volume EMSD CSV 每頁重複表頭（release 用 PR-3 ingestion 重抓；`validate_data` 阻斷）
- 停售標示失效：黑名單 canonical key 匹配修正後，頁面停售型號 289 → 1,075（黑名單 1,095 keys 中 1,079 個可解析品牌；4 個唔喺現行頁面資料）
- 保護型號失效：`protected_models()` 之前誤將 MODELS dict 整個正規化，核心 29 保護形同虛設；現改為 canonical key 集合
- EMSD CSV 混入 37 行重複表頭（已清理；1,863 筆登記 / 1,814 型號）
- 價位篩選「5以上」錯誤包含未知價型號（`core.filter`）
- 平板寬度 721–999px 導覽 tooltip 撐出頁面水平滾動（`ui.responsive`）
- 比較面板唔支援 Escape 關閉、開啟／關閉焦點唔跟隨、缺 `role`/`aria-expanded`（`ui.comparison-modal`）
- 深色模式頁腳文字對比 1.85:1；淺色模式連結／按鈕／hero 統計／最佳值標示對比不足
- 頁腳版本內嵌 `models_data.VERSION` 靜態常量（`operations.version-display`，改為只讀 `metadata.json.version`）
- Windows 本地生成 HTML 用 CRLF，與 CI／已入庫 LF 唔一致（可重現建置）
- 「只顯示已選」之下反選後，型號仍留在列表（`core.filter`）
- `AGENTS.md` 與 `validate_metadata.py` 用法示例檔名（`validate-metadata.py` 不存在，以 CI 實際命令 `validate_metadata.py` 為準）
- `validate_metadata.py` 之前拒收 RFC 3339 小數秒（`2026-09-02T19:28:14.500Z`）；內嵌 Schema `format: date-time` 本身容許，已對齊（非 UTC `Z` 或格式錯照樣拒）
- 能源分析表舊次序 `1,3,4,2,5` → 固定 `1,2,3,4,5`，2／5 級 0 都顯示；「定頻最高只有 3 級」限定為核心 29 語境，唔再同全量 1–5 級資料混淆
- BigGo smoke 單一硬編 `RA-10RF`：個別型號停售／一時無價會誤判整個 API 失敗 → 多候選 fallback，安全門禁（smoke 不過即跳過批次）不變

#### Data

- `model_blacklist.json` 1,095 個 key 遷移為 canonical（matched 1,079、orphan 16；遷移報告 `docs/blacklist-migration-2026-09.md`）
- `model_status.json` tracking key 一併遷移
- `emsd_空調能源標籤.csv` 移除重複表頭（1,900 → 1,863 筆登記）
- README／需求摘要／報告計數同步實際快照（1,814 型號 · 1,809 有價 · 1,863 筆登記，截至 2026-09-03）
- metadata `recordCount` 按 D12 改為唯一型號數（1,814）；CI 另傳 optional `rawRecordCount`（1,863）／`registrationCount`（1,863）／`modelCount`（1,814）
- README 狀態分佈圖同步實際頁面：有價 674 / 停售 1,075 / 官方價 65（合共 1,814；無價 0）
- README 能源級別圖同步全量 canonical model（1,127／166／168／348／5，合共 1,814），並列 registration（1,172／166／172／348／5，合共 1,863）同核心 29 對照

#### Security

- 無改動（BigGo 憑證仍只存 GitHub Secrets）

### PR #10 審查返修（2026-09-23；撰寫時未發布、未部署）

> PR #10（v1.2.9 治理候選）審查發現 9 項缺陷；以下修復全部本機完成、commit 前 E2 證據見
> [docs/STATUS.md](docs/STATUS.md) §11。產品版本仍由 `models_data.py` 的 `VERSION` 決定；
> production `metadata.json` 仍為 1.2.8，未部署、未發布 Release。

#### Added

- **Pages deployment envelope**：新增 `deploy_envelope.json`；`build_pages_artifact.py`
  同時輸出 payload + 最終 `metadata.json`（+ 一致公開 sidecar），封包前驗完整 Schema、
  `version == models_data.VERSION`、payload hash、CSV hash 同 counts，錯配 fail-closed。
- **PR 隔離 fixture 封包**：新增 `scripts/make_fixture_release.py`；PR gate 喺 repo 外用
  fixture metadata 真生成 PDF、重建 envelope，再以 `verify_candidate.py` 做 loopback HTTP、
  payload hash、run identity、PDF 重建同 Chromium runtime 驗證，永不部署。
- **daily→Pages 精確銜接**：新增 `scripts/dispatch_pages_deploy.py`（`repository_dispatch`
  帶已 push commit＋sourceRunId）同 `scripts/verify_deploy_request.py`（成功 run／master／
  祖先綁定；唔 fallback 最新 master）。R7 再收緊：有界 polling 等 source run completed/
  success、精確比對 run attempt、workflow path 必須係 daily-update.yml、部署 commit 必須
  單親直接 child、checkout `metadata.json` 要 binding `workflowRunId`／`commit`。
- **可插拔私人 raw sink**：新增 `scripts/private_raw_sink.py`——`local-dir` 如實標示非
  durable，`github-release-asset` 候選 adapter（PRIVATE 回讀、同名拒覆蓋、上傳後下載
  sha256＋size 核驗、90 日 retention、失敗唔發成功 receipt）；`docs/PRIVATE_RAW_SINK_RUNBOOK.md`
  列明 live 啟用前置；`docs/adr/ADR-004`。
- **測試**：真 HTTP＋瀏覽器＋PDF 重建 E2E、fake GitHub API dispatch／verify、remote sink
  fake HTTP、symlink／junction 無 skip 拒絕測試。

#### Changed

- **R7 base 同步**：候選 branch merge `origin/master` `be43b7c`（2026-09-22 自動更新）；
  `metadata.json`／EMSD 收據／`new_models.json`／`update_queue.json` 以 master 流水線事實為準，
  冇手改 production metadata；`index.html`／`空調對比報告.pdf` 用合併後程式同已提交資料重建。
- **freshness monitor combined health**：freshness 同 postdeploy 結果合併判斷；
  fingerprint 唔含 `ageSeconds`（同一 stale 6 小時後仍 noop）；分類改變才 update、
  完全恢復才 close；report 缺失／非 object／network／API 錯誤非零且脫敏。
- **concurrency 隔離**：daily 同 Pages 按 event／ref 分組，PR 再唔可以取消或阻塞生產 run。
- **archive sidecar**：`archive_release.py` 收錄 `deploy_envelope.json` 同同本次一致的公開
  receipt／status；舊／錯配 raw receipt 唔會歸檔當成本次證據。
- **postdeploy-verify**：接受 Pages workflow 嘅 push／workflow_dispatch／
  repository_dispatch 成功 run；checkout 後再驗 HEAD == 平台記錄而且係 master 祖先。

#### Fixed

- Pages artifact 唔再漏 `metadata.json`；輸出目錄唔再被任意 rmtree；PR 唔會用 production
  metadata 驗候選；`workflow_dispatch` 只限 master 先入 production；Windows symlink 測試
  唔再以 skip 當 pass。
- **R7**：Pages production concurrency 加 `queue: max`（預設 single 會以新 pending 取代舊
  pending，可能犧牲已驗證部署）；`verify_deploy_request.py` 補 run attempt／workflow path／
  單親 direct parent／metadata binding／完成時序負向測試（時間可注入，不在測試真等）。

## [1.2.8] - 2026-08-26

### Added

- 治理落地：`docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源，內嵌六個規範區塊）
- 決策記錄 `docs/DECISIONS.md`（人類決策項與技術轉向，D1-D10）
- PDF 報告導出（`generate_pdf.py`，reportlab 內置中文字體）
- `scripts/extract_governance.py` / `scripts/feature-check.py` / `scripts/gen-metadata.py` / `scripts/validate_metadata.py`
- CI 門禁 GATE-01/03/05/06（Block 級）+ 頁面 runtime fetch `metadata.json`
- BigGo 官方認證（client credentials → access_token，55 分鐘快取；免登入通道關閉後轉向，D10）
- README 數據統計章節（狀態/機型/能源/類型/匹數/價位/品牌分佈 mermaid 圖）+ 治理架構一覽與 DevOps 目標清單
- 治理文檔四類架構圖（整體架構/AI 流程/AI Agent 權限/DevOps 流水線）

### Changed

- BigGo 價源：網頁 scrape → 官方 JSON API（`api.biggo.com`，D1）
- 說明文檔全面改為書面語（README/需求摘要/要求/報告/AGENTS/CONTRIBUTING/copilot-instructions）
- 決策 D2：日常價錢更新全自動；啟動初期一次性數據可本地處理後上傳（D9）
- `fetch_biggo.py`：全局冷卻 + 最小請求間隔 + 降並發（D3）

### Fixed

- 比較器「狀態」（有價/官方價/無價/停售）與「價位」篩選恢復（v1.2.5 功能於皮膚重構時遺失）
- 治理 marker 前綴、metadata pattern、subprocess 編碼等多個 CI 問題

### Data

- BigGo 有價型號 731 → 742（本地全量復核，零網絡錯誤）
- 淘汰黑名單 1,103 → 1,095（復活 8 個重有市售報價型號）

## [1.2.7] - 2026-08-25

### Changed

- 皮膚/深色模式全面恢復（Blue Fantasy 壁紙 + whale-girl 吉祥物 + 元件級對比度）
- 響應式適配修正（手機下拉溢出/吉祥物重疊，8 裝置 × 深淺色 16 組合測試）

### Fixed

- `SPECS_OVERRIDE` 9 個重複 key（P0）

## [1.2.6] - 2026-08-25

### Changed

- 代碼重構：抽離 `models_data.py`、`batch_utils.py`；`fetch_*` 統一 `crawl_utils.fetch`
- 深色模式對比度修正（目錄/表格 hover/code/引用/卡片/按鈕）
- 網頁 hero 顯示成功更新時間（`last_deploy` 香港時間）

### Fixed

- `generate_html.py` 黑名單／價格 JSON 只讀一次（生成效能）
- `fetch_emsd.py` 安全閘門（抓不齊不覆寫）+ 原子寫入

## [1.2.5] - 2026-08-18

### Changed

- 停售/官方價/有價/無價狀態標籤 + 價位標籤；黑名單以「停售」展示

## [1.2.4] - 2026-08-18

### Data

- 本地全量 BigGo 搜索：有價 734；第一版淘汰黑名單 1,103 個

## [1.2.3] - 2026-08-18

### Added

- BigGo 線上 smoke 防護；型號淘汰黑名單機制（`model_lifecycle.py` + `model_blacklist.json`）

## [1.2.2] - 2026-08-16

### Added

- 每日檢查打卡（`last_check`）、BigGo 驗證閘門、`price_utils.py` 重用工具

## [1.2.1] - 2026-08-16

### Changed

- 主力價錢源確定為 BigGo 官方 JSON API；PricesAPI 改核心 29 驗收/後備

## [1.2.0] - 2026-08-16

### Changed

- 主力價錢源改用 PricesAPI（後再調整）；BigGo/Price 舊快照後備

## [1.1.1] - 2026-08-16

### Fixed

- TOSOT/Gree 規格被重複 dict key 覆蓋丟失
- 代碼重構（crawl_utils 共用 + 單元測試 + XSS 加固 + 版本號單一來源）

## [1.1.0] - 2026-08-16

### Added

- BigGo 官方 JSON API 全量實抓（731 型號）+ 深海女仆主題 UI

## [1.0.0] - 2026-08-15

### Added

- 正式版：全量 1,854 型號 + 官網核實 220 + 互動比較器 + 論壇討論精華 + GitHub Pages

## [0.2.0] - 2026-08-12

### Data

- EMSD 全量 1,927 型號核實（能源/雪種/耗電）

## [0.1.0] - 2026-08-11

### Added

- 報告初版（29 型號統合對比）
