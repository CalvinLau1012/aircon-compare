# ADR-001：原始 EMSD HTTP response 快照與原始 hash（候選設計）

- **日期**：2026-09-22
- **狀態**：**已由 D7-A 人類批准並實作本機候選（E2）**；未部署、未設定真正 private sink Secret、未產生首個 live raw snapshot。
- **背景**：治理 §6.2 要求每個資料快照記錄「原始回應 bytes 的 SHA-256」；現時
  `emsd_receipt.json` 只記錄轉換後 CSV 的 `datasetHash`（以及頁數、totalRows），
  並無保存原始 HTTP response bytes 或其 hash。兩者唔可以互相冒充：CSV hash 證明
  轉換後 bytes 一致，但不能證明伺服器當時實際回傳內容（後者可被中間層改寫或
  官方頁面格式變更影響）。
- **問題**：
  1. 原始 HTML 為 37 頁、合共數 MB；保存全部 bytes 會增加 repo／volume 體積同
     公開私隱面；只保存逐頁 hash 唔可以離線重驗轉換。
  2. 保存位置（repo、volume、私人備份）、保留期、清理策略未定。
  3. 官方頁面內容可能含非資料元件（導覽、追蹤），直接入公開 repo 亦有授權疑問。
- **候選方案**：
  - A：逐頁保存 response bytes 到 `emsd_raw/<UTC>-p<NN>.html` + `raw_receipt.json`
    （逐頁 sha256、總 hash），公開 repo 只存 hash receipt，bytes 存 volume／私人備份。
  - B：只保存逐頁 sha256＋Content-Length＋Last-Modified，唔存 bytes（可偵測中途
    改變但唔可重放）。
  - C：維持現狀（只 CSV hash），文檔明確標示證據上限。
- **建議**：A（最少保存 hash receipt，bytes 可選），但需要人類決定保留期、儲存位置、
  公開範圍與清理策略；實作時必須：
  - `fetch_emsd.py` 只喺整批成功後原子提交 raw receipt；失敗／中止不能寫半套；
  - `gen-metadata.py` 可以選擇驗證 raw receipt hash，但 **唔可以** 用 CSV hash 冒充原始 hash；
  - metadata Schema 新增欄位屬破壞性／擴充決定，需 Code Owner 批准。
- **後果（分類）**：
  - REQUIREMENT：§6.2 仍適用；現時缺口屬 `UNKNOWN`／未實作，唔可以宣稱已合規。
  - OBSERVED：目前只有 CSV `datasetHash`，冇原始 response hash。
  - 未決定：方案、Schema、保留與清理。
- **回滾**：本 ADR 未改任何程式或資料；如採納後需回滾，移除新增 raw 檔與欄位即可
  （維持向後兼容，舊 metadata 仍可讀）。

## D7-A 已批准實作補充（2026-09-22）

- `fetch_emsd.py` 保存每頁實際接收 bytes 同 `byteLength`／`sha256`／可用 HTTP
  `Last-Modified`／`ETag`；raw receipt 只在整批抓取、解析、資料提交全部成功後原子寫入
  `emsd_raw_receipt.json`，只含 hash／metadata。
- 成功 CSV `emsd_receipt.json` 回寫 `rawReceiptHash`；raw receipt 內有 CSV `datasetHash`，
  兩份收據雙向引用，唔會用 CSV hash 冒充 raw bytes hash。
- 私人 bytes 經 `persist_raw_archive()` 寫入 repo 外 sink；require 模式 write 失敗即阻斷，
  唔會發布一個聲稱已保存但 private object 唔存在嘅 receipt；90 日 retention 邊界已測。
- PR 只可用 deterministic fixtures／假 HTTP／假 sink；真正 fine-grained token Secret、
  私人 Release asset 上載同首個 live snapshot 屬 merge 後 Phase D 平台驗收（UNKNOWN）。
- 公開 Git worktree／index／commit／Pages artifact／普通公開 Actions artifact 不會包含 raw
  HTML／archive；`stage_artifacts.py` 只 allowlist hash receipt。
