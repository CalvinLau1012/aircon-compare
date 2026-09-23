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
    provider 選擇／Secret／首個 live snapshot、D3 required reviewer、D6 self-host 同步。
- **回滾**：本輪全部改動仍喺 branch；可 revert 對應 commit；唔會回退 production
  metadata／runtime snapshots。

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
