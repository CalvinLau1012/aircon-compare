# ADR-004：可插拔私人 raw sink adapter（D7-A 返修；live 啟用未批准）

- **日期**：2026-09-23
- **狀態**：**本機候選已實作**（PR #10 返修）；remote adapter 只有 fake HTTP 測試，
  **未**建立任何 Release、未上傳任何資產、未取得 live 證據。真正遠端方案選擇同
  Secret／平台設定仍屬人類決策（UNKNOWN）。
- **背景**：D7-A 要求 EMSD 原始 response bytes 私人保存 90 日、公開只留 hash receipt。
  PR #10 審查發現原本只有 local sink 目錄，runner 目錄唔係 90 日 durable storage；
  而且 remote provider 未定，唔可以在 PR 階段亂配 Secret 或造假 live 證據。
- **決策**：
  - 抽出 `scripts/private_raw_sink.py`，提供共同介面：
    - `local-dir`：現行目錄 sink；receipt 如實標示 `durableRemote: false`、
      `adapter: local-dir`，唔可以當係 remote 完成。
    - `github-release-asset`（候選）：Private GitHub Release asset；供應端
      `GET /repos/{repo}` 必須回讀 `private: true`；同名資產拒絕覆蓋；上傳後即時
      下載核對 sha256 + size；只清理同名前綴、超過 90 日、非 keep 嘅資產。
  - 單一入口 `persist_raw_archive`：remote 配置不完整（有 repo 冇 token 等）即
    raise，唔會靜默降級；`require` 模式未配置即阻斷，亦唔會發成功 CSV 收據；
    upload／下載核驗失敗一律 raise，成功 receipt 唔會出現。
  - 公開 receipt 只記 adapter、`verified`、`durableRemote`、`retentionDays`，
    唔記 token／私人 URL／raw bytes；raw bytes 只存在記憶體、repo 外 temp 或
    私人 sink。
  - local sink 防 escape：sink／run 目錄係 symlink 或 Windows junction 即拒寫；
    cleanup 只刪 sink 內、`run-` 前綴、realpath 無逃逸、非 link-like 嘅目錄。
  - raw 舊證據唔會冒充本次：收據只在本次 persist 成功後寫；公開封包只有在
    `datasetHash` 同 CSV 收據 `rawReceiptHash` 一致時才收錄 raw receipt。
- **原因**：將「可插拔實作」同「平台啟用」分開：代碼同測試可以做足，但未經人類選定
  provider 前唔可以創造 live 資產或 Secret。呢個保持 D7-A fail-closed 同時唔虛報
  remote 完成。
- **後果／啟用前置（平台待辦）**：
  1. 人類選定 remote provider（GitHub Release asset 只係候選）並提供最小權限 token；
  2. 喺 repo Secret 設 `AIRCON_EMSD_RAW_REMOTE_REPO`／`AIRCON_EMSD_RAW_REMOTE_TOKEN`
     （及可選 tag／retention）；
  3. 設 `AIRCON_EMSD_REQUIRE_RAW_SINK=1` 作正式阻斷；
  4. 首個 live snapshot 之後，人工核對 provider 回讀、下載 hash、90 日 retention 同
     raw bytes 無出現在公開 log／artifact；
  5. runbook 見 `docs/PRIVATE_RAW_SINK_RUNBOOK.md`。
  未完成以上步驟前，daily 保持非 require：唔寫 raw receipt，亦唔會將 local adapter
  講成 remote 完成（STATUS 記 UNKNOWN）。
- **回滾**：revert 相應 commit；local sink 舊介面（`AIRCON_EMSD_RAW_SINK_DIR`）仍
  可用，公開 CSV／metadata 流程不受影響。
