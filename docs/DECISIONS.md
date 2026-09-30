# 決策記錄（Decisions）

> 本文件記錄項目發展過程中的重要決策，包括人類決策項（由項目負責人決定）與因技術限制導致的方案轉向。
> 每項決策均註明背景、選項、決策結果與原因，供日後追溯。
> 更新機制見 [治理源](AIRCON_COMPARE_GOVERNANCE.md)。
> 記錄核對：2026-09-21；目前證據與限制見 [STATUS.md](STATUS.md)。決策日期及當時測試數保留，不代表今日重新驗證。

---

## D1 · BigGo 價錢源策略：網頁抓取 → 官方 JSON API（技術限制轉向）

- **日期**：2026-08-26
- **狀態**：已實行（取代「純快照」方案）
- **背景**：BigGo 網頁版（biggo.hk）對 GitHub Actions 的 IP 段（AWS 共享 IP）持續返回限流（429/403），價錢批次無法在線上自動執行。
- **選項**：
  - A：保留本地手動批次（用戶本地執行後推送結果）
  - B：純快照模式（停止線上價錢抓取，價錢維持現有快照）
  - C：改用 BigGo 官方 JSON API（`api.biggo.com`）
- **決策**：用戶先批准 B（純快照），並要求尋找免費 MCP 方案；調查後發現 BigGo 官方 JSON API（與網頁版為不同主機、當時 product search 免認證），經本地實測（HTTP 200）與 CI smoke 驗證後，轉向 C。
- **原因**：用戶明確要求價錢更新必須全自動（不接受本地手動處理）；官方 API 經實證對 GitHub IP 友好。
- **後果**：價錢批次恢復全自動；同日稍後免登入通道被關閉（見 D10），改用官方免費認證延續此方案。

## D2 · 價錢批次執行位置：日常必須在 GitHub Actions 全自動執行（人類決策）

- **日期**：2026-08-26
- **狀態**：已實行
- **背景**：BigGo 網頁版限流期間，曾提出「用戶本地執行批次後推送結果」的過渡方案。
- **決策**：
  1. **日常價錢更新**：否決本地批次方案；必須由 GitHub Actions 全自動完成，不接受用戶日常介入。
  2. **啟動初期例外**：項目啟動初期的一次性數據（如首輪全量停售驗證、黑名單確認）可由用戶在本地處理後直接上傳，此類一次性數據只需在更新後驗證並同步上線。
- **原因**：用戶明確表示不希望項目平日需要其手動干預；但啟動初期的一次性數據輸入可以在本地進行。
- **備註**：首輪建立的 `model_blacklist.json`（當時 1,103 個型號，非現時數量）即屬此類一次性本地驗證數據，見 D9。

## D3 · BigGo API 大批量限流應對：自適應降速（技術限制應對）

- **日期**：2026-08-26
- **狀態**：已實行
- **背景**：一次性強行全量批次（228 個型號）在 CI 中執行至約 60 個請求後，出現連續 40 個網絡錯誤，判斷為 API 對大批量請求的批次級限流（smoke 單一請求成功）。
- **決策**：
  1. 遇 HTTP 429 即全局冷卻 60 秒以上（尊重 Retry-After），批次內所有 worker 同步等待；
  2. 加入全局最小請求間隔 2.5 秒（主動限速，避免觸發上限）；
  3. 並發數由 3 降至 2；
  4. 連續失敗 12 個即全局冷卻 90 秒後再繼續（不立即中止）；
  5. 中止時即時取消未完成請求（避免等待所有 pending 線程）。
- **原因**：單次請求成功但大批量觸發限流，需以自適應降速取代硬碰；同時保持「連續 40 個錯誤才中止」的安全閘門。

## D4 · PDF 報告導出（人類決策）

- **日期**：2026-08-26
- **狀態**：已實行
- **決策**：實現 PDF 報告導出功能（`generate_pdf.py`），並列入功能註冊表 required 項（`report.pdf-export`），與網頁版共用同一 metadata 規則。
- **原因**：用戶決定實現；滿足離線閱讀需求。
- **技術**：reportlab + 內置 STSong-Light 中文字體（零外置字體依賴）。

## D5 · ranking／recommendation 測試策略（人類決策）

- **日期**：2026-08-26
- **狀態**：已實行
- **決策**：為 ranking 與 recommendation 功能編寫輕量測試（驗證輸出結構與基本合理性），不引入重型評測框架。
- **原因**：用戶在「輕量測試／深度測試」選項中選擇輕量測試。

## D6 · 部署事實單一來源（治理決策）

- **日期**：2026-08-26
- **狀態**：已實行（治理要求）
- **決策**：版本號只能修改 `models_data.py` 的 `VERSION`；部署時間、資料日期等部署事實一律由流水線生成的 `metadata.json` 提供，不得手填。
- **原因**：防止人為編造或遺漏部署事實；頁面運行時讀取 metadata.json 顯示版本／最後部署／資料日期。

## D7 · PricesAPI 降為後備價源（技術限制轉向）

- **日期**：2026-08（v1.2.6 前後）
- **狀態**：已實行
- **決策**：價錢主力來源為 BigGo；PricesAPI 僅對核心 29 型號驗收為選用後備。
- **原因**：BigGo 覆蓋型號更廣；PricesAPI 覆蓋有限。

## D8 · 淘汰機制：網絡錯誤不得視為淘汰（治理規則）

- **日期**：2026-08-18 起
- **狀態**：已實行（治理要求）
- **決策**：停售黑名單只記錄「確認無任何市售報價」的型號；網絡錯誤、限流導致的查詢失敗一律不計入淘汰統計。核心 29 型號及有官方網店價的型號受保護，不得自動淘汰。
- **原因**：防止限流或臨時故障造成誤淘汰；保護受治理約束的型號。

## D9 · 首輪淘汰黑名單：一次性本地驗證後上傳（人類決策）

- **日期**：2026-08-18（2026-08-26 補記）
- **狀態**：已實行
- **背景**：項目啟動初期需要一次全量「是否仍有市售報價」驗證，以建立淘汰黑名單。
- **決策**：首輪 1,103 個型號的黑名單由用戶在本地強行驗證後直接上傳；此類啟動初期一次性數據處理允許本地進行（屬 D2 例外），日常更新仍全自動。
- **原因**：一次性啟動數據無需為其建立長期自動化通道；上傳後由驗證閘門把關再上線。
- **後果**：`model_blacklist.json` 為一次性人類驗證產物；後續日常淘汰由 `model_lifecycle.py` 自動追蹤（連續多次無報價才入黑名單，網絡錯誤不計）。

## D10 · BigGo 免登入 API 通道關閉：轉向官方免費認證（技術限制轉向）

- **日期**：2026-08-26
- **狀態**：已實行（依本條後果記錄，2026-08-26 已提供憑證並完成當時 CI smoke；本次未讀取或重驗憑證）
- **背景**：2026-08-26 下午起，`api.biggo.com/api/v1/spa/search/{query}/product` 對未登入請求返回 `429 {"result":false,"require_login":true}`（本地 IP 與 GitHub IP 同樣）。此前同日早上此通道仍免認證（D1 實證）。網頁版 HTML 頁面仍可訪問，但 JSON API 通道已收緊。
- **選項**：
  - A：回歸網頁版 HTML 解析（GitHub IP 被限流，不滿足全自動）
  - B：轉用其他價錢源（覆蓋/穩定性未知）
  - C：BigGo 官方認證（免費）：註冊 account.biggo.com → 生成 `client_id`/`client_secret` → `https://api.biggo.com/auth/v1/token`（grant_type=client_credentials）攞 access_token，product search 帶 token 請求
- **決策**：選 C；認證資料由用戶在本地一次性生成（符合 D2 一次性例外），憑證只放 GitHub Secrets，日常更新由 CI 全自動帶 token 抓取。
- **原因**：BigGo 為免費官方認證（MCP Server 官方推薦方式）；保持主力價源不變、全自動可延續。
- **後果**：已實施（用戶 2026-08-26 提供憑證，存於 GitHub Secrets）；本地全量復核 728 型號得價 722、黑名單復核復活 8 個，有價型號 731 → 742；CI 帶憑證 smoke 實測通過（run 32979760795）。

## D11 · Canonical 型號鍵：canonical_brand|norm_model（人類決策）

- **日期**：2026-09-03
- **狀態**：已實行
- **背景**：改善方案 F-05 審計發現三套 key 語意分裂——黑名單用原始字串（含 `-`／`/`／空格）、`protected_models()` 回傳正規化 key（且核心 29 實際上誤將整個 dict 正規化）、`record_results()` 用原始字串做 membership check——令含符號嘅型號保護失效、頁面只標示 289 個停售（應為 1,079）。
- **選項**：
  - A：`canonical_brand|norm_model`（唯一品牌 ID + 型號正規化）
  - B：只用 `norm_model` + 跨品牌碰撞閘門
- **決策**：選 A；`crawl_utils.canonical_brand()` 以已核實別名表做跨平台品牌矯正（中文／英文／顯示名 → 統一 ID，例如 日立牌／HITACHI 日立 → HITACHI），未知品牌做大寫化 fallback；`canonical_model_key(brand, model)` 輸出 `BRAND|NORM`。黑名單、model_status tracking、protected set、filter_active、record_results、revive_model 全部共用同一 key。
- **原因**：用戶選定 A，並要求注意品牌名喺各平台唔一致要矯正；跨品牌碰撞從根本上防範。
- **後果**：`model_blacklist.json` 1,095 個 key 遷移為 canonical（matched 1,079、orphan 16 → `UNKNOWN|NORM`、碰撞 0），遷移報告見 `docs/blacklist-migration-2026-09.md`，備份 `model_blacklist.json-bak-canonical-migration`；頁面停售標示由 289 → 1,079。同時修正 `run_price_batch` 並發 3 → 2（回歸 D3）。

## D12 · EMSD 重複登記：保留全部 registration + canonical product view（人類決策，R3）

- **日期**：2026-09-03
- **狀態**：已實行（2026-09-21 核對：PR-3 已入 master；load_registrations、canonical product view、Schema 及 CI 計數參數均已落地）
- **背景**：EMSD CSV 同一型號可有多個登記記錄（1,863 registrations / 1,814 models），舊 `load_models()` 靜默「第一筆勝出」，無審計規則。
- **選項**：
  - A：保留全部登記 + 另出 canonical product view；metadata 分開記錄計數
  - B：維持首筆勝出
- **決策**：選 A；CSV 保留全部 registration，`crawl_utils.load_registrations()` 回傳全部登記、`load_models()` 按 canonical key 去重回傳 product view；metadata.json Schema 新增 optional `rawRecordCount`／`registrationCount`／`modelCount`（向後兼容，CI 未傳就唔寫）。
- **原因**：用戶選定 A；令 1,863 registrations 與 1,814 models 嘅關係可審計。
- **後果**：治理文檔 `AIRCON_METADATA_SCHEMA_V1` 區塊更新（schemaVersion 維持 1.0.0、新欄位 optional）；`validate_metadata.py` 接受新欄位。實施提交 [`1d4e1ee`](https://github.com/CalvinLau1012/aircon-compare/commit/1d4e1ee)；9/20 metadata 已同時記錄三個計數欄位。上文 1,863／1,814 為決策時快照，最新核查見 STATUS。

## D13 · 價錢快照 key：M1 保留原始型號 key（技術範圍決策）

- **日期**：2026-09-03
- **狀態**：已實行
- **背景**：canonical key 全面統一（D11）時，`biggo_prices.json`（742 項）等價錢快照亦以型號字串做 key；全量遷移會波及 generate_html 價格 lookup 與多個 loader。
- **決策**：M1 只遷移黑名單、tracking 同保護集；價錢快照（biggo_prices.json / prices.json / gemini_prices.json）保留原始型號字串 key，需要時以 `norm_model` helper 雙讀。全量價錢 key 遷移延後到後續 PR。
- **原因**：控制 M1 風險同 diff 大小；價錢快照唔參與淘汰／停售判定。
- **後果**：黑名單復核復活時新價會以 norm 型號 key 寫入 biggo_prices.json（該等型號頁面唔顯示，只作復核證據保留）。

## D14 · CI PDF／metadata 次序：兩階段封裝（人類決策，R2-R3）

- **日期**：2026-09-13
- **狀態**：已實行
- **背景**：舊流水線次序係「生成 HTML → 生成 PDF → 生成 metadata.json」。PDF 讀 repo 內上一 run 嘅 metadata.json，令 PDF 嘅 version／datasetDate／deployTime 落後一拍；之後 metadata 又被新 run 覆寫。直接將 metadata 移前唔可行：`releasePayloadHash` 要覆蓋最終 Web／PDF 負載，同 PDF 需要 metadata 形成循環。
- **選項**：
  - A：兩階段封裝——先出同 run 核心事實（無 hash）→ PDF 用核心事實 → PDF 完成後 finalize payload hash 寫正式 metadata
  - B：維持舊次序（PDF 永遠落後）或 PDF 生成後再改寫內文
- **決策**：選 A。`scripts/gen-metadata.py` 加 `--stage core|finalize`：
  1. **core**：由受信任作業生成 version／build／commit／deployTime／dataset 事實（deployTime 仍由腳本 UTC 生成，不接受人手時間）；輸出唔可以叫 `metadata.json`，而且缺 `releasePayloadHash` 過唔到正式 Schema（未 finalize 不可部署）；core 寫入 `$RUNNER_TEMP`（repo 外），`.gitignore` 亦加 `metadata.core.json`。
  2. **PDF**：`generate_pdf.py --metadata <core>`，用同 run 嘅 version／datasetDate／deployTime。
  3. **finalize**：以 `deploy_payload.json` 明確 manifest 計 `releasePayloadHash`，只新增 hash 欄位；寫入前按治理內嵌 Schema 自我驗證；最終 metadata 嘅核心事實同 core 逐欄一致。
- **hash 範圍**：manifest 明確列出 `index.html`、`空調對比報告.pdf`、`emsd_空調能源標籤.csv`；排除 `.git`／`.venv`／`.agents`／cache／測試檔／舊生成物／最終 `metadata.json`（自引用）。framing 用「長度前綴 + 相對路徑 + 長度前綴 + 內容」，排序後計算，確保可重現同無歧義。舊 `--payload-dir .` 只保留兼容，正式流水線唔再用。
- **原因**：用戶 2026-09-13 明確批准此修正；治理 §7.2.3 要求非自引用 payload hash，舊 `--payload-dir .` 範圍過寬且次序錯配。
- **相容性**：Metadata Schema 無變更（schemaVersion 維持 1.0.0，無新 required 欄位）；省略 `--stage` 時 CLI 維持舊單階段行為。`recordCount` 按 D12 註釋改為唯一型號數，CI 同時傳 optional `rawRecordCount`／`registrationCount`／`modelCount`。
- **回滾**：`git revert` 對應 commit（workflow 恢復單階段 `--payload-dir .`；metadata 格式仍係 Schema 1.0.0，無資料遷移）；core 檔只係暫存，無殘留狀態。
- **後果**：PDF 同最終 metadata 嘅 version／datasetDate／deployTime 同 run 一致；hash 範圍可審計、唔會混入 `.git`／`.venv`／`.agents`／工作區任意檔案。

---

## D15 · 私人自建部署線（已移離公開 repo）

- **日期**：2026-09-14
- **狀態**：私人線（2026-09-22 移出公開候選；歷史決策與完整實作暫存於 repo 外 Windows Temp 可恢復副本，**未係長期私人儲存**）
- **背景／決策摘要**：當時為自建 Docker／自架伺服器建立持久發佈工具（image 重建 + volume
  程式碼同步 + staging／apply／rollback 安全不變式）。呢條線唔屬公開 CI／Pages 部署路徑。
- **後果**：完整 bytes、sandbox 測試與 runbook 已移離公開 repo（暫存於 repo 外可恢復副本；commit／push 前人類須轉移到持久私人儲存並核驗 `SHA256SUMS`）；公開候選只保留
  GitHub Actions 每日更新與 Pages 發佈。Git 歷史仍包含此私人線，待人類做私隱評估；
  正式 apply／rollback 一直由維護者喺自己環境執行。
- **回滾**：私人包內附還原指引；公開 repo 唔再提供相關入口。

## D16 · v1.2.9 修復候選：時間真實性、完整驗證與部署後核對（人類授權 CHANGE）

- **日期**：2026-09-21
- **狀態**：已實作（本機候選，E2 通過）；**未部署、未發布、治理 PR／Code Owner 評審未完成**
- **背景**：用戶明確要求補齊版本／更新記錄、修復已知治理問題（時間與資料真實性、完整 Schema／功能證據、部署後核對與歸檔、回滾演練），並明確本輪為程式修復需測試；同時限制不得 commit／push／deploy／發訊息／操作 Secrets／正式 apply。
- **選項**：
  - A：只改文檔與版本號，保留現有抓取／驗證行為——不能滿足時間真實性與完整門禁；
  - B：以真實收據綁定資料事實 + 完整 Draft 2020-12 驗證 + pytest 實際證據 + 明確部署後／歸檔工具，並讓抓取失敗阻斷發布；
  - C：重構整個流水線與發布架構——超出授權及風險範圍（R3）且無人類批准。
- **決策**：採 B，並將產品 `VERSION` 由 1.2.8 升至 1.2.9（候選）。具體：
  1. **收據事實**：成功 EMSD 抓取收據對已寫入 CSV 計 `datasetHash`、記錄實際 UTC `retrievedAt`；403/429、0 頁、壞表頭、計畫／提交失敗都寫失敗收據，唔改舊 CSV。部署 metadata 的 `datasetDate`（UTC+8）／`datasetRetrievedAt`／來源／快照 ID 由成功、hash-bound 收據產生；`basis=retrieval-date-fallback`。重建用舊完整收據保留舊日期；舊無 hash 收據必須重新成功抓取。**（第二輪修訂）** CSV／`new_models.json`／`update_queue.json` 改為 `plan_new_models`（純讀計畫）＋`commit_dataset`（三檔 tmp＋replace，任一失敗即回滾），唔會半更新；成功收據在整組提交後才寫。
  2. **Fail-closed**：每日 EMSD 抓取失敗即中止（移除 `continue-on-error`）；core 未通過 `--core` 驗證唔會生成 PDF；正式 Schema 仍然拒絕無 `releasePayloadHash` 嘅 core。官網批次用 `run_official_batch.py` 作雙重檢查（return code＋輸出有效）；**任一實際嘗試目標失敗即非零、唔推進隊列**，且失敗時只喺記憶體累積、全過才原子替換快照（唔可以用部分結果覆寫）。BigGo smoke 失敗跳過並明確記錄「未刷新、保留快照」，真批次失敗非零退出。
  3. **完整驗證**：`validate_metadata.py` 改用 Draft 2020-12 + FormatChecker；`extract_governance.py` 拒重複 JSON key、Registry 與成功標準按內嵌 Schema 完整驗證；新增 `jsonschema[format]==4.26.0` 依賴。
  4. **功能證據**：`feature-check.py` 由檔案存在改為 pytest 實際 collection node ids + 靜態斷言檢查；新增 `pytest_evidence_plugin.py` 收集 setup／call／teardown、skip／xfail／fail，`--run-tests` 要求 required 綁定實際 passed。**（第二輪修訂）** 靜態檢查亦拒絕空斷言、只有常量斷言（`assert True`、`1 == 1`、`x = True; assert x`）、unittest 常量斷言及 try/except pass 吞例外嘅總是成功測試。
  5. **部署後／歸檔候選**：新增 `postdeploy_check.py`（expected＋online 完整 Draft 2020-12＋format、整個 metadata JSON object 等值、線上 payload hash／CSV hash／瀏覽器 runtime 顯示，cache bust + 有界重試，錯版本最終失敗，只准官方 Pages／localhost）及 `archive_release.py`（逐檔 CHECKSUMS＋PROVENANCE；`archiveCommit`／`sourceCommit`／`deploymentCommit` 分離；目錄＋zip 雙重 no-clobber；嚴格 SemVer tag）。`postdeploy-verify.yml` 接受 Pages 內建 `dynamic` 及 `push` 事件、只接本 repo master 成功、contents:read；`release-archive.yml` 封裝前先跑 GATE-08、預設唔發布。**（第二輪修訂）** 所有 workflow 第三方 Actions 固定到 GitHub refs API 核實嘅完整 commit（checkout／setup-python／upload-artifact／download-artifact）。
  6. **回滾演練**：公開 fixture-only `tests/test_restore_drill.py` 做 temp-dir 應用＋資料恢復（checksum、schema／MAJOR 兼容、失敗唔破壞 good package）。
- **原因**：用戶要求修復問題並要求測試；治理 §7.2 要求 metadata 事實來自部署作業、§9.2 要求功能契約有實際行為證據、§11.1 要求部署後驗證、§8.3 要求長期可審計資產。唔降低任何 required 功能或門禁。
- **後果（分類）**：
  - `REQUIREMENT`：required 狀態、protection、Metadata Schema、成功標準及硬門禁未降低；Feature Registry 的證據 bindings 有待評審候選更新（2026-09-22 新增多個 testBindings）。此候選**未經 Code Owner／治理 PR 批准**；受信任 CI（E3）與部署後驗證（E4）均未完成。
  - `OBSERVED / E2`：pytest 399 passed；feature-check `--run-tests` 18 節點 passed；公開恢復演練 5 項（16 斷言）passed；postdeploy／archive fixture 測試通過；三個 workflow Actions 已固定完整 commit（2026-09-21 refs API 核實）。
  - `UNKNOWN`：受信任 CI（E3）、實際 Pages 部署後核對（E4）、真實 EMSD 抓取產生新 hash-bound 收據、真實自建 apply／rollback、Release 歸檔發布均未執行。
- **回滾**：本輪全部改動在本機未提交；可由本輪開始前外部備份或 `git checkout -- <file>` 還原。若日後已入 master，可 `git revert` 對應 commit；metadata Schema 與部署格式不變，無資料遷移。`models_data.VERSION` 回 1.2.8 需連同文檔記錄一併 revert。

## D17 · 2026-09-22 使用者決策：D1-B／D2-A／D3 pending／D4-A／D5-A／D6-A deferred／D7-A／D8-A／D9-A

- **日期**：2026-09-22
- **狀態**：D1-B、D2-A、D4-A、D5-A、D7-A、D8-A、D9-A 已批准；D3 待使用者選 A／B／C；D6-A deferred。
- **背景**：v1.2.9 候選（D16）已完成大量治理修復，但 GATE-07 Pages 部署鏈、原始 EMSD bytes、
  全歷史秘密審計、監控告警、私人 self-host 線分離及 PR 交付仍待人類決策／實作。
- **決策**：
  1. **D1-B**：官網 enrichment queue model 未被目錄／parser 覆蓋時，EMSD CSV／Web／PDF／
     metadata 可照發布；queue stage／models 原樣保留；machine receipt 記 pending coverage
     同缺少 canonical models；網頁／status 顯示「官網規格待核」。壞 queue／receipt／schema／
     script／output hash race／failed>0 仍硬失敗阻斷。ADR-002 由提案改為已批准。
  2. **D2-A**：GitHub Pages 改用 Actions 統一部署。PR 只 build＋完整 gates，永不 deploy；
     master deploy job `needs` build、`environment: github-pages`、最小 `pages:write`／
     `id-token:write`；Pages artifact 只可包含 `deploy_payload.json` 公開檔案，拒 symlink／
     缺檔／額外私人檔／traversal；所有 Actions 固定完整 commit；`postdeploy-verify.yml`
     綁新 workflow exact `head_sha`，不回退 latest。
  3. **D3**：`release` protected environment 的 required reviewer 仍待選 A（單人維護，
     prevent_self_review=false）／B（獨立覆核，prevent_self_review=true）／C（不設）；
     未選前不得宣稱 release protection 完成。
  4. **D4-A**：新增全 reachable refs 歷史秘密審計；credential findings 必須 0；self-host
     path／port／檔名列已知殘餘風險。若發現真秘密即停 push／PR，只報類型／commit／path。
     未有秘密則保留歷史，不 filter-repo／force-push。
  5. **D5-A**：私人 self-host 包移入私人 repo（名稱／URL 只喺交付報告）；
     visibility 必須 API 回讀 PRIVATE；來源 SHA256SUMS 先驗，fresh clone 逐檔重算；
     Temp 原件在使用者確認前不刪；公開 repo 不加私人 URL／路徑／IP／port／secret。
  6. **D6-A**：公開 v1.2.9 PR merge 後才同步私人 self-host 線（queue／receipt／metadata／
     Pages-independent 契約）；PR 階段標 DEFERRED BY DECISION D6-A。
  7. **D7-A**：原始 EMSD response bytes 私人保存 90 日，公開只留 hash receipt；raw receipt
     與 CSV datasetHash 雙向綁定；private save 失敗阻斷公開提交／部署；PR 用 fixtures／假
     HTTP／假 sink 測試，真正 Secret 同首個 live snapshot 屬 merge 後平台驗收。
  8. **D8-A**：新鮮度閾值 `age > 72h`（以 metadata.datasetRetrievedAt UTC 計）；71:59:59 同
     72:00:00 pass；missing／invalid／future timestamp、Schema／payload hash／Pages 讀取／
     postdeploy mismatch 硬失敗。Monitor 每 6 小時＋workflow_dispatch，同狀態不重複開／更新
     issue，狀態改變才 update，恢復 close；alert 不含秘密／私人基建。
  9. **D9-A**：從 dirty master 建 `codex/v1.2.9-governance-release`，整理可審閱 commits，
     push 後建 draft PR；PR checks 跑完整 gates；dsh 不自行 merge、tag、Release 或 deploy。
- **原因**：公開 Pages 線同私人 self-host 線要徹底分離；同時滿足資料新鮮、原始證據、全歷史
  私隱審計、告警去重及可審計 PR 交付，而不降低任何 required 功能／門禁。
- **後果（分類）**：
  - `REQUIREMENT`：required 功能、protection、Metadata Schema、成功標準及 fail-closed
    門禁全部保留；D1-B 只放行可證明的 coverage pending。
  - `OBSERVED / E2`：本機 pytest 433 passed／1 skipped、focused 69 passed／1 skipped、feature-check 18 節點、acceptance 7 gates rc=0；
    私人 repo PRIVATE 回讀及 fresh clone checksum 已取得；history audit credentialFindings=0。
  - `UNKNOWN`：受信任 CI（E3）、live Pages E4、真正 private sink Secret／首個 live raw
    snapshot、release environment required reviewer、D6 self-host 同步均未執行。
- **回滾**：本輪全部改動在 branch `codex/v1.2.9-governance-release` 未 merge；可 revert
  對應 commit；私人 repo 不移除公開 repo 歷史，亦不改 production metadata。

## D18 · PR #10 審查返修：部署封包、daily→Pages 銜接、combined health、可插拔 raw sink（R6）

- **日期**：2026-09-23
- **狀態**：本機候選已實作（未 commit 前）；remote raw sink live 啟用未批准（UNKNOWN）。
- **背景**：PR #10（v1.2.9 治理發布候選）審查發現 9 項缺陷：Pages artifact 缺
  metadata.json；daily 用預設 GITHUB_TOKEN push 唔觸發 Pages workflow；freshness
  `plan_issue` 忽略 `postdeployOk`；D7 只有 local sink；`pages-deploy.yml`
  workflow_dispatch 唔會入 production；`build_pages_artifact build` 直接 rmtree 任意
  `--out`；daily 同 bootstrap 共用 concurrency group 可以互相 cancel；Windows
  symlink 測試 skip；信任邊界需再審查。
- **決策**（屬已批准 D2-A／D7-A／D8-A 之實作，無新產品政策）：
  1. **封包分層**：`deploy_payload.json` 保持唯一 hash 範圍；新增
     `deploy_envelope.json` = payload + metadata.json + 一致公開 sidecar；封包前驗
     Schema／version／payload hash／CSV hash／counts，錯配 fail-closed；輸出目錄拒
     repo 根／祖先／`.git`／link 父層／與輸入重疊／未封印既有目錄，staging + 安全替換。
     詳見 ADR-003。
  2. **daily→Pages 銜接**：daily push 成功後以 `repository_dispatch` 帶精確已 push
     commit + sourceRunId；Pages 用 GitHub API 核實成功 run／master／祖先，PR／fork
     永不 deploy；concurrency 按 event／ref 隔離。詳見 ADR-003。
  3. **combined health**：freshness 同 postdeploy 失敗合併判斷，fingerprint 唔含
     ageSeconds（同一 stale 6 小時後 noop）；分類改變才 update；完全恢復才 close；
     report 缺失／non-object／network／API 錯誤全部非零且脫敏。
  4. **可插拔 raw sink**：local-dir 如實標示非 durable；新增 GitHub Release asset
     adapter（PRIVATE 回讀、唔覆蓋、上傳後下載 hash 核驗、90 日 retention），只有
     fake HTTP 測試，未建立任何 Release；require 缺配置即阻斷。詳見 ADR-004／
     `docs/PRIVATE_RAW_SINK_RUNBOOK.md`。
  5. **測試**：新增真 HTTP + 瀏覽器 runtime + PDF 重建 E2E、fake GitHub API
     dispatch／verify、remote sink、symlink／junction 無 skip 拒絕測試。
- **原因**：修復 PR 評審發現嘅 fail-open／覆蓋缺口，同時唔降 required 功能、唔擴權、
  唔虛報 live 證據。
- **後果（分類）**：
  - `REQUIREMENT`：D14 hash 語義、D2-A 最小權限、D7-A fail-closed、D8-A 72h／
    去重、18 個 required feature nodes 全部保留。
  - `OBSERVED / E2`：本機 pytest、acceptance 7 gates、fake HTTP／display 證據（見
    STATUS §11 實數）。
  - `UNKNOWN`：merge 後首個 daily dispatch、真正 Pages Actions E4、remote raw sink
    provider 選擇／Secret／首個 live snapshot、D3 required reviewer（其後由 D19 解決）、D6 self-host 同步。
- **回滾**：本輪全部改動仍喺 branch；可 revert 對應 commit；唔會回退 production
  metadata／runtime snapshots。

## D19 · `release` environment 單人 required reviewer（D3-A）

- **日期**：2026-09-23
- **狀態**：已實行（平台 API 已回讀）
- **背景**：GATE-09 的 publish job 使用 `environment: release`；D17 留下 A（單人維護、
  `prevent_self_review=false`）／B（獨立覆核）／C（不設 reviewer）三個選項。項目目前只有一名
  維護者，因此無法採用需要另一名使用者批准的 B。
- **選項**：A：維護者本人為 required reviewer 並允許 self-review；B：另一名可信使用者／team
  獨立批准並禁止 self-review；C：不設 required reviewer。
- **決策**：採 D3-A。GitHub `release` environment 已建立；唯一 required reviewer 為
  `CalvinLau1012`（GitHub user id `178408566`），`prevent_self_review=false`、`wait_timer=0`、
  `deployment_branch_policy=null`、`can_admins_bypass=true`。2026-09-23 GitHub API 建立回應及其後回讀均顯示
  `required_reviewers` protection rule。
- **原因**：單人維護條件下仍保留一次明確的人工作業批准，避免手動觸發 `publish=true` 後立即
  發布；同時不建立實際無人可以通過的獨立覆核關卡。
- **後果**：
  - `OBSERVED / 平台`：引用 `environment: release` 的 publish job 會進入 environment approval；
    維護者可批准自己的 deployment；管理員亦可另行明確 bypass。一般 PR build／只建立歸檔而不 publish 的路徑不因本決策改變。
  - `UNKNOWN`：本決策沒有執行 publish、Release、tag、deploy、E3 或 E4；首次真實等待／批准流程
    仍須在獲准發布時驗收。
  - `SECURITY TRADE-OFF`：批准人與提交者可以是同一人，提供防誤觸關卡，但不構成獨立雙人覆核。
- **回滾**：可在 GitHub environment 設定移除 reviewer／刪除 `release` environment，並以新決策
  記錄取代 D19；不得只改文件而留下平台設定漂移。

## 決策模板

新決策按以下格式追加：

```
## DX · 標題

- **日期**：YYYY-MM-DD
- **狀態**：提議／已實行／已取代
- **背景**：（問題或需求）
- **選項**：（如有）
- **決策**：（採用的方案）
- **原因**：（為什麼這樣決定）
- **後果**：（已知影響）
```


## D20 · 用戶批准 v1.2.9 按序發布（合併前核對 → merge → daily deploy → E3／E4 → tag／Release）

- **日期**：2026-09-23
- **狀態**：已批准（本輪只執行合併前修復與證據；生產執行仍待獨立驗收）
- **背景**：v1.2.9 候選（D16）及 PR #10 返修（D18／R7）已完成本機 E2，但 merge、真實 daily、
  Pages E4、Release 歸檔全部未發生。用戶需要決定發布授權同次序。
- **選項**：A：一次過授權合併、部署、tag／Release；B：分階段授權（合併前核對 → merge →
  正式 update／deploy → E3／E4 通過後才 tag／Release）；C：暫不發布。
- **決策**：用戶批准 B 的完整次序並確認授權成立。本輪（2026-09-23 R8）只完成合併前修復與證據：
  remote raw sink env 接線、合併→daily→Pages→postdeploy→archive 前置／失敗條件核對、測試與門禁證據，
  交回獨立驗收後才執行生產 merge／deploy；tag／Release 僅在 E3／E4 通過後。
- **原因**：用戶要求嚴守次序，避免未驗證候選直接上線或提早建立長期資產；同時保留 fail-closed 門禁，
  不為取得綠燈放寬。
- **後果**：
  - `REQUIREMENT`：required 功能、protection、Metadata Schema、成功標準及阻斷門禁不變；本次無降級。
  - `OBSERVED / E2`：remote env 接線測試、merge push fail-closed 實測（1.2.8 vs 1.2.9 版本／
    payload hash 錯配）、本機 gates／acceptance 實數（見 STATUS §14）。
  - `UNKNOWN`：merge、Pages Source 切換、首次 daily、repository_dispatch E3、live E4、
    tag／Release、D7 provider／Secrets、首次 release environment approval 均未執行。
  - `BOUNDARY`：本輪沒有執行 merge、deploy、tag、Release、Secrets 操作或 Pages 設定變更；
    亦沒有建立 PAT／共用憑證。無憑證前唔可以宣稱 D7 完成。
- **回滾**：本決策只記錄授權與次序，無平台狀態改動；如需撤回，以新決策記錄取代，並維持
  現有 fail-closed 門禁。


## D21 · Release 後 runtime hotfix：Node.js 24 Actions＋`ubuntu-24.04` runner＋bounded BigGo smoke

- **日期**：2026-09-24
- **狀態**：已實作候選（本機 E2）；未 merge、未發布；交獨立驗收後決定
- **背景**：v1.2.9 已發布並上線。daily／Pages／postdeploy／release 實際 run logs 出現
  GitHub 警告「`Node.js 20 is deprecated ... forced to run on Node.js 24`」，來源係當時
  固定嘅舊官方 Actions（node20 runtime）；另 GitHub 提示 `ubuntu-latest` label 將於
  2026-10-19 起遷移到 Ubuntu 26。使用者明確要求本輪 hotfix 修正，但不得為消除警告降低
  任何門禁。
- **選項**：
  - A：只升 action major tag（例如 `checkout@v7`）——可快速消警告，但破壞完整 SHA pin，
    亦違反治理 §9.3；
  - B：升級到官方 node24 release 嘅完整 commit（refs API 回讀）＋保留全部最小權限、
    environment、concurrency、fail-closed 條件；runner 由浮動 `ubuntu-latest` 固定為
    `ubuntu-24.04`（不提前用 Ubuntu 26）；BigGo smoke 改有界（單次 attempt、每網絡階段
    約 8 秒 timeout、無 60／90 秒冷卻），批次／正常查詢保持原完整語義；
  - C：暫時忽略警告／等 GitHub 強制升級——唔符合使用者要求，亦令 CI 持續出現
    deprecation 噪音。
- **決策**：採 B。產品版本不變（`models_data.VERSION` 維持 1.2.9，未改 production
  metadata／資料／生成物）；本輪只係 release 後技術 hotfix 候選。
- **原因**：官方 node24 release＋完整 SHA pin 同時滿足治理 §9.3 同 deprecation 修復；
  runner 固定可預期，避免 2026-10-19 突然跳 Ubuntu 26；smoke 有界令失敗唔會拖長
  daily workflow（2026-09-23 run 35911295151 嘅失敗 smoke 曾耗時約 14 分鐘），
  而批次可靠性（retry／冷卻／限速）完全不變。
- **後果（分類）**：
  - `REQUIREMENT`：15 項 required 功能、protection、Metadata Schema、成功標準、
    fail-closed 門禁、最小權限、完整 SHA pin 全部不變；冇任何門禁被放寬。
  - `OBSERVED / E2`：本機 pytest 同 workflow 靜態測試（實數見 [STATUS.md](STATUS.md) §15）；
    官方 release `action.yml` runtime=node24 同現有 inputs 相容性已逐一核對；舊 Node 20
    SHA 加入測試負向清單。
  - `UNKNOWN`：PR trusted CI 警告掃描、merge、production 再部署／live E4 未執行。
  - `BOUNDARY`：冇 tag／Release、冇 production dispatch、冇真實 BigGo／EMSD 抓取、
    冇 Secrets／environment／版本／metadata 改動。
- **回滾**：`git revert` 本輪 commit 即恢復舊 pin／runner／smoke 語義；產品 metadata
  與線上狀態不受影響。
- **2026-09-24 驗收更新（追加）**：Draft PR #14 head
  `2d17a9c99537652627abc53f6275e63d462b75dd` 已取得 trusted PR E3：
  `pull-request-gates` run 35941034804 與 Pages `build` run 35941034788 均 success；
  實際執行 job 日誌中 Node.js 20 forced-runtime、`ubuntu-latest`／Ubuntu 26 migration、
  `punycode`／`DeprecationWarning` 均為 0，runner 為 `ubuntu-24.04`。因此上述
  `UNKNOWN` 中「PR trusted CI 警告掃描」已轉為 `OBSERVED / E3`；merge、production
  再部署及 live E4 仍未執行。production-only upload／download artifact 與 deploy
  steps 在 PR 路徑按設計 skipped，首次實際執行仍須觀察。


## D22 · Pages 部署後 GATE-08 改為同一 workflow 的必要 reusable job

- **日期**：2026-09-24
- **狀態**：已實作候選（E2）；待 trusted CI、merge 後 production 實證
- **背景**：PR #14 merge 後的 push Pages run 35942461038 可由舊
  `workflow_run` 啟動 postdeploy；但獲授權完整 daily 透過
  `GITHUB_TOKEN` 發出 `repository_dispatch` 後，Pages run 35942793376 雖然
  build／deploy success，卻沒有建立對應的 `workflow_run` postdeploy run。較早 daily
  Pages run 35913258756 亦有同樣現象。當次已用手動 exact-ref run 35943298448 補做完整
  GATE-08 並成功，但自動鏈仍有缺口。
- **選項**：A：保留跨 workflow 的 `workflow_run`，接受 token 事件連鎖不可靠；
  B：production Pages deploy success 後，在 `pages-deploy.yml` 內以本 repo reusable
  workflow 直接呼叫 GATE-08，同時保留手動 exact-ref fallback；C：只依賴 freshness
  monitor 的 no-browser 核對。
- **決策**：採 B。Pages `postdeploy` job 必須 `needs: [build, deploy]`，只在
  production 且 deploy success 時呼叫 `postdeploy-verify.yml`，傳入 build 已核實的
  exact commit；called workflow 只取 `contents: read`，核實 checkout HEAD 等於指定
  ref 且為 `origin/master` 祖先。PR 不 deploy，亦不執行 postdeploy。移除舊
  `workflow_run` 入口，避免重複或漏跑；保留 `workflow_dispatch` 人工 fallback。
- **原因**：把 GATE-08 放進同一 Pages run 的依賴圖，部署成功後由 GitHub 直接排程必要
  job，亦令 GATE-08 失敗反映在 Pages workflow 結論，毋須依賴另一個 token 觸發事件。
- **後果**：
  - `REQUIREMENT`：GATE-08、exact commit、master 祖先、最小權限與 fail-closed 不變；
  - `OBSERVED / E2`：聚焦測試 91 passed；machine acceptance 7/7 gates、完整 pytest 529 passed、18 個 required nodes 全 passed；
  - `OBSERVED / E4 fallback`：run 35943298448 對 commit `b80a1d5` 完整通過；
  - `UNKNOWN`：同 workflow automatic postdeploy 要在 merge 後 production Pages run
    實際成功，才可宣稱自動鏈閉合。
- **回滾**：可 revert 本決策實作；不得在沒有等價可靠自動 GATE-08 的情況下只刪除
  reusable call。

## D23 · BigGo API 只在價格批次需要推進時呼叫

- **日期**：2026-09-24
- **狀態**：已實作候選（E2）；待 trusted CI
- **背景**：獲授權 daily run 35942488710 在價格批次未啟動時仍先執行一次 smoke。
  用戶其後明確要求「不要不停調用 BigGo API，有需要才使用」。
- **決策**：非 force 路徑先用本地 `scripts/price_batch_state.py` 判斷狀態。只有
  active 批次才執行 bounded smoke 與 `--price-batch`；inactive／已完成直接
  `skip-not-active`，零 BigGo API 請求。meta 損毀仍 exit 2 阻斷；明確
  `force_price_batch=true` 仍由 `--force-batch` 內建 smoke 保護。
- **原因**：避免每日排程為無待辦價格批次消耗 API 請求，同時保留有需要時的連線保護、
  批次重試／冷卻與失敗保留快照語義。
- **後果**：EMSD daily、Pages、GATE-08 都不需要 BigGo；未啟動價格批次的日常 run 不再
  接觸 BigGo。價格批次 active 或人類明確 force 時才會使用。


### D22／D23 · 2026-09-24 production 驗收更新（追加）

- PR #15 exact head `92a5e134493ab7c99263288c366f9fa163bf15b3` 的 trusted CI：
  Pages build run 35944528389 success，daily pull-request-gates run 35944528135 success；
  PR 的 update／deploy／GATE-08 全部按設計 skipped。
- PR #15 已以 merge commit `3f7799960f74a3f4e8d49987fb3a87a8df67d278` 合併。
  合併只觸發 Pages production run 35944781623，沒有觸發 daily 或 BigGo。
- 同一 Pages run 依序完成 build（2m23s）→ deploy（11s）→
  `部署後核對（GATE-08） / verify`（44s），三個 job 均 success；GATE-08 核實 exact
  commit 與 master 祖先後，metadata full-object、payload／CSV hash、PDF 重建及瀏覽器
  runtime 全部 PASS。D22 的 production `UNKNOWN` 已轉為 `OBSERVED / E4`。
- D23 的靜態契約、完整 acceptance 與 trusted PR CI 已通過；inactive 零 API 路徑將由
  下一次自然 scheduled daily（且 price batch inactive 時）提供 runtime 日誌證據。
  本輪不為製造證據重跑 daily，遵守用戶「不要不停調用 BigGo」要求。

## D25 · 文檔只可追加、不可刪除或改寫（人類決策）

- **日期**：2026-09-24
- **狀態**：已實行（約定）
- **背景**：用戶 2026-09-24 指示「以元文件為準；如果我在後面有改變，你也要記錄，不要刪除，
  只能添加」。本輪文檔整理最初以「改寫舊句」方式更新（未 commit 前已還原），需要一致嘅記錄規則。
- **選項**：
  - A：直接改寫舊句，令文件只顯示最新狀態（**否決**：失去歷史同審計軌跡）；
  - B：只追加新節／新條目，舊記述原文保留，並標明日期同證據（採用）。
- **決策**：採用 B。適用範圍：各更新日誌、`docs/STATUS.md`、`docs/GOVERNANCE_MATRIX.md`、
  `docs/DECISIONS.md`、`docs/adr/*`、`docs/PRIVATE_RAW_SINK_RUNBOOK.md`、`README.md`、
  `需求摘要.md`、`CHANGELOG.md`。需求以 `需求摘要.md`（元文件）為準；用戶日後嘅改變亦只可追加。
- **原因**：保留歷史快照同可追溯性，令審計可以重建當時狀態；亦避免 AI 靜靜改寫前人記述。
- **後果**：
  - 同一份文件可能同時有「當時記述」同「追加更新」；現況以最新追加節為準，舊記述保留原語義。
  - 本輪追加位置：`STATUS.md` §17、`GOVERNANCE_MATRIX.md` §10、`docs/README.md`
    2026-09-24 節、ADR-003／ADR-004 狀態更新節、runbook §5、`README.md` 資料日期口徑追加條目、
    `需求摘要.md` 現況快照追加註。
  - 格式／錯字修正唔算狀態改寫，但必須喺 commit message 標明；語義內容一律只可追加。
- **回滾**：如日後改回可改寫，需以新決策取代本項並更新 `AGENTS.md` 規則 10。

## D24 · 更新日誌／純文檔改動唔手動重生 `index.html`（md-only commit，人類決策）

- **日期**：2026-09-24
- **狀態**：已實行（約定）
- **背景**：`deploy_payload.json` 的 payload 包含 `index.html`、`空調對比報告.pdf`、
  `emsd_空調能源標籤.csv`；`metadata.json` 的 `releasePayloadHash` 由 daily 流水線對呢啲
  bytes 計算。2026-09-24 兩次文檔 push（PR #17 merge run 35946623750、更新日誌整理 run
  35950593781）都因為手動重生 `index.html` 而令 production Pages build 以
  「releasePayloadHash 唔一致」fail-closed；兩次都由下一次 daily run 重新生成
  `index.html`＋`metadata.json` 後自動恢復（例：run 35946820790 → `repository_dispatch`
  run 35947112610 success）。
- **選項**：
  - A：每次改更新日誌都重生 `index.html`，接受一次 production 紅 run；
  - B：純文檔／更新日誌改動只 commit 文檔，`index.html` 交由下一次 daily 一併重生（md-only commit）；
  - C：為文檔同步手改 `metadata.json`（**否決**：等於手填部署事實，違反 D6 同 AGENTS 規則 2）。
- **決策**：採用 B。用戶 2026-09-24 明確選擇 md-only commit 約定，並且唔授權為此額外觸發一次
  production daily run。
- **原因**：純更新日誌改動唔影響比較器功能，冇必要令 production build 無謂 fail-closed；同時
  唔可以為求同步而手動改部署事實。
- **後果**：
  - 約定期間 `index.html` 可能暫時滯後於 `空調對比報告.md`；下一次 daily 會用
    `generate_html.py` 重生並連同新 `metadata.json` 一齊提交同部署。
  - 真正改 UI／CSS／報告內容仍按 AGENTS 規則 8 重生；該次 push 的 Pages run 預期會
    fail-closed 到下一次 daily（現行 pipeline 語義，未改變）。
  - 2026-09-24 本輪已把 `index.html` 回復到 pipeline 一致版本
    （`payloadHash=sha256:b2de4e3f…` 同 `metadata.json` 相符；本地
    `build_pages_artifact.py --check-only` rc=0），令 build gate 唔會因為純文檔 push 而紅。
  - **2026-09-24 實測補充（GATE-08 PDF 核對）**：payload 內有**兩個**由 `空調對比報告.md`
    生成嘅檔案——`index.html` 同 `空調對比報告.pdf`。改 md 更新日誌後，即使唔手動重生
    payload 檔案，GATE-08 嘅 `payload.pdf_matches_metadata` 仍會紅，因為 committed PDF
    內容仍係舊 md：run 35951000298 就係 build／deploy success、GATE-08 failure
    （online `8f13a3c3…` vs rebuilt `d548e1d4…`）。本地 A/B 重建證實因果：
    用 md @`8c213c8`（未加日誌）重建 = `8f13a3c3…`（同 committed／線上 PDF 逐位元相同）；
    用加咗日誌嘅 md 重建 = `d548e1d4…`。
  - 因此本約定嘅準確預期係：
    - 只改 `README.md`／`需求摘要.md`／`CHANGELOG.md`／`docs/*`（唔郁 md）→ payload 不變 → 全綠；
    - 改 `空調對比報告.md`（網站更新日誌）→ 預期一次紅 run（唔重生就 GATE-08 PDF 核對紅，
      手動重生就 build gate 嘅 `releasePayloadHash` 紅），下一次 daily 全量重生
      `index.html`＋PDF＋metadata 後恢復綠；唔好為咗即時變綠而手動重生 payload 或改 metadata。
  - **2026-09-24 觀察補充（append）**：上面「唔郁 md → 全綠」有前提——payload 內嘅 md 生成物
    必須已經同 md 同步（即上一次 daily 已跑完、md 之後未再改）。一旦 md 有新改動而 daily 未跑，
    之後**任何** push（包括純 docs）都會 GATE-08 `payload.pdf_matches_metadata` 紅：
    run 35952357474（純文檔追加 `5cb4a03`）同 35952373427（`04604b6`）都係 build／deploy
    success、GATE-08 failure，hash 對同 35951000298 一樣（online `8f13a3c3…`／rebuilt
    `d548e1d4…`）。呢個係 pending 狀態嘅性質，唔係新增改動造成。
  - 回滾：如日後改回每次重生，刪除本約定並更新 AGENTS 規則 9 即可。

## D26 · R3 雙 writer／EMSD 雙來源／BigGo 自動 atomic lease／私人 00:45 timer

- **日期**：2026-09-28
- **狀態**：已實作候選（本機 E2）；未 push、未開 PR、未 deploy；交獨立驗收後決定
- **背景**：用戶明確批准 R3 架構改變（資料來源＋部署＋權限）：(1) GitHub Pages 同私人
  自架 server 兩個 writer 共用同一套 BigGo 憑證；(2) EMSD 官方開放資料多了一個穩定
  open-data CSV 來源，需要主來源＋獨立核對；(3) 私人 server 排程仍需唯一、可審計。
  先前 v1.2.9 只由 GitHub Actions 寫 Pages；私人 server 舊設計係單一 cron+nginx 容器。
- **選項**：
  - A：維持單 writer、單一 EMSD 分頁來源、人手控制 BigGo 呼叫——否決：兩個 writer 之下
    冇 lease 會重複耗用同一憑證，亦冇 API 級事實去支撐自動化；
  - B：兩個 writer 各自獨立呼叫 BigGo；EMSD 只用一個來源；私人 server 保留容器 cron——
    否決：重複呼叫同一 provider、冇 cross-check、兩個 scheduler 不可審計；
  - C（採用）：兩個 writer 隔離寫自己 namespace；EMSD open-data CSV primary＋逐頁
    cross-check；BigGo 用 GitHub Contents API blob SHA CAS 自動 lease；私人 server 由
    host systemd 00:45 HKT timer 唯一排程，one-shot updater＋read-only nginx。
- **決策**：採 C。實作：
  1. **兩個 writer**：GitHub Actions 維持現有 Pages 路徑；私人 repo 新增 one-shot
     updater（`docker/run-update.sh`＋`docker/lib/release_tree.sh`）、獨立
     `aircon-web`（read-only）、三個 isolated named volumes（production-data、
     test-data、raw-evidence）、versioned release＋atomic `current` symlink。
  2. **EMSD 雙來源**：`emsd_dual_source.py` 做確定性 header 映射／欄位正規化／
     registration-key＋governed-field 比較；`fetch_emsd.py` 只在兩個來源完整且一致時
     才提交 CSV／收據；mismatch 寫脫敏 diff report（repo 外）並保留上一生產資料集；
     兩個來源 raw bytes 以 `sourceKind` namespace 入私人 raw archive；公開收據加
     `dualSource` 區塊（metadata Schema 不變，`receipt_facts` 只接受 verified 雙來源
     receipt）。
  3. **BigGo 自動 atomic lease**：`scripts/biggo_coordinator.py`（generic、只靠環境／
     Secrets；GitHub Contents API blob SHA CAS；lease 45 分鐘、每 5 分鐘 renew；
     completed cycle+stage idempotent；needs_review 禁止自動重跑；本地 cap
     1+2×queued；factual quota 80% cap；安全 rate-limit 證據 allowlist）；
     `scripts/biggo_stage_runner.py`（本地 stage 先決；inactive 零 token／search／
     coordinator mutation；缺 coordinator 配置安全跳過保留快照；winner smoke→batch→
     publish；任何呼叫但 snapshot 未確定→needs_review）；`fetch_biggo.py` 改為每 model
     最多 2 attempts，403／429 唔即刻 retry（honour Retry-After，否則項目自身 48h
     fallback），smoke 最多一個 search request。
  4. **私人排程**：`systemd/aircon-update.timer` 明確 `Asia/Hong_Kong`、00:45、
     `Persistent=true`、`RandomizedDelaySec=0`；容器 cron／entrypoint 已刪除；
     test compose 有獨立 port／network／volume 並預設阻擋 `api.biggo.com`。
- **原因**：CAS lease 係 GitHub Contents API 已有嘅原子原語，唔需要另建服務；本地
  stage 先決確保兩個 writer 日常零重複呼叫；雙來源 fail-closed 保留「EMSD 為唯一權威」
  同「唔可以帶住唔一致資料上線」嘅既有契約；00:45 HKT host timer 令私人線只有一個
  可審計 scheduler。
- **後果（分類）**：
  - `REQUIREMENT`：15 項 required 功能、Metadata Schema、成功標準、所有 fail-closed
    門禁、公開 attribution、最小權限全部不變；48h 冷卻一律描述為項目自身政策，
    **唔係** provider quota window；官方 provider quota／window 保持 UNKNOWN。
  - `OBSERVED / E2`：公開 repo 本機 pytest 570 passed（非瀏覽器）＋12 browser smoke；
    feature-check 15 項／18 節點 passed；governance extract／validate_data／
    validate_metadata／git diff --check 全部 rc=0。私人 repo focused tests 14 passed／
    1 skipped（本機冇 docker）；sandbox/restore PASS=117 FAIL=0（s12 因本機冇 rsync
    skip）。實數詳見 `docs/STATUS.md` §18。
  - `UNKNOWN`：trusted CI（E3）、任何 deployment／E4、首次自然 active-stage lease
    競爭、首次雙來源 live 一致性（本機環境 TLS 被阻，未能執行 controlled live fetch）。
  - `BOUNDARY`：冇 push／PR／deploy／tag／Release；冇改 GitHub Secrets／environment；
    冇 SSH 生產 server；冇真實 BigGo API 呼叫；冇改 `metadata.json`／`index.html`／
    committed PDF／EMSD CSV 等生產生成物；冇降低任何門禁。
- **回滾**：兩個分支可 `git revert`／直接丟棄（`codex/dual-writer-coordination`、
  private `codex/dual-writer-server`）。公開 workflow 可回復舊 BigGo step；私人 repo
  可回復舊 `run-update.sh`＋cron 設計。已發布 Pages metadata 與線上狀態不受影響。

### D26 · 2026-09-28 後續返修（追加；不刪改上文）

- **force 亦要 coordinator lease**：移除 `.github/workflows/daily-update.yml` 直接呼叫
  `fetch_biggo.py --force-batch` 嘅 bypass；force intent 改為 `AIRCON_BIGGO_FORCE_STAGE`
  經 `scripts/biggo_stage_runner.py` 執行，同樣要 CAS acquire lease、cooldown、budget、
  idempotency、needs_review、publication 檢查。`fetch_biggo.py` 全部 CLI 網絡入口
  （`--smoke`／`--price-batch`／`--force-batch`／單型號）一律 exit 2 fail closed，冇
  繞過 lease 嘅路徑。
- **實際 heartbeat**：`LeaseHeartbeat` 喺 smoke、batch（`run_price_batch`／
  `run_force_batch` 嘅 `should_abort` callback）、publication 全程運行；45 分鐘 lease、
  `maybe_renew()` 每 5 分鐘節流；lease 失效即停止提交新工作、唔發布，`finally` 保證
  stop＋join（停唔切當 lease lost），冇 orphan thread。
- **Retry-After 兩種標準格式**：`parse_retry_after` 同時支援 delta-seconds 同 HTTP-date；
  無法解析只用項目自身 48h fallback（basis `project-48h`），唔會推斷 provider quota reset。
- **語義取代**：舊文檔「批次保持完整 5 次 retry」記述由 D26 取代；approved C 為每 model
  最多 2 attempts、403／429 無即時 retry。
- **證據（E2）**：見 `docs/STATUS.md` §18.5；私人文檔 runbook 同步追加返修節。
- **邊界**：本返修冇執行 sudo／apt／system package／server／production／SSH／deploy／push／
  真實 BigGo；公開同私人 server 兩條線保持分離。

## D27 · GitHub Actions 與 EMSD CSV 修復（Frostar 解析／receipt 會計／D1-B coverage pending／CKAN resolver）

- **日期**：2026-09-29
- **狀態**：已實作候選（本機 E2）；未 commit、未 push、未 deploy；用戶已批准 R2/R3 範圍，交獨立
  驗收後由 Codex 決定最終本地 commit。
- **背景**：master daily run `36523360069`、`36490966449` EMSD 抓取成功，但 stage 1 官網核實
  以非零結束，令 BigGo 同發布被 skip。觀察到兩類問題：（1）`fetch_rasonic.py` 只認 `RC-` 型號，
  Frostar `FR-KS7/9/12/18` 四個官方頁雖然 HTTP 200 且 Product JSON-LD 完整，仍被判失敗；
  （2）`fetch_official.py` receipt 會計受 hitachi 列表頁影響，且舊 target `cafa-09crn8-pc2`
  （連字號）實測 404（正確 `cafa-09crn8pc2` 為 200）令每日重試同 stage 1 硬失敗。
  另外現行 D1-B 只在「所有腳本 rc=0」時才可 coverage pending，確認頁面已消失嘅情況會變硬失敗；
  EMSD 主來源仍用固定 direct URL，缺少官方目錄解析；daily 失敗時冇結構化 EMSD 證據留存。
- **選項**：
  - A：只把失敗目標剝走／改成硬編碼例外——會失去身份驗證、掩蓋真失敗，否決；
  - B（採用）：修 parser 同 receipt 契約，精準區分「確認頁面消失」同硬失敗；用官方 CKAN
    dataset 目錄解析 primary CSV（仍然保留逐頁 cross-check）；失敗時 always() 上載脫敏證據；
  - C：放寬門禁令 daily 直接跳過官網核實——唔可接受，否決。
- **決策**：採 B，範圍限定用戶批准嘅修復：
  1. **Frostar FR-KS**：`fetch_rasonic.py` 支援 `FR-KS`（同 `RC-` 一樣）；型號必須同時出現喺
     Product JSON-LD name 同 URL slug（正規化後）；身份唔完整、200 空白／登入／parser 例外
     一律硬失敗。舊「stale／truncated URL」假設已由 4 個 HTTP 200 官方頁實測否證。
  2. **CAFA stale target**：移除重複舊 slug `cafa-09crn8-pc2`（404）；保留 `cafa-09crn8pc2`
     （200），canonical 型號 cover 不變。
  3. **Receipt 契約**：`emit_fetch_receipt` 加 `skipped`、`failureReasons`、
     `coveragePendingModels／coveragePendingReasons`；嚴格驗證 counts／lists／covers 一致；
     hitachi 列表頁當 infra 前置，成功唔計型號 succeeded，失敗以 `HITACHI-LISTING:<url>`
     單位計入 failed（硬失敗）。唔准把 skipped 當 newly succeeded。
  4. **D1-B coverage pending 精準化**：只有 HTTP 404／410（且 URL 型號對得上 EMSD 登記）可以
     歸類 coverage pending；網絡、timeout、200 空白／登入、parser 例外、identity 不確定、
     receipt／output 不一致一律 fail-closed。pending 時 queue stage／models 原樣保留、舊規格
     保留、唔 advance、唔自動淘汰；只有純 coverage gap 可以繼續 EMSD daily 發布（公開 status
     顯示待核）。
  5. **CKAN resolver**：以 DATA.GOV.HK `package_show` dataset ID
     `hk-emsd-emsd1-meels-listed-models` 揀唯一 active Room Air Conditioners CSV resource
     （name／description、format CSV、state active、URL allowlist；唔靠 UUID——catalog 頁
     英／繁／簡 resource UUID 唔同）。request／final URL 都要 HTTPS +
     `www.emsd.gov.hk` + `/energylabel/files/*.csv`；HTML masquerade、schema 唔明、redirect
     去非 allowlist host 全部 fail-closed。目錄暫時不可用（網絡／5xx／429）才 fallback 上次
     批准 direct URL **重新抓新鮮 bytes**（唔用 cache／304 bytes）；目錄有回應但 URL／身份唔合
     契約即 fail closed、無 silent fallback。逐頁 paginated 來源仍是 mandatory cross-check。
  6. **Receipt 擴充**：公開 EMSD receipt 加 catalog dataset ID／resource ID／catalog URL／
     dataset page URL／resolved CSV URL／resolution time，以及 CSV ETag／Last-Modified／
     byteLength／SHA-256；`metadata.json` 內嵌 Schema 不變，`gen-metadata.py` 接受批准
     resolved URL（regex allowlist）並驗證 catalog block；私人 raw sink／raw byte hash 不變。
  7. **Daily 失敗證據**：以 `always()` 上載 EMSD diff／`emsd_receipt.json`／
     `emsd_raw_receipt.json` 同 official machine receipt／status；只含已脫敏檔，無 secrets／
     私人路徑。cron 00:30 HKT、trusted master／PR、pinned `ubuntu-24.04` 同 actions、BigGo
     coordinator gating 全部不變。
  8. **Attribution**：UI／README 加「資料目錄：DATA.GOV.HK；資料由機電工程署提供」；依 D24
     唔手動重生 `index.html`／PDF，由下一次 daily 全量重生。
- **原因**：官方頁仍然有效時必須解析而唔係跳過；只有可證實「頁面已消失」才可 pending；目錄解析
  令 primary CSV 來源可追溯至官方 catalog，同時保留獨立逐頁核對；失敗證據要機器可讀先可審計。
  一切改動唔降低 required 功能、Metadata Schema、成功標準或 fail-closed 門禁。
- **後果（分類）**：
  - `REQUIREMENT`：15 項 required 功能、protection、Metadata Schema（`metadata.json` 欄位不變）、
    成功標準、全部阻斷門禁及公開 attribution 無放寬；D1-B 只放行可證實嘅純 coverage gap。
  - `OBSERVED / E2`：完整 pytest **638 passed**（1 預期 duplicate-zip warning）；
    `feature-check.py --run-tests` 15 項／18 節點 passed；`extract_governance`／
    `validate_data`／`validate_metadata`／`check_public_privacy --mode worktree`／
    `git diff --check` 全 rc=0；四個 Frostar 頁同 CAFA 404 係 bounded read-only 官方頁核實
    （非 BigGo、非 EMSD 抓取）。
  - `OBSERVED / relay`：CKAN API 真實回應 shape 經 read-only relay 讀取（本機直連
    `data.gov.hk` TLS `UNEXPECTED_EOF_WHILE_READING`）：`success: true`、`result.name =
    hk-emsd-emsd1-meels-listed-models`、11 resources、Room Air Conditioners CSV active direct
    EMSD URL；正式直接驗證仍屬 UNKNOWN。
  - `UNKNOWN`：受信任 CI（E3）、任何部署（E4）、真實 CKAN live fetch（本地 TLS 被阻）、
    真實 BigGo、下次自然 daily 嘅 coverage pending 平台行為。
  - `BOUNDARY`：冇 commit／push／merge／deploy／tag／Release、冇觸發任何 GitHub Actions、
    冇操作 Secrets／environment、冇 SSH 生產或私人 server、冇改私人 repo、冇改
    `index.html`／PDF／EMSD CSV／`metadata.json` 生產生成物。
- **回滾**：本輪全部改動喺 `codex/dual-writer-coordination` 工作樹，未 commit；可用
  `git checkout -- <path>` 或 commit 後 `git revert` 還原。如需停用 CKAN resolver，可在
  `fetch_emsd.py` 直接使用 `dual.CSV_URL`（still fresh fetch）而同一次 revert 收據擴充；
  D1-B／receipt 欄位屬 additive，舊 receipt／舊 daily 格式仍可被 wrapper 拒絕（fail-closed），
  不存在資料遷移。

### D27 · 2026-09-29 返修（追加；不刪改上文）：Codex review 六項缺陷修正

- **背景**：Codex 對 D27 首版 diff 做獨立審查，發現六項具體缺陷：D1-B 判斷只睇
  `missing_coverage`（script pending 但已有其他覆蓋時仍然 advance）；`http_get` 用
  `urlopen` 自動跟 redirect，未跟之前冇驗證 target；CKAN resolver 冇驗 provider 身份，
  且 injected non-200 全部當 unavailable；`fetch_rasonic` 用 normalized substring 比對 URL，
  `FR-KS7` 會誤中 `FR-KS70`；daily `always()` 直接上載 checkout tracked receipt／status，
  早退時舊 bytes 會被當成本 run 證據；文檔需 append-only 修正。
- **決策（全部喺同一 D27 範圍內收緊，無新產品政策）**：
  1. **D1-B 判斷修正**：`run_official_batch` 分開兩個事實
     `missingQueueCoverage`（queue model 冇任何覆蓋）同 `scriptCoveragePending`
     （通過 `validate_marker` 嘅 receipt 明確 404／410 pending）；任何一項非空
     都 `decision=queue-kept-pending-coverage`、唔 advance、queue／舊規格原樣保留；
     `coveragePending` 為兩者 OR。只有 `markerValid is True` 嘅 receipt 才可貢獻
     covers／pending；invalid marker 只可以係 hard failure。`publish_official_status`
     加 `missingQueueCoverage`；UI pending hint 會列出 script pending 型號。
  2. **Redirect pre-validation**：`http_get` 新增 optional `validator`；自訂
     `_ValidatingRedirectHandler` 喺 `redirect_request` 內**先**驗證 target，唔合格即
     raise（保留 `CatalogInvalid` kind，唔會貶為 network_error）。CKAN request 帶
     `validate_catalog_api_url`（https＋`data.gov.hk`＋固定 path＋dataset query＋無
     userinfo／port／fragment）；CSV request 帶 `validate_official_csv_url`。同一
     allowlist 內嘅官方 redirect 可以照跟。Injected transport 嘅 `finalUrl` 一樣要過
     完整 identity（唔再只驗 hostname）。離線 loopback HTTP server 測試證明未批准
     redirect 目的地零請求、批准 redirect 正常跟隨。
  3. **CKAN provider 身份**：由先前 read-only relay 觀察到嘅真實 shape
     `result.organization.name='hk-emsd'`；resolver 要求 `organization` 係 dict 且
     `name == 'hk-emsd'`，缺失／唔同／非 dict 即 `CatalogInvalid`（fail closed）。
     Injected response `status != 200` 分支改用 `_catalog_unavailable` 同一政策：
     400／401／403／404 等即硬失敗；只有網絡／5xx／408／425／429 可以 bounded retry
     之後當 `CatalogUnavailable`。200 但 invalid schema／HTML／歧義 resource 行為不變
     （全部 fail closed）。
  4. **型號身份精確比對**：`fetch_rasonic.parse_product_page` 由 substring 改為
     `_model_from_url` 抽出嘅精確 URL slug token 同 Product JSON-LD name token
     canonical 相等。`FR-KS7` vs `FR-KS70`、`RC-XG7` vs `RC-XG70` 加入硬失敗回歸；
     四個真實 Frostar slug 保持成功。crawl_utils.fetch 冇 redirect final URL 證據，
     所以 evidence 註明 identity = configured URL slug + Product JSON-LD name
     （`identityBasis`、`configuredUrl`、`urlSlugToken`），唔聲稱 final URL 已驗證。
  5. **Per-run evidence staging**：新增 `scripts/stage_run_evidence.py`。EMSD fetch 先寫
     `$RUNNER_TEMP/emsd-evidence/run-start.utc`，失敗／成功都經 staging helper：只複製
     存在、JSON object、有 UTC timestamp（`retrievedAt`／`startedAt`／`finishedAt`／
     `generatedAt`）、timestamp >= run start（容忍 clock skew）且非未來嘅檔；舊
     checkout receipt／status 只會標記 excluded。`run-status.json` 記錄 source rc、
     run id、commit、逐檔 staged／excluded 原因；source rc 非零且零檔 staged 時寫
     `source-rc-N-and-no-current-run-evidence` error。Official 同樣 stage 本 run
     receipt／status（official_stage1／2 output rc；projection 成功先寫 current status）。
     Workflow artifact 只上載兩個 RUNNER_TEMP staging dir；唔會刪除／改寫生產 receipt，
     private sink 路徑／token／objectId 唔入 staging。
  6. **文檔追加**：本節、`docs/STATUS.md` §19.7、`CHANGELOG.md` 返修條目；舊記述原文保留。
- **原因**：六項都係 fail-closed 契約缺口或證據完整性問題；修正只收緊，冇降低任何
  required 功能、Metadata Schema、成功標準或門禁。
- **後果（分類）**：
  - `REQUIREMENT`：D1-B、redirect allowlist、EMSD provider 身份、identity 精確比對、
    證據新鮮度、Metadata Schema、required 功能全部無放寬。
  - `OBSERVED / E2`：完整 pytest **664 passed**（1 預期 duplicate-zip warning）；
    `feature-check.py --run-tests` 15 項／18 節點 passed；machine acceptance
    **7/7 gates rc=0**（report `D:\tmp\aircon-acceptance-20260929-r2.json`，
    PYTEST log 664 passed）；`extract_governance`／`validate_data`／
    `validate_metadata`／`check_public_privacy --mode worktree`／`git diff --check`
    全 rc=0；新增／更新測試全部離線（loopback HTTP、fake transport、fake scripts）。
  - `UNKNOWN`：同 D27 主文（E3／E4、live CKAN 直接驗證、真實 BigGo）不變。
  - `BOUNDARY`：未 commit／push／deploy、未觸發任何 GitHub Actions、未呼叫真實
    BigGo／EMSD、未改私人 repo、未改生產生成物（`index.html`／`metadata.json`／PDF／CSV）。
- **回滾**：同 D27 主文；新增欄位全部 additive，舊 receipt 仍會被 wrapper fail-closed
  拒絕（唔存在弱化路徑）。

### D27 · 2026-09-29 第二次返修（追加；不刪改上文）：provenance baseline＋fetch_official pending 身份

- **背景**：Codex 第二輪 focused review 發現兩個 acceptance gap：
  （1）`stage_run_evidence` 只靠 `timestamp >= runStart − 300s`，前一次手動 run 喺 5 分鐘內嘅
  tracked receipt／status 會被當成本 run 證據；timestamp 單獨唔證明 provenance；
  （2）D27 要求 404／410 coverage pending 必須有 URL target identity＋EMSD 登記型號，但
  `fetch_official.py` 對 Panasonic／HITACHI／COMFEE 任何 404 都直接 pending，冇核對登記同
  URL 型號身份。
- **決策（只收緊，無新產品政策）**：
  1. **Pre-step baseline provenance**：`stage_run_evidence.py` 加
     `--write-baseline`（source step 前捕捉每個候選檔 presence＋SHA-256；缺席都記錄
     `present: false`）。staging 必須傳 `--baseline`，只有同時滿足：
     存在／regular file（拒 symlink）／JSON object／有 UTC timestamp／
     `timestamp >= runStartedAt`（**冇 skew 容忍，pre-run 1 秒都排除**）／非未來／
     hash 同 baseline 唔同（baseline absent 或 content 有變）／如 receipt 有
     `runId`／`githubRunId` 而 `--run-id` 有提供就必須相等；先會 stage。baseline 檔寫喺
     `$RUNNER_TEMP/evidence-baseline/`（evidence dir 以外），唔會上載。`run-status.json`
     逐檔列出 `before-run-start`／`baseline-unchanged`／`run-id-mismatch`／`missing` 等原因，
     source rc 非零且零檔 staged 時寫 error record；baseline 缺失／損毀即 rc=2 fail closed。
     Workflow EMSD step 同 official pre-step 各自寫 baseline；upload path 只含
     `emsd-evidence/`／`official-evidence/`。
  2. **fetch_official pending 身份**：`main` 每次 batch 只 load 一次 EMSD 已登記型號 canonical
     set（`emsd_registered()`，可 inject）。`pending_eligible(reason, model, url_token,
     registered)` 要求 reason 係 404／410、model 喺登記 set、URL token 同 model canonical
     相等：Panasonic 由 URL 抽 `CW-…` token；HITACHI 由 constructed product URL 抽
     `RA-…`／`RAW-…` token；COMFEE 用 URL 最後 path segment（即 slug）。三者缺一即
     `product-hard-failure`（唔會 pending）；非 404 成功完全唔受登記檢查影響；CAFA 重複
     stale slug 移除保持不變。
  3. **文檔**：本節、`docs/STATUS.md` §19.8、`CHANGELOG.md` 第二次返修條目（append-only）。
- **原因**：timestamp 可以被舊檔／人為更新蒙混；provenance 需要 pre-step baseline hash＋
  run window＋run id；coverage pending 係「已確認官方頁面消失」嘅窄分類，必須先確定
  target 身份同 EMSD 登記，否則一律硬失敗。
- **後果（分類）**：
  - `REQUIREMENT`：D1-B、來源身份、fail-closed 門禁、Metadata Schema、required 功能不變。
  - `OBSERVED / E2`：完整 pytest **670 passed**（1 預期 duplicate-zip warning）；
    新增 staging provenance 回歸（pre-run 1 秒、baseline hash 相同但 timestamp 入 window、
    fresh changed content、run-id mismatch、baseline 缺失／損毀 fail closed、baseline 唔入
    artifact）＋fetch_official pending 身份回歸（registered 404 pending、URL mismatch／
    unregistered hard fail、非 404 成功不受影響）；全部離線 fake／fixture。
  - `UNKNOWN`：E3／E4、live CKAN 直接驗證、真實 BigGo／EMSD，同 D27 主文。
  - `BOUNDARY`：未 commit／push／deploy、未觸發 Actions、未改私人 repo、未改生產生成物。
- **回滾**：同 D27 主文；baseline／pending 欄位全部 additive，舊 receipt 仍被 wrapper
  fail-closed 拒絕（冇弱化路徑）。

## D28 · live CKAN CSV schema 修復：真實 29 欄 header／供應範圍／值 canonicalization（工程修復；append-only）

- **背景（OBSERVED / E3）**：PR #19 merge（merge commit `4d364ace36bec28888b92a201d17771a1b051df3`）
  後，手動 dispatch 一次 production daily（run **36590838333**、`workflow_dispatch`、master `4d364ac`、
  `force_price_batch=false`）喺 step 10「抓取 EMSD + 新機偵測」fail-closed（`kind=schema`）；
  BigGo step skipped（`prices_meta.json` 冇 `price_batch_start` → inactive，零 BigGo 呼叫）；
  冇 data commit。脫敏 diff（artifact 11043608164）顯示 CKAN resolver 成功
  （dataset `hk-emsd-emsd1-meels-listed-models`、resource `2944ffac-4bb3-4240-a5f8-d902d0531b20`、
  `https://www.emsd.gov.hk/energylabel/files/meels_rac.csv`），但四個受治理欄位無 alias：
  `missing=['brand','cspf','hspf','provider']`；paginated 來源完整（37 頁、1,816 行）。
- **真實 CSV shape（OBSERVED；受信任 CI live bytes＋read-only relay 覆核）**：29 欄英文 header，
  包括 `Reference Number`、`Year`、`Brand Traditional Chinese`、`Model`、`Refrigerant`、
  `Energy Efficiency Grade Cooling (1 to 5)`、`Annual Energy Consumption Cooling (kWh)`、
  `Cooling Capacity (kW)`、`Cooling Seasonal Performance Factor (CSPF)`、`Inverter`、
  `Heating Seasonal Performance Factor (HSPF)`、`Information Provider Traditional Chinese`、
  `Product being Supplied by Information Provider`（值：Yes／No／No Information）。
  `Supplied=Yes` 共 1,816 行（同當日 paginated 1,816 行一致）；`No`／`No Information` 歷史登記
  1,357 行唔屬現行可比對範圍。
- **決定（工程修復；唔放寬任何門禁）**：
  1. header alias 只加觀測到嘅真實欄位：`brandtraditionalchinese`、
     `informationprovidertraditionalchinese`、`coolingseasonalperformancefactorcspf`、
     `heatingseasonalperformancefactorhspf`；英文／簡體 brand／provider 欄唔用（避免同
     paginated 繁中值產生假 mismatch）。
  2. 要求唯一 `Product being Supplied by Information Provider` 欄；只取 `Yes` 行做 primary
     同 cross-check 範圍；欄缺失／重複或值未知即 `SourceSchemaError`（fail-closed）。
  3. CSV primary 值轉受治理生產表示：`inverter` 只接受 Y／N 等價 token → 是／否，未知 token
     fail-closed；`heatingGrade` 空 → `不適用`；供暖數值欄空 → `—`（同歷史 paginated 官方
     sentinel 等價，符合 `validate_data.py` 契約）。
- **離線證據（OBSERVED / E2；relay bytes 只作診斷，唔入 repo）**：live shape 解析 → 1,816 行、
  排除 1,357；同 repo paginated 快照（2026-09-27、1,834 行）交集 1,813 行 **0 值 mismatch**，
  差異只係 21 行過期＋3 行新增嘅兩日時間差。
- **測試**：新增 live 29 欄 header 回歸（supplied 範圍／繁中欄選擇／inverter 是-否／供暖
  sentinel／未知 token 或欄位缺失 fail-closed）；三個 fetch_emsd 離線 fixture（receipt／
  raw receipt／private sink）補真實供應欄。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、fail-closed 路徑全部不變；
    production 輸出值表示同歷史 paginated 等價。
  - `OBSERVED / E2`：完整 pytest **674 passed**（1 預期 duplicate-zip warning）；7/7
    acceptance gates rc=0；feature-check 15 項／18 節點；validate_data 1,834 行；
    validate_metadata version 1.2.9／datasetDate 2026-09-28。
  - `UNKNOWN`：修復後第二次 production daily（自然 schedule）尚未執行；E4 未觀察。
- **回滾**：revert 本 commit；失敗路徑本身保留舊 CSV（雙來源 fail-closed），舊資料不受影響。

## D29 · BigGo stage bundle 交易與本地 apply（P0 修復；append-only）

- **日期**：2026-09-30
- **狀態**：已實作候選（本機 E2）；未 push／未開 PR／未 deploy；交獨立驗收
- **背景**：D26 已批准雙 writer／共享憑證 CAS lease 架構，但 `fetch_biggo` 網絡階段會在
  coordinator publish 前寫本地四檔並推進 idx；`completed-idempotent` 唔 sync；active
  loser 只寫 snapshot；`acquire` 可被陳舊 writer 倒退高 stage；snapshot-only 無法重播
  tracking／blacklist effects（2026-09-30 離線重現）。
- **選項**：
  - A：維持現狀＋文件警告——否決：publish 失敗即 remote／本地分歧，且倒退風險仍在；
  - B（採用）：網絡階段零寫入；write-ahead intent；immutable v2 bundle（base/new
    snapshot＋stage-result＋manifest）；CAS completed 後 local apply；acquire 單調守門；
    CI guard／紅 alert；
  - C：改 coordinator state schema version 或 metadata schema——超出本輪 P0 且影響私人
    repo／公開 metadata，否決。
- **決策**：採 B。實作全部在公開 repo；coordinator state 只加 optional 欄位
  （`callsMayHaveStarted`／`intentAt`／`stageResultHash`／`stageResultPath`／`bundleHash`），
  schemaVersion 維持 1；舊 v1 snapshot-only manifest 只會被拒絕自動 apply（人手處理）。
- **原因**：符合 D26「已呼叫但 snapshot 未確定 → needs_review、禁止自動重跑、local
  stage 先決」同「未確認價格唔可以上線」；所有效果可 hash 驗證、可重入、stage 只前進。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、fail-closed 門禁、
    403/429／cooldown／budget／48h 規則同 inactive 零 API 全部不變。
  - `OBSERVED / E2`：7/7 acceptance gates rc=0；pytest 717 passed；feature-check
    15 項／18 節點；離線故障注入矩陣見 `docs/STATUS.md` §20。
  - `UNKNOWN`：E3／E4、私人 coordinator 現有 state／legacy bundle、merge／部署未執行。
  - `BOUNDARY`：未呼叫真實 BigGo／EMSD、未改私人 repo／Secrets、未改 metadata／版本號／
    `index.html`／PDF／CSV／價格快照（除測試 temp fixture）。
- **回滾**：revert 本輪 commit；新 runner 對舊 v1 manifest 會拒絕自動 apply 並發 alert；
  如需完全回舊行為，需同時 revert `fetch_biggo.py`／`model_lifecycle.py`／
  `biggo_coordinator.py`／`biggo_stage_runner.py`／workflow 同新增模組。

### D29 · 2026-09-30 首輪返修（追加；不刪改上文）

- **背景**：Codex 對 D29 首版 P0 交易實作做獨立審查，發現四類阻斷：stage 間 intent／
  attempts 污染；same-owner renewed 可重跑；daily 尾步 alerts 會令 source run failure
  而 `verify_deploy_request` 拒絕 Pages deploy；budget 只在 batch 前查一次、冇逐請求
  reserve，且 blacklist review 額外請求未計入 cap；另有 `_needs_review` TypeError、
  status artifact 跌返 error、review 缺 `should_abort`。
- **決定（全部喺同一 D29 範圍內收緊，無新產品政策）**：
  1. `_takeover_state` 只對同一 cycle/stage expired active takeover 保留 intent／attempts；
     合法前進（完成前一 stage／新 cycle）fresh intent=false、attempts=0；completed commit
     清 intent；cooldownUntil 保留為跨 stage 事實。
  2. same-owner active 帶 durable intent → `winner-intent`；runner 只可完整 bundle adopt
     或 needs_review。
  3. BigGo 警報移至獨立只讀 `workflow_run` workflow；source 嚴格限制本 repo／master／
     schedule 或 workflow_dispatch／40-hex head_sha／非 PR；success 或 failure 均可核實
     （alert 不 deploy）；缺 artifact fail-closed。daily 保持 success、Pages source success
     判定不變；未放寬 `verify_deploy_request`。
  4. 新增 thread-safe per-request `RequestLimiter`；search cap = 1 smoke + 2×（batch＋
     review 型號數）；token 1/stage；provider 80% 逐請求；refusal = 網絡錯誤／partial、
     不當 clean miss。runner 與 fetch 共用 `stage_workload`／`force_workload` 定義。
  5. `_needs_review` 經 emit 記精確 status／reason／requests；review incremental submit＋
     `should_abort`＋cancel futures。
- **原因**：維持 D26／D29 的「未確認唔可上線、交易確認後才 apply、fail-closed」契約；
  警報可見性唔可以反過來破壞 daily 發布與 Pages 信任閘門。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、門禁全部不變；
    `verify_deploy_request` 與 GATE-08 未被放寬。
  - `OBSERVED / E2`：7/7 acceptance gates rc=0；pytest **752 passed**；feature-check
    15 項／18 節點；離線故障注入見 `docs/STATUS.md` §21。
  - `UNKNOWN`：E3／E4、merge／部署、alert workflow 實際 workflow_run 觸發未觀察。
  - `BOUNDARY`：未 push／deploy／呼叫真實 BigGo／改私人 repo／改生產資料。
- **回滾**：沿 D29 主文；新增 limiter／alert workflow 可獨立 revert（alert workflow
  revert 後 daily 不受影響，只失去獨立紅色信號）。

### D29 · 2026-09-30 第二輪返修（追加；不刪改上文）

- **背景**：Codex 第二輪審查發現：（a）`acquire` 可讓新 stage 越過 remote 未完成
  （active／idle）stage，連帶清走 expired active 的 durable intent；（b）providerQuotaLimit
  的 80% 只按 stage-local attempts 計，跨 stage 可超額，且冇 window ledger；（c）需確認
  apply／stage guard 只在 remote completed 生效、blocked-incomplete 紅色但唔阻無 diff 的
  EMSD daily。
- **決定（同一 D29 範圍收緊，無新產品政策）**：
  1. `acquire` 未完成（active／idle）或 needs_review 的 remote stage 一律不可跨越：
     同 stage 安全重入／adopt／needs-review 除外；新 result `blocked-incomplete`
     （0 PUT、0 本地寫、0 BigGo、紅 alert）；正常／force 一視同仁，冇新 cycle 旁路。
     `stale-writer` 舊 result 不再由 acquire 產生（runner 保留兼容分支）。
  2. provider quota／window 有配置（env 或 legacy state）→
     `blocked-quota-window-unsupported`，零 BigGo；`budget()` 標示
     `providerWindowAccounting='unsupported'`；runner 不再把 stage-local 80% 傳入
     limiter。provider window 在未有共享、持久、可核對 ledger 前維持 UNKNOWN／
     fail-closed；48h 冷卻只係項目 fallback。
  3. apply／immediate local writes 只在 remote completed 之後；`blocked-incomplete` 無
     canonical diff，daily guard pass（EMSD 保持 success），獨立 alert workflow 紅；
     `verify_deploy_request`、privacy、GATE 未改。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、Pages source success 閘門、
    privacy／GATE 全部不變。
  - `OBSERVED / E2`：7/7 acceptance gates rc=0；pytest **765 passed**；feature-check
    15 項／18 節點；離線故障注入見 `docs/STATUS.md` §22。
  - `UNKNOWN`：E3／E4、merge／部署、provider window 真實 window 用量（維持 fail-closed）。
  - `BOUNDARY`：未 push／deploy／呼叫真實 BigGo／改私人 repo／改生產資料。
- **回滾**：沿 D29 主文；acquire 收緊可獨立 revert（但會回復越 stage 風險）。

### D29 · 2026-09-30 第二輪返修｜證據補記（追加；不刪改上文）

- 加入 active stage1 由其他 owner 有效 lease 持有（跨 stage 只可 `lost`、0 PUT）的回歸後，
  最終 `run_acceptance.py` 7/7 gates rc=0、PYTEST **766 passed**（1 預期 duplicate-zip
  warning，含 browser smoke 12）；上文 765 為較早一次 run 實數。

### D29 · 2026-09-30 第三輪返修（append-only；不刪改上文）

- **背景**：Codex 第三輪發現：（a）`acquire` 只拒倒退、不拒跳級，remote completed
  stage1 可跳去 stage3；較新 cycle 可在上一 cycle 未完成時開始；（b）fetch 的
  `shutdown(wait=False)` 會在 running worker 仍存活時返回，runner 随即清除 limiter／
  abort context，worker 可能無保護再 outbound。
- **決定**：
  1. completed remote 加 7-stage gap 守門：同日期只准 remote.stage+1；較新 cycle 需
     上一 cycle 已 7/7 且新 cycle 由 stage1 開始；否則 `blocked-stage-gap`（0 PUT、
     0 BigGo、0 apply、紅 alert）；force 係人手 intent，completed 後可另行處理，但唔可以
     繞過 active／idle／needs_review；較舊請求維持 `completed-ahead`。
  2. fetch 三個批次函式改有界 incremental submit + `wait(FIRST_COMPLETED)` +
     `shutdown(wait=True, cancel_futures=True)`；abort-aware cooldown／pace／retry
     sleep；token lock 單次 auth；REQUEST_STATS 加鎖；`_reserve` 每個 outbound 前執行。
     Runner 只會在 fetch 返回後清除共享 context。
- **原因**：維持 7-day 批次完整性與每請求 hard cap／abort 保護；唔可以出現無保
  護的 orphan worker outbound。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、Pages source success、
    privacy／GATE、provider fail-closed（§22）全部不變。
  - `OBSERVED / E2`：7/7 acceptance gates rc=0；pytest **784 passed**；feature-check
    15 項／18 節點；離線 worker／gap 故障注入全 pass（見 `docs/STATUS.md` §23）。
  - `UNKNOWN`：E3／E4、merge／部署未執行；in-flight urlopen 不可中斷屬已知界限。
  - `BOUNDARY`：未 push／deploy／呼叫真實 BigGo／改私人 repo／改生產資料。
- **回滾**：沿 D29 主文；gap 守門與 worker wait 契約可獨立 revert（但會回復跳級／
  orphan worker 風險）。

### D29 · 2026-09-30 第四輪返修（force cycle 邊界；append-only）

- **背景**：Codex 發現 `_stage_gap_reason`／`_guard_regression` 對 force 直接放行，
  remote completed normal 1/7 可被 force 覆寫，force completed 又可被同日／較舊 normal
  奪回，月度 cycle 進度遺失。
- **決定（單一 state 下的最小 fail-closed 轉移，無 schema 變更）**：
  1. remote normal N/7（N<7）→ 拒絕任何 force：`blocked-force-cycle-incomplete`。
  2. remote normal 7/7 → 只准日期不早於 normal cycle 日期的 force；較舊 →
     `blocked-stale`；同日若原 force cycle 已 completed → `completed-idempotent`。
  3. remote force YYYY-MM-DD → 同日／較舊 normal 或較舊 force → `blocked-stale`；
     較新 normal 只准 stage1（否則 `blocked-stage-gap`）；較新 force（人手 intent）
     允許；首次無 state 的 force 允許。
  4. active／idle／needs_review 一切照舊 fail-closed。
- **原因**：force 只授權一次受 lease 查價，不代表授權放棄未完成月度 cycle；單一 state
  下唯一安全做法係只喺 normal 7/7 或無 previous state 時容許 force，並且禁止時序倒退。
- **後果（分類）**：
  - `REQUIREMENT`：metadata Schema、required 功能、成功標準、Pages source success、
    privacy／GATE、provider fail-closed、stage/worker 契約全部不變；冇降門禁。
  - `OBSERVED / E2`：7/7 acceptance gates rc=0；pytest **800 passed**；feature-check
    15 項／18 節點；force boundary 離線矩陣見 `docs/STATUS.md` §24。
  - `UNKNOWN`：E3／E4、merge／部署、真實 force run 未觀察。
  - `BOUNDARY`：未 push／deploy／呼叫真實 BigGo／改私人 repo／改生產資料。
- **回滾**：沿 D29 主文；force 邊界可獨立 revert（但會回復覆寫未完成 cycle 的風險）。
