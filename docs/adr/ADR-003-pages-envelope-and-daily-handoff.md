# ADR-003：Pages 部署封包分層與 daily→Pages 真實銜接（D2-A 返修）

- **日期**：2026-09-23
- **狀態**：**本機候選已實作**（PR #10 返修）；未 merge、未部署、未跑受信任 CI E3／live E4。
  設計屬已批准 D2-A 嘅實作細節，非新產品政策。
- **背景**：
  1. PR #10 審查發現 Pages artifact 只包含 `deploy_payload.json` 嘅 payload 檔案，
     最終 `metadata.json` 冇部署；頁面／postdeploy 需要線上 metadata 先可以一致性核對。
     但 metadata 內含 `releasePayloadHash`，唔可以將 metadata 放入自己嘅 hash 範圍。
  2. 每日更新 workflow 用 checkout 預設 `GITHUB_TOKEN` 執行 `git push`；GitHub 文件
     明確呢類 push 唔會觸發 `push` workflow，所以新 daily commit 唔會自動部署。
- **選項（封包）**：
  - A：將 `metadata.json` 加入 `deploy_payload.json`，再排除 hash 自引用欄位。缺點：
    改變 D14 語義，metadata 生成／驗證次序會出現循環。
  - B：保持 `deploy_payload.json` 為唯一 `releasePayloadHash` 範圍，新增
    `deploy_envelope.json` 作為實際部署清單（payload + metadata.json + 一致嘅公開
    sidecar）。**採用**。
- **選項（daily→Pages 銜接）**：
  - A：reusable workflow 由 daily 直接呼叫。缺點：daily 需要 `pages:write` /
    `id-token:write` 權限，權限面擴大；且呼叫 run 嘅 `GITHUB_SHA` 係 push 前 commit。
  - B：`repository_dispatch`（GitHub 文件確認 GITHUB_TOKEN 嘅例外）帶精確
    `commit` + `sourceRunId`；Pages workflow checkout 該 commit，用 GitHub API 有界
    polling 核實 source run completed + success、event 可信、`head_branch=master`、
    `run_attempt` 精確、workflow path 係 `.github/workflows/daily-update.yml`，而且
    部署 commit 只有一個 parent 且等於 source run `head_sha`，`metadata.json` 嘅
    `workflowRunId`／`commit` 亦對應。**採用**。
- **決策**：
  - `deploy_envelope.json`：`requiredFiles` = payload 全部 + `metadata.json`；
    `optionalFiles` = 公開收據／status，只有同本次 `datasetHash`（同 CSV 收據
    `rawReceiptHash`）一致才收錄；舊／錯配 raw receipt 會 skip 並記錄原因，唔會
    當成本次證據發布。
  - `build_pages_artifact.py`：封包前驗完整 Schema、`version == models_data.VERSION`、
    payload hash 重算、CSV hash、record 計數；錯配即失敗。輸出目錄拒 repo 根／祖先、
    `.git`、link／junction 父層、與輸入重疊、未封印既有資料目錄；用 staging +
    安全替換，驗證失敗唔會刪既有內容。
  - PR／非 master dispatch：唔可以用 repo production metadata；喺 runner temp 用
    `make_fixture_release.py` 由候選 payload + 收據重建 fixture metadata + PDF，
    再行真 HTTP／瀏覽器／PDF 重建核對（`verify_candidate.py`），永不部署。
  - production：daily 成功 push 後 `dispatch_pages_deploy.py` dispatch；
    Pages workflow 以 `verify_deploy_request.py` 核實（run id／attempt／path／
    direct parent／metadata binding；queued／in_progress 只可短暫存在，timeout
    即失敗）；`postdeploy-verify.yml` 只喺 Pages workflow 成功後核對同一 commit，
    並再驗 HEAD == 平台記錄且係 master 祖先。
  - concurrency：daily 同 Pages 都按 event／ref 分組（PR 一組、production 一組），
    PR 唔可以取消或阻塞生產。Pages workflow 用 `queue: max`（GitHub 預設
    `queue: single` 只保留一個 pending run，新 pending 會取代舊 pending，可能令
    已驗證但未執行嘅 daily 部署被犧牲）；`cancel-in-progress: false` 保持唔會中途
    取消部署。

- **原因**：保持 D14「metadata 唔喺自己 hash 內」、D2-A「PR 永不 deploy、master 最小
  權限」同 D7-A「證據一致才發布」；同時令 daily 新產物真正觸發部署而唔擴權。
- **後果**：
  - `deploy_payload.json` 語義不變；archive 會額外收錄 envelope 同一致 sidecar。
  - master push 只可以部署「payload 同 committed metadata 一致」嘅 commit；有人改
    payload 但未經 daily 可信流程重新生成 metadata 會 fail-closed（正確，唔可以手改
    metadata 迎合 payload）。
  - `metadata.commit` 仍係資料來源 commit（source commit）；部署 commit 由 dispatch
    payload／Pages run 記錄提供，postdeploy 綁 Pages run `head_sha` 並驗 master 祖先。
  - 真正首次 daily dispatch／Pages Actions 部署仍屬平台驗收（E3／E4 UNKNOWN）。
- **回滾**：revert 相應 commit 即可回到舊 `deploy_payload.json`-only 封包；舊 Pages
  artifact 與 postdeploy 流程不受影響。

## 狀態更新（2026-09-24 追加；上文「本機候選／未 E3、E4」原文保留）

- **已上線並取得 E3／E4**：production Pages run 35944781623（push，exact merge SHA）
  依序 build（2m23s）→ deploy（11s）→ 同 workflow GATE-08（44s）全部 success；
  手動 exact-ref fallback run 35943298448 對 commit `b80a1d5` 全 PASS。
- 上文第 59 行「真正首次 daily dispatch／Pages Actions 部署仍屬平台驗收（E3／E4 UNKNOWN）」
  已由上述 run 完成，狀態由 UNKNOWN 轉 OBSERVED。
- 相關 md／payload fail-closed 事件同 D24 約定見 `docs/STATUS.md` §17。
