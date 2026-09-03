# 決策記錄（Decisions）

> 本文件記錄項目發展過程中的重要決策，包括人類決策項（由項目負責人決定）與因技術限制導致的方案轉向。
> 每項決策均註明背景、選項、決策結果與原因，供日後追溯。
> 更新機制見 `docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源）。

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
- **備註**：現有 `model_blacklist.json`（1,103 個型號）即屬此類一次性本地驗證數據，見 D9。

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
- **狀態**：已實行（待人類提供憑證）
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
- **狀態**：已決定（PR-3 實施中；metadata Schema 更新同 load_registrations 喺 PR-3 落地）
- **背景**：EMSD CSV 同一型號可有多個登記記錄（1,863 registrations / 1,814 models），舊 `load_models()` 靜默「第一筆勝出」，無審計規則。
- **選項**：
  - A：保留全部登記 + 另出 canonical product view；metadata 分開記錄計數
  - B：維持首筆勝出
- **決策**：選 A；CSV 保留全部 registration，`crawl_utils.load_registrations()` 回傳全部登記、`load_models()` 按 canonical key 去重回傳 product view；metadata.json Schema 新增 optional `rawRecordCount`／`registrationCount`／`modelCount`（向後兼容，CI 未傳就唔寫）。
- **原因**：用戶選定 A；令 1,863 registrations 與 1,814 models 嘅關係可審計。
- **後果**：治理文檔 `AIRCON_METADATA_SCHEMA_V1` 區塊更新（schemaVersion 維持 1.0.0、新欄位 optional）；`validate_metadata.py` 接受新欄位。

## D13 · 價錢快照 key：M1 保留原始型號 key（技術範圍決策）

- **日期**：2026-09-03
- **狀態**：已實行
- **背景**：canonical key 全面統一（D11）時，`biggo_prices.json`（742 項）等價錢快照亦以型號字串做 key；全量遷移會波及 generate_html 價格 lookup 與多個 loader。
- **決策**：M1 只遷移黑名單、tracking 同保護集；價錢快照（biggo_prices.json / prices.json / gemini_prices.json）保留原始型號字串 key，需要時以 `norm_model` helper 雙讀。全量價錢 key 遷移延後到後續 PR。
- **原因**：控制 M1 風險同 diff 大小；價錢快照唔參與淘汰／停售判定。
- **後果**：黑名單復核復活時新價會以 norm 型號 key 寫入 biggo_prices.json（該等型號頁面唔顯示，只作復核證據保留）。

---

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
