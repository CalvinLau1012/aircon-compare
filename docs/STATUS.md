# 狀態核查 — 2026-09-23（v1.2.9 修復候選；PR #10／D3-A）

> 本文件是指定快照的證據記錄，不取代 [治理要求](AIRCON_COMPARE_GOVERNANCE.md) 或生產 `metadata.json`。
> **目前狀態**：v1.2.9 修復已實作並通過 E2（本機測試），PR #10 以 draft 形式 push；候選 branch 已
> merge 最新 `origin/master`（be43b7c，2026-09-22 自動更新）同步生產資料；2026-09-23 已套用 D3-A
> `release` environment 單人 required reviewer 關卡；**未 merge PR、未 deploy、未發布 Release、
> 未操作 Secrets、未改生產 metadata**。受信任 CI（E3）與部署後核對（E4）仍未發生。

## 1. 基準與版本

| 項目 | 核查值／證據 | 分類 |
| --- | --- | --- |
| 已同步本機基準 | 候選 branch `codex/v1.2.9-governance-release` 已 merge `origin/master` `be43b7c0d0940b67614c64e070b2f10a9e591843`（2026-09-22 自動更新）；PR #10 為 draft | OBSERVED / E1 |
| 生產版本（線上） | 1.2.8；合併後 production `metadata.json` 為 master 流水線提交（`B20260922.83`，未手改） | OBSERVED / E1–E3 |
| 本機候選版本 | `models_data.py` `VERSION=1.2.9`（候選，未發布）；本地生成物為候選，hash 與舊生產負載唔同屬正常 | OBSERVED / E1–E2 |
| 部署構建 | `B20260922.83`；輸入 commit `f6887941c022c6c67fb4612ca688184830d1ff86`（合併後 metadata.json 流水線事實） | OBSERVED / E1–E3 |
| 更新流程 | [35776119746](https://github.com/CalvinLau1012/aircon-compare/actions/runs/35776119746)，schedule，success（2026-09-22；合併前 master 流水線） | OBSERVED / E3（歷史） |
| Pages 流程 | [35530841644](https://github.com/CalvinLau1012/aircon-compare/actions/runs/35530841644)，success | OBSERVED / E3（歷史）；不等於全部 E4 行為已驗證 |
| 本輪外部動作 | R6/R7 已 push draft PR branch、更新 PR body、merge `origin/master` 同步 base；D3-A `release` environment 已 API 設定並回讀；未 merge PR／deploy／建立 Release／操作 Secrets | OBSERVED / E1 |

同步前原有 UI／測試修復已核對；同步前工作另有外部備份及保留的 stash。備份中的私有工作記錄不屬部署負載。`CHANGELOG` 的 Unreleased 已分開「1.2.9 候選」與「1.2.8 之後已部署維護記錄」，唔可以整區視作已發布或未上線。

## 2. 排程與時間語義

配置 [daily-update.yml](../.github/workflows/daily-update.yml) 為 `30 16 * * *`：預定香港每日 00:30。GitHub schedule 可能延遲，並無準時保證（[GitHub 官方說明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)）。**cron 維持不變，不以提前排程冒充修復。**

以下為 2026-09-21 歷史生產 run 觀察（當時未修復收據傳遞）：

| 事件 | HKT |
| --- | --- |
| 預定觸發 | 00:30 |
| 更新 run 建立 | 02:56:35 |
| 更新 job 開始 | 02:57:14 |
| EMSD 收據記錄完成抓取 | 02:59:57 |
| metadata 封包時間 | 03:00:00 |
| 更新 job 完成 | 03:00:04 |
| Pages run 完成 | 03:00:51 |

歷史問題（本輪候選已改）：`datasetDate` 當時用 runner UTC `date +%F`、`datasetRetrievedAt` 用 metadata 生成時間後備；真正抓取證據係 `emsd_receipt.json` 18:59:57Z。

**候選行為（已實作、待 CI／部署驗證）**：

- `fetch_emsd.py` 成功收據在 CSV 原子寫入後對實際 bytes 計 `datasetHash`，`retrievedAt` 為抓取完成時間；抓取失敗寫失敗收據且唔改舊 CSV（403/429、0 頁、壞表頭、新機偵測失敗、寫入失敗）。
- `gen-metadata.py` 只接受成功、hash-bound、批准來源、非未來 UTC 嘅收據；`datasetDate` = 實際 `retrievedAt` 轉 UTC+8 香港日期、基於 `retrieval-date-fallback`；官方日期不假造。
- 重建模式用舊完整 hash-bound 收據 + 同一 CSV 會保留舊日期；舊無 hash 收據明確失敗並要求重新成功抓取，唔可以補假時間／假 hash。
- `deployTime` 仍為部署包封裝 UTC，UI 以 HKT 顯示；**唔會**改成 Pages 完成時間。

## 3. 資料口徑

> 2026-09-22 合併 `origin/master` `f688794`（自動更新 2026-09-21）後，最新 production
> snapshot 為 datasetDate 2026-09-21、build `B20260921.75`、1,834 registrations／1,773 models；
> 本節以下數字係先前 `3e2958d` 基準嘅歷史快照，唔可當最新數量。最新 metadata 以
> `metadata.json`／trusted pipeline 為準。


生產基準 [metadata.json](https://github.com/CalvinLau1012/aircon-compare/blob/3e2958d5852c035b4a09c5831fa66187f615d6ce/metadata.json) 與收據（歷史快照）：

| 數據 | 值／口徑 |
| --- | --- |
| rawRecordCount／registrationCount | 1,885 筆登記 |
| modelCount／recordCount | 1,824 個 canonical 型號 |
| 比較器型號 | 核心 29 + 其餘 EMSD 1,795 = 1,824；核心已包含於全量，不能再加 29 |
| 比較器有價／有尺寸 | 1,808／1,636（此快照計算；有價包含舊價格及停售型號，不代表現貨） |
| EMSD 抓取 | 歷史 run success=true、38/38 頁、1,885 行、未中止 |
| 官網 220／BigGo 742 | 分別為 2026-08-15／2026-08-26 歷史核實數，不代表今日全量重查 |

本輪候選**未計劃**重抓全庫／價格（避免外部負載與未授權資料變更）；收據新路徑用臨時 fixture 測試，生產 `emsd_receipt.json` 仍係舊無 hash 收據——下一次成功抓取才會產生 hash-bound 收據。驗證期間一次意外本地抓取已即時由 HEAD 還原：CSV／收據／sidecar bytes 與生產快照一致，未 commit／push／部署。

## 4. 本輪候選已實作與仍有差距

| 項目 | 本輪證據（E2） | 限制／後續 |
| --- | --- | --- |
| EMSD 收據時序與頁數 | `tests/test_emsd_receipt.py`；50 倍數／0 頁／壞表頭／偵測失敗／提交失敗／403／--help 防誤觸；交易式三檔提交＋故障注入回滾 | 未跑真實網絡抓取；新生產收據要受信任 CI 產生 |
| 收據 metadata 鏈 | `tests/test_receipt_metadata.py`：UTC↔HKT 跨月跨年、hash、來源、未來時間、舊收據失敗、信任閘、workflow wiring | 需 CI 實跑一次成功抓取 → metadata 先有 E3 |
| 完整 Schema 驗證 | `tests/test_schema_validation.py`：const/minimum/maxLength/allOf/真實日期/URI、root 非 object、core fail-closed | 依賴 `jsonschema[format]==4.26.0`；CI 安裝後生效 |
| 治理區塊 | 六塊唯一、duplicate key 拒絕、Registry／成功標準完整 Schema 驗證 | 治理文檔 3.1.2 評審未完成 |
| 功能契約證據 | `tests/test_feature_check_evidence.py`；`feature-check.py --run-tests` 18 個 required 綁定節點實際 passed；假 node／空斷言／常量斷言（`assert True`、`x = True; assert x`）／吞例外／skip 均阻斷 | CI 需實跑；瀏覽器需 Playwright |
| GATE-08 部署後核對 | `scripts/postdeploy_check.py` ＋ `tests/test_postdeploy_check.py`：expected＋online 完整 Draft 2020-12＋format、整個 JSON object 等值（非 8 欄位改動都攔）、本地 fixture HTTP＋瀏覽器；workflow 接受 Pages `dynamic` 及 `push`、只接本 repo master 成功、contents:read | 未接上實際 Pages run；線上核對未執行（E4 未取得） |
| GATE-09 歸檔 | `scripts/archive_release.py` ＋ `tests/test_archive_release.py`：reports 來源正確、`archiveCommit`／`sourceCommit`／`deploymentCommit` 分離、目錄＋zip 雙重 no-clobber、嚴格 SemVer；workflow 封裝前先跑 GATE-08（含 `--payload-dir .`） | 未發布 Release；需要人類設定 protected environment `release` |
| 批次推進閘門 | `run_official_batch.py` ＋ `tests/test_official_batch_gate.py`；fetch 腳本任一目標失敗即非零、唔用部分結果覆寫快照；BigGo 真失敗非零 | 未在 CI 實跑外部抓取 |
| Workflow Actions | 三個 workflow 全部 `actions/*` 固定到已核實完整 commit；靜態測試檢查無 major tag 殘留 | SHA 由 GitHub refs API 於 2026-09-21 核實；未來升級要重新核實 |
| 回滾演練 | 公開 fixture-only `tests/test_restore_drill.py` | 屬本地 mock；真實自建 apply／rollback 仍由維護者喺私人環境執行 |
| 資料契約負向 | `tests/test_validate_data_contracts.py`：逐行 15 欄／必填／數值／能源級別／sentinel／JSON entry 型別 | 未覆蓋所有數值範圍（不發明範圍） |
| PDF／HTML 事實 | `tests/test_pdf_metadata_guard.py`：invalid metadata 唔出 PDF、同輸入重建一致、HTML 可重現；`reportlab==5.0.0` 已固定 | 未用 PDF parser 逐頁核（CID 字型）；重建一致性取代內容抽取 |
| 公開恢復演練 | `tests/test_restore_drill.py`：checksum、兼容、原子換入、失敗唔破壞 good package | 屬本地 mock；生產 apply/rollback 待維護者 |
| 工作流靜態安全 | `tests/test_workflow_security.py`：SHA pin、信任條件、pipefail、allowlist、privacy index、tag 檢查 | CI 執行仍屬 E3 UNKNOWN |
| 私隱模式 | `tests/test_public_privacy.py`：index vs worktree、untracked、已刪 tracked、git 失敗、pending marker、tree 掃描 | 歷史私隱待人類評估 |
| BigGo 三態 | `tests/test_biggo_batch_semantics.py`：net_err 不推進、abort 冷卻、cooldown/not-active 狀態 | 未打真實 API（避免外部負載） |
| 15 項 required 功能 | Registry 完整 Schema 通過；pytest 399 項全過；feature-check 實跑 18 節點 passed | E2 只覆蓋受測範圍，唔等於部署後行為 |

以上差距沒有降低任何 REQUIREMENT，也不授權改部署架構、跳過門禁或操作生產。

## 5. 本輪驗證與交付邊界

實際執行（原機 Windows 項目 `.venv`；私人線 shell 測試已移出公開 repo）：

| 項目 | 實際結果 |
| --- | --- |
| `python -m pytest tests/ -q`（`.venv`） | **399 passed**（含瀏覽器 smoke；1 個預期內 duplicate-zip warning），退出 0 |
| `feature-check.py --run-tests` | 15 required、18 個 collection 節點實際 passed；報告寫系統 temp |
| `extract_governance.py` | 六區塊、ID 唯一、JSON 嚴格解析、完整 Schema 驗證通過，退出 0 |
| `validate_data.py` | 通過，退出 0 |
| `validate_metadata.py`（生產 metadata） | 通過（仍係 1.2.8 生產事實） |
| postdeploy／archive 測試 | 本地 fixture HTTP＋瀏覽器、歸檔 no-clobber 全過 |

候選 HTML／PDF 已改動；舊生產 metadata 的 `releasePayloadHash` 不再代表候選負載。發布前必須由受信任流水線重新生成 metadata，並由 GATE-08 核對線上一致。**唔可以**用本機候選宣稱 GATE-08／09 或部署完成。

未執行：commit／push／deploy、Release 發布、Secrets 操作、私人自建環境 apply／rollback、真實 Pages 部署後核對、EMSD 真實網絡重抓、價格／全庫重抓。

## 6. 需要人類決定／平台後續

1. 治理文檔 3.1.2 及 Registry／門禁變更評審（PR／Code Owner）。
2. 1.2.9 是否發布：需受信任 CI 全綠 + GATE-08 通過 + 決定 Release tag 與歸檔。
3. D3-A 已於 2026-09-23 完成：`release` environment 唯一 required reviewer 為
   `CalvinLau1012`，`prevent_self_review=false`；實際 publish 仍需該維護者在等待關卡人工批准。
4. 私人自建伺服器正式 apply／rollback 演練仍需維護者喺自己環境執行（工具已移出公開 repo）。
5. Actions 完整 commit 已按 2026-09-21 refs API 核實固定；日後升級需重新核實，不由 AI 猜 SHA。

## 7. 2026-09-22 第二輪返修（v1.2.9 候選，未提交／未部署）

> 用戶授權 CHANGE 本機候選：唔 commit／push／deploy、唔發 Release、唔操作 Secrets、
> 唔執行正式 apply／rollback、唔改外部 repo 權限。全部改動喺工作樹。

### 7.1 私人部署線移出公開 repo
- `docker/`、`release/` 及文件私人段落完整複製到 **repo 外本輪可恢復副本**（Windows Temp；
  內含 `SHA256SUMS`、`RECOVERY.md`、`docs-snapshot/`、`tests-private/`；本文件唔記錄本機路徑）。
- **未係長期私人儲存**：Temp 只係本輪可恢復副本，唔可以當永久私人真源。**commit／push 前人類待辦**：
  將該包轉移到持久私人儲存（私人 repo／加密備份），並重跑 `sha256sum -c SHA256SUMS` 核驗。
- 公開工作樹對 `docker/`、`release/` 嘅刪除仍屬候選（本輪未 commit／未 stage／未 push）。
- 核對 bytes 一致後，公開工作樹移除 `docker/`、`release/`；公開文件只留中性註記。
- **限制**：Git 歷史（HEAD `3e2958d` 及更早）仍包含私人線；工作樹移出唔會改寫歷史，
  需人類做私隱評估。T-67 治理草案以 `.git/info/exclude` 精確排除，未刪未改（檔案仍在 repo 根）。

### 7.2 本輪實質修復（節錄）
| 類別 | 內容 |
| --- | --- |
| CI 信任 | 三個 workflow Actions 固定完整 commit；Chromium 只裝一次且喺任何 pytest 前；daily 用 `stage_artifacts.py` 精確 allowlist（拒 allowlist 以外改動）、privacy `--mode index`、push fail-closed（無 pull --rebase）、build 加 run attempt |
| GATE-08 | postdeploy 完整 object＋Schema 才結束重試；報告寫入失敗非零；expected 無效即停 request；PDF 必須同 expected metadata 重建一致（唔止 magic）；`workflow_run` 接受 Pages `dynamic`、master only、read-only |
| GATE-09 | archive 重 hash 實際 bytes＋CHECKSUMS＋zip 集合／重複 entry；provenance 只 ignored archivedAt；tag 必須等於 metadata.version；release 等級要求 feature-check／postdeploy／junit 有效報告；打包前私隱掃描 |
| 批次一致性 | 官網 6 個腳本任一目標失敗即非零、失敗不覆寫快照；wrapper 輸出必須 dict/list 且每 entry 有實質 evidence；BigGo 有 net_err 唔推進 idx（可重試）；EMSD CSV＋sidecar 用 crash journal 交易式提交 |
| Feature Check | subprocess 非零／collectionErrors／未完成 session／缺 setup-call-teardown／skip／XPASS／deselect／空 selection 全部 fail-closed；report.ok 同 errors 一致；APPROVED_SKIP 只逐節點豁免 skip |
| 資料契約 | `validate_data.py` 加逐行 15 欄、必填、數值、能源級別、sentinel、JSON entry 型別；`gen-metadata` 生產必須收據、finalize 驗 CSV hash／rawCount、本地 force 不可覆寫 repo metadata；extractor 拒 NaN／Infinity／未知 marker |
| PDF | invalid／missing metadata 不可出 production PDF；同輸入重建 byte-for-byte；表格截斷加省略號；HTML 同輸入重建一致 |
| 治理 | 新增 `docs/GOVERNANCE_MATRIX.md`（GATE／features／SC／TODO 盤點）及 `docs/adr/ADR-001-raw-emsd-snapshot.md`（原始 response 快照候選，未實作）；8 個 required 功能**新增 testBindings**（待 Code Owner 評審治理候選，未降級） |

### 7.3 本輪驗證
- 全套 `pytest tests/ -q`：見下方 §5 更新（實際數字）。
- `feature-check.py --run-tests`：18 個綁定節點 passed。
- `extract_governance.py`、`validate_metadata.py`、`validate_data.py`、`git diff --check`：退出 0。
- 私隱 gate：`--mode worktree` 0 命中；`--mode index` 喺 CI `git add` 後使用。
- 全程冇任何即時 EMSD／BigGo／官網抓取；測試全部隔離 temp／loopback。

### 7.4 仍待人類／平台（唔可以虛構）
1. 受信任 CI（E3）：daily／postdeploy／release-archive 未執行。
2. 生產 E4：live Pages 部署後核對、真實 rollout。
3. protected environment `release` 嘅 required reviewers 屬平台設定 UNKNOWN。
4. Pages root auto-deploy 與完整門禁脫節：需人類決定方案（本輪未改 Pages 設定）。
5. Git 歷史私人線私隱評估（改寫／轉私人 repo）。
6. 原始 EMSD response 快照設計（ADR-001）。
7. 監控頻率／新鮮度閾值／告警接收者：UNKNOWN。
8. 私人自建伺服器正式 apply／rollback 由維護者執行。
9. **commit／push 前**：將 repo 外私人包（Windows Temp 可恢復副本）轉移到持久私人儲存並核驗
   `SHA256SUMS`；確認公開刪除已成為持久私人真源後，先好將公開刪除 commit／push。


## 8. 2026-09-22 第四輪返修（v1.2.9 候選，未提交／未部署）

> 全部本地候選；未 commit／stage／push／deploy、未 live fetch。最終驗收以
> machine acceptance report 為準（repo 外；`scripts/run_acceptance.py`）。

### 8.1 本輪修復
| 問題 | 修復 |
| --- | --- |
| queue 契約分散、bool／負數／超範圍／錯誤 model 可通過；workflow `|| echo 0` 假裝 stage 0 | 新增 `queue_utils` 單一契約（missing→預設；壞檔 raise；stage exact int 0/1/2；models 非空字串、canonical 無重複；stage 0 必須空、1/2 必須非空）；`fetch_emsd`／`advance_queue`／`run_official_batch`／workflow 全部改用；workflow 解析失敗即 step 失敗 |
| stage 2→0 先清 queue 後啟動 price batch，meta 失敗會遺失 queue | `advance_queue` 改為先啟動 price meta（True／False 皆成功語義），成功／已啟動後才清 queue；故障注入＋重跑冪等測試 |
| `prices_meta` 壞檔回 `{}`、save 直接覆寫 | `batch_utils` load 對 corrupt／型別／範圍 fail-closed；save 同目錄 tmp＋fsync＋replace，失敗保留舊 bytes；workflow 用 `scripts/price_batch_state.py`（0 active／1 inactive／2 corrupt 阻斷） |
| 永遠通過測試（rollback tautology） | 改為精確部分狀態斷言＋journal 內容驗證＋解除故障 recovery 還原＋recovery 失敗保留 journal |
| `stage_artifacts` 容許 deletion／唔驗 manifest／唔查 index | deletion（worktree／staged）fail-closed；rename 拒絕；manifest 用 `gen-metadata` 同一契約；stage 前驗 staged paths；失敗零部分 stage；dry-run 不改 index |
| privacy gate 「tracked」實際讀 worktree | 明確四模式：`tracked`=HEAD tree blobs、`index`=staged blobs、`worktree`=tracked+untracked、`tree`=目錄 bytes；cat-file 嚴格解析、unmerged／重複 path／invalid UTF-8 fail-closed；PDF 等 binary 只列證據上限 |
| release 接受任意非空文字報告 | 新增 machine acceptance runner（gate ID、exact argv、UTC、真實 rc、log sha256）＋JUnit；archive validator 要求 required gates exactly once、rc=0、argv allowlist、log hash；feature／postdeploy 做結構驗證 |
| 官網批次舊快照可冒充完成 | wrapper 收集每腳本 machine receipt（attempted／succeeded／failed／alreadyVerified／covers）、output before/after hash、queue hash；zero-attempt 需 alreadyVerified＋evidence；queue model 未覆蓋即唔推進；receipt 寫 repo 外 |
| 文件數字漂移（GATE-06 24 vs 23） | 移除逐檔測試數；現況只保留 aggregate 實數（399 passed、18 feature nodes），以 machine acceptance manifest 為準 |

### 8.2 本輪新增負向證據（節錄）
- queue：broken JSON／bool／-1／3／非字串／空白／重複／stage 0 帶 models 全部 reject；
  workflow 無 stage-0 fallback（靜態測試）。
- meta：corrupt／型別範圍錯 reject；save 失敗保留舊 bytes；stage2 啟動失敗不清 queue；
  重跑冪等完成。
- staging：allowlisted deletion、staged deletion、unexpected deletion、rename、
  pre-staged unexpected、manifest traversal／duplicate／self-reference／missing／symlink
  全部非零且零部分 stage。
- privacy：HEAD／index／worktree bytes 分離、unmerged、cat-file 失敗、invalid UTF-8。
- release：`ok`／`FAILED` 文字、非零 gate、缺／重複／未知 gate、log tamper、弱
  feature／postdeploy JSON、壞 JUnit 全部拒絕。
- official batch：舊快照＋zero attempt、queue 執行中被改、output hash 唔符、
  無 marker、腳本失敗、stage 唔一致、advance 失敗全部唔推進。

### 8.3 待人類／平台（唔可以虛構）
1. ADR-002：官網 queue model 冇品牌目錄覆蓋時，daily 要 A 阻斷／B 發布但 queue pending／C 擴來源（現行實作係 fail-closed A，待決定）。
2. 受信任 CI（E3）與 live Pages（E4）仍未執行；acceptance manifest 只係本機 E2。
3. protected environment `release` required reviewers、Pages root auto-deploy 與門禁脫節屬平台設定／架構缺口。
4. Git 歷史仍含私人線；私人包（Windows Temp）需先轉移持久私人儲存並核驗。
5. 私人自建線嘅 `docker/run-update.sh` 未跟隨本輪 wrapper／queue 契約更新；還原私人線時需同步（記錄喺私人包 notes）。
6. 原始 EMSD response 快照（ADR-001）未實作；監控閾值／告警 UNKNOWN。


## 9. 2026-09-22 第五輪返修（v1.2.9 候選，未提交／未部署）

| 問題 | Root cause | 修改 | 負向證據 |
| --- | --- | --- | --- |
| `git diff --cached --name-status -z` parser 誤讀（`M\0path` 被當 tab 拆成空 path） | 未按 NUL token 格式解析 | `scripts/stage_artifacts.py` 改純 parser（M/A/D/T/R/C、truncated/未知/空/duplicate/invalid UTF-8 全拒）；新增 index snapshot＋失敗原子恢復 | `test_stage_artifacts.py`：合法 pre-staged allowlist 成功且 index blob OID 不變、pre-staged＋新改動、parser 格式／拒絕 case、invariant 失敗恢復 index |
| release 報告路徑可逃逸 reports 根（log/JUnit traversal、absolute、backslash、symlink） | 直接 `os.path.join(reports_dir, rel)` | `archive_release.safe_report_path`（canonical POSIX、拒 `..`／drive／backslash／NUL／symlink／逃逸）＋ required report exactly-one | `test_archive_release.py`：traversal／absolute／backslash／symlink／duplicate report 全部拒且不建 archive |
| acceptance schema 只驗 ok=true | 無 schema/identity 驗證 | 驗 schemaVersion==1、runner、ok exact bool、commit 40-hex 且等於 archiveCommit、gates exact set、timestamps | wrong runner／schema／commit／timestamp 全部拒 |
| official receipt 綁定不足（phantom covers、wrong script、failed>0+rc0、count mismatch、advance 前未寫 receipt） | marker 只做寬鬆檢查 | strict marker（schemaVersion/script/counts/lists/covers=union、listed 必須有 output evidence）；ready receipt 先寫再 advance；final 更新失敗如實報 | `test_official_batch_gate.py`：phantom／wrong script／failed+rc0／count mismatch／queue 改動／hash race／ready 寫入失敗（advance 未執行）／valid ready→advance |
| acceptance runner CLI 可執行任意 argv、未知 `--only` 靜默、ID 可逃逸 | `--spec`＋寬鬆 only | 移除 `--spec`（自訂命令只可內部 `run_gates`）；ID `^[A-Z][A-Z0-9_]*$` unique；未知 `--only` 非零；report／log 必須 repo 外（測試可 `--allow-repo-paths`） | `test_acceptance_runner.py`：duplicate／unsafe ID、unknown only、`--spec` 拒絕、repo 路徑拒 |
| `prices_meta` 日期只 regex、`detect_mode` 錯誤轉 full | 日期未驗真實日曆、catch 過寬 | `batch_utils` 用 `date.fromisoformat`／`strptime` 驗真日期、月份、`last_deploy`／`last_force_batch` 時間；`detect_mode` 唔再吞錯 | `test_queue_price_contract.py`：2/30、month 13、`2026-9`、壞 deploy time、corrupt meta detect_mode raise |

本輪實數：**399 passed**、feature-check 18 節點、acceptance 7 gates rc=0（machine manifest 見交付報告）。歷史數字不改；現況只保留 aggregate。

## 10. 2026-09-22 第六輪（D1-B／D2-A／D4-A／D7-A／D8-A）

> 全部本地候選；未 commit／push／deploy、未發布 Release、未跑 E3／E4、未執行 live fetch。

| 決策 | 狀態 | 證據 |
| --- | --- | --- |
| D1-B coverage pending | 已實作候選 | `run_official_batch.py` 分流 hard vs coverage；`publish_official_status.py`＋UI 待核提示；負向測試覆蓋壞 receipt／failed／hash race／queue 保留 |
| D2-A Pages Actions | workflow／artifact 候選 | `pages-deploy.yml`：PR 不 deploy、master `needs` build、`github-pages` environment、最小 Pages 權限；`build_pages_artifact.py` 只收 manifest 公開檔；postdeploy 綁 exact `head_sha`；未切換 Pages Source（E4 UNKNOWN） |
| D4-A history audit | 已實跑 | `check_public_history.py`：208 reachable commits、934 blobs、**credentialFindings=0**；selfHostFindings 32（分類為已知私人部署線 residual risk）；報告寫 repo 外 |
| D5-A 私人 repo | 已取得平台回讀 | 私人 repo API 回讀 `visibility=PRIVATE`（名稱只喺交付報告）；來源 `SHA256SUMS` 先驗 25/25；push 後 fresh clone 逐檔 checksum／bytes 全部一致；Temp 原件保留未刪 |
| D7-A raw receipt | 已實作候選 | 逐頁 raw bytes 證據＋公開 hash receipt＋private sink 介面、require fail-closed、90 日 retention 邊界測試；raw bytes 不在公開 worktree／artifact；真正 private Secret／首個 live snapshot 未做（UNKNOWN） |
| D8-A 72h monitor | 已實作候選 | threshold=`age > 72h`；71:59:59／72:00:00 pass；future／invalid／missing fail-closed；issue 去重、狀態改變 update、恢復 close；6 小時 workflow 未實跑平台 |
| D3 required reviewer | **pending 使用者選 A／B／C** | 平台設定未回讀，維持 UNKNOWN |
| D6 self-host 同步 | **DEFERRED BY DECISION D6-A** | 公開 PR merge 後另開私人 repo 工作；今輪未把私人線混入公開 repo |

### 10.1 本輪新增／修改（候選）

- Workflow：`.github/workflows/pages-deploy.yml`、`freshness-monitor.yml`；更新
  `postdeploy-verify.yml`、`daily-update.yml`。
- 腳本：`scripts/build_pages_artifact.py`、`scripts/check_public_history.py`、
  `scripts/check_freshness.py`、`scripts/freshness_issue.py`、
  `scripts/publish_official_status.py`；更新 `fetch_emsd.py`、`run_official_batch.py`、
  `stage_artifacts.py`、`generate_html.py`。
- 測試：`tests/test_pages_deploy.py`、`tests/test_public_privacy_history.py`、
  `tests/test_emsd_raw_receipt.py`、`tests/test_freshness_monitor.py`、
  `tests/test_coverage_status.py`；更新相關回歸。
- Docs／ADR：`docs/adr/ADR-001`、`ADR-002`、`docs/DECISIONS.md` D17、
  `docs/GOVERNANCE_MATRIX.md` §8、README／CHANGELOG／需求摘要／報告／治理版本歷史。

### 10.2 本輪實數與 machine evidence（OBSERVED / E2）

- 全套 pytest：**433 passed／1 skipped**（Windows symlink 平台限制 skip）。
- focused（note §4.6）：**69 passed／1 skipped**。
- `feature-check.py --run-tests`：**18 個綁定節點 passed**，無 skip／xfail／fail。
- Machine acceptance：`D:\tmp\aircon-dsh-acceptance-round6b\acceptance.json`，
  `ok=true`、7 gates（GOVERNANCE_EXTRACT／VALIDATE_DATA／VALIDATE_METADATA／
  PRIVACY_WORKTREE／PYTEST／FEATURE_CHECK／DIFF_CHECK）全部 rc=0；log／JUnit SHA-256
  可重算。此 manifest commit 仍係 `3e2958d`；commit 後需再跑一次指向 PR head。
- D4-A history audit：`D:\tmp\aircon-history-round6.json`，208 commits／934 blobs、
  credentialFindings=0、selfHostFindings=32（已知私人部署線殘餘風險，只列類型／commit／path）。
- D5-A 私人 repo visibility API 回讀 PRIVATE；fresh clone `SHA256SUMS` 25/25 通過，
  逐檔 bytes 與來源 Temp 包一致（Temp 原件未刪）。

- PR bootstrap：新 `pages-deploy.yml` 未存在於 default branch 前唔會 trigger；已喺既有
  `daily-update.yml` 加唯讀 `pull-request-gates`（same-repo PR、contents:read、exact PR
  head SHA、跑 acceptance＋history audit＋artifact check-only，不 deploy）作臨時 trusted PR checks。

### 10.3 外部未執行（不可虛構）


- commit／push／draft PR（本輪本地完成後才做）。
- 受信任 CI（E3）、真實 Pages Actions deploy（E4）、Pages Source 切換。
- 真正 private raw sink Secret／首個 live raw snapshot／私人 Release asset 交叉核對。
- `release` environment required reviewers（本輪當時 D3 未選；2026-09-23 已由 D19／§13 解決）。
- 私人 self-host 線同步（D6 deferred）。

## 11. 2026-09-23 PR #10 審查返修（v1.2.9 候選；本機完成後 commit／push）

> 全部本地候選：本節數據係 commit 前 E2 證據；未 merge、未部署、未跑 E3／E4。
> 每個 finding：root cause → 修改 → 負向／正向測試。

| # | Finding（PR 審查） | Root cause | 修改 | 測試證據 |
| --- | --- | --- | --- | --- |
| 1 | Pages artifact 冇 `metadata.json` | `build_pages_artifact` 只 copy `deploy_payload.json` 檔案；metadata 又唔可以入自己 hash | 新增 `deploy_envelope.json`（payload＋metadata＋一致 sidecar）；封包前驗 Schema／version／payload hash／CSV hash／counts；PR 用 `make_fixture_release.py` 隔離 fixture metadata + 真 HTTP／瀏覽器／PDF 重建 | `tests/test_pages_deploy.py`（envelope exact files／錯配 fail／stale raw receipt skip）；`tests/test_pages_artifact_e2e.py`（真 loopback＋Chromium＋`pdf_matches_metadata`） |
| 2 | daily push 唔觸發 Pages workflow（R7 再收緊：attempt／path／direct parent／metadata binding／完成時序） | 預設 `GITHUB_TOKEN` 嘅 push 唔產生新 run；原本只驗祖先且假設 source run 已完成 | daily push 成功後 `dispatch_pages_deploy.py` 帶精確 commit＋sourceRunId `repository_dispatch`；`verify_deploy_request.py` 有界 polling completed/success、run_attempt、workflow path、單親 direct parent、metadata `workflowRunId`／`commit`；唔 fallback 最新 master | `tests/test_verify_deploy_request.py`（in_progress→completed、timeout、attempt／path／run id mismatch、遠祖先／merge、metadata mismatch、failure/cancelled/API error、valid chain）、`test_daily_workflow_dispatches_pushed_commit_after_push` |
| 3 | freshness 忽略 `postdeployOk` | `plan_issue` 只比較 freshness reason／body | combined health；fingerprint 唔含 `ageSeconds`；錯配／缺 report fail-closed；HTTP／network／API error 非零；report 脫敏 | `test_freshness_monitor.py`：fresh+postdeploy fail 會 alert、同 stale 6 小時後 noop、分類改變 update、恢復 close、report 缺失／non-object／fetch error／API error 非零 |
| 4 | D7 只有 local sink | runner 目錄唔係 90 日 durable；remote provider 未定 | `scripts/private_raw_sink.py` 可插拔 adapter：local 如實標示非 durable；GitHub Release asset 候選（PRIVATE 回讀、拒覆蓋、上傳後下載 hash 核驗、90 日 retention、失敗唔發 success receipt）；require 缺配置即 raise | `tests/test_private_raw_sink.py`（fake HTTP：private 回讀、下載核驗、上傳失敗、同名拒覆蓋、retention、partial config、symlink／junction escape、token／raw bytes 唔入公開檔） |
| 5 | `pages-deploy` dispatch 唔會入 production | 所有 package／upload／deploy step 都係 push-only | job／step 以 `steps.target.outputs.mode` 統一控制；workflow_dispatch 只限 master；PR 永不 production；deploy job 用 needs output | `test_pages_deploy_workflow_contract`（trigger／mode／step 次序／deploy 條件） |
| 6 | `build_pages_artifact build` 亂 rmtree `--out` | 無 out 安全檢查、直接清理 | 拒 repo 根／祖先／`.git`／link 父層／與輸入重疊／未封印既有目錄；staging＋安全替換；失敗唔刪既有內容 | `test_pages_artifact_rejects_dangerous_out_dirs`、`test_pages_artifact_refuses_unsealed_existing_dir_and_preserves_it`、`test_pages_artifact_failure_keeps_existing_content` |
| 7 | daily／bootstrap 共用 concurrency group | `weekly-update` cancel-in-progress 可被 PR run 取消 | daily 同 Pages 都按 event／ref 分組（PR 一組、production 一組）；PR 唔可以取消／阻塞生產 | `test_daily_workflow_dispatches_pushed_commit_after_push`（group 表達式）、`test_pages_deploy_workflow_contract` |
| 8 | Windows symlink 測試 skip 當 pass | 建立唔到 symlink 就 `pytest.skip` | 冇 skip：真 symlink／junction 可用就實測；平台唔准就用受控 monkeypatch 驗同一拒絕路徑；cleanup 亦拒 link-like | `test_pages_artifact_rejects_symlink_source_without_skip`、`test_pages_artifact_rejects_symlink_out_parent`、`test_local_sink_rejects_symlink_or_junction_sink`、`test_local_cleanup_skips_symlink_escape_and_outside_content` |
| 9 | 信任邊界再審查 | dispatch／verify／raw sink／freshness 未有端到端負向覆蓋 | 逐條 API／run／commit／hash 綁定同 fail-closed；報告／log 脫敏 | 上述測試＋feature-check `--run-tests` |

### 11.1 本輪實數（OBSERVED / E2）

- 全套 pytest：**475 passed／0 skipped／0 failed**（`pytest-junit.xml` 可重算；Windows symlink 已無 skip）。
- `scripts/run_acceptance.py`：7 gates 全部 rc=0；report 寫喺 repo 外 temp
  （`<repo-external-temp>/acceptance.json`；commit 記 `a6fa0d2`，commit 後再跑最終一次）。
- `feature-check.py --run-tests`：**18 個 required 綁定節點全部 passed**，無 skip／xfail／fail。
- `check_public_history.py --all-refs`（commit 前）：216 commits／1042 blobs、
  **credentialFindings=0**、selfHostFindings=35（已知私人部署線 residual risk）。
- 端到端：fixture release → envelope → 真 loopback HTTP → metadata／payload hash／run identity →
  PDF 重建一致 → Chromium runtime（version／last deploy／search／compare）全 PASS。
- D5 私人包本機 checksum 再核（read-only、唔印名稱）：SHA256SUMS 25/25 一致；私人 repo
  visibility 本輪未有憑證再回讀，維持先前 OBSERVED、今輪不重複宣稱。

### 11.2 語義同未執行

- `deploy_payload.json` 仍係唯一 `releasePayloadHash` 範圍；`metadata.json` 只入
  deployment envelope，唔入自己 hash（D14 不變）。
- master push 只可以部署「payload 同 committed metadata 一致」嘅 commit；有人改 payload
  但未經 daily 可信流程重新生成 metadata 會 fail-closed，唔可以手改 production metadata。
- 未執行：merge、首個 daily `repository_dispatch`、Pages Actions E4、remote raw sink
  provider 選擇／Secret／首個 live snapshot、D3 required reviewer（當時；後續由 D19／§13 解決）、D6 self-host 同步。

## 12. 2026-09-23 R7：base 同步與 source run 收緊（PR #10）

> merge commit `698e612`；`origin/master` `be43b7c` 已成為 HEAD 祖先；未 merge PR、
> 未 deploy、未切 Pages Source。R7 只係把已批准嘅 D2-A／D7-A／D8-A 實作補到 fail-closed。

| 項目 | Root cause | 修正 | 證據 |
| --- | --- | --- | --- |
| source run 綁定不足 | 只驗 run id／祖先／假設 completed；`sourceRunAttempt` 冇比對；冇驗 workflow path；daily push 同 Pages 查 API 有完成時序競爭 | `verify_deploy_request.py`：有界 polling（queued／in_progress 可短暫存在，timeout 即失敗）、精確 `run_attempt`、workflow path 必須 `.github/workflows/daily-update.yml`（`@ref` 安全解析）、部署 commit 必須單親 direct parent == source `head_sha`、checkout `metadata.json` 綁定 `workflowRunId`／`commit`；sleep／timeout 可注入 | `tests/test_verify_deploy_request.py`：valid chain、in_progress→completed、timeout、attempt／run id／path mismatch、遠祖先／merge、metadata mismatch／缺失、failure／cancelled／API error、無 token／body 洩漏 |
| Pages concurrency 語義 | 文件聲稱 FIFO 但 GitHub 預設 `queue: single` 只保留一個 pending，新 pending 會取代舊 pending | `pages-deploy.yml` production／PR 分組保留、`cancel-in-progress: false`、加 `queue: max` | 契約測試 assert `queue: max` 及 `cancel-in-progress: false` |
| PR conflict | master 2026-09-22 自動更新重新生成 binary PDF，同候選 PDF 衝突 | merge `origin/master`（非 merge PR）；資料檔以 master 流水線事實為準；`generate_html.py` + `generate_pdf.py`（用合併後 committed metadata）重建 index／PDF；唔手改 metadata | `git merge-base --is-ancestor origin/master HEAD`；生成物由合併後程式產生 |
| 文件現況 | README／需求摘要／報告 current snapshot 仍寫 2026-09-21 | EMSD 現況日期同步為 `datasetDate 2026-09-22`（型號 1,773／登記 1,834 不變；合併後 CSV hash 不變）；需求摘要過時「1,808 有價」改為 1,752（重算：非核心 1,723＋核心 29）；歷史數字保留 | 文件 diff；`generate_html`／`generate_pdf` 重建後測試 |

### 12.1 R7 實數（merge 後、final HEAD 前）

- full pytest：**496 passed／0 skipped／0 failed**（Windows symlink 已無 skip）。
- focused：`tests/test_verify_deploy_request.py` 21 cases、`tests/test_pages_deploy.py`、
  freshness、private raw sink、postdeploy、archive、EMSD receipt／raw receipt、
  workflow security 全部 pass。
- 最終 acceptance／history 會喺 R7 final HEAD 再跑一次，machine manifest 寫 repo 外。

### 12.2 仍未執行（UNKNOWN）

- merge PR、deploy、Pages Source 切換、E3／E4。
- remote raw sink provider 選擇／Secret／首個 live snapshot（D7 保持 UNKNOWN）。
- D3 required reviewer（R7 當時未完成；後續由 D19／§13 解決）；D6 self-host 同步。
- PR 首個 `repository_dispatch` 真實 run 綁定（要 merge 後 daily 先遇到）。

## 13. 2026-09-23 D3-A：`release` environment 人工批准關卡

- **人類決策**：項目只有一名維護者，採 D3-A；不要求不存在的第二名 reviewer。
- **平台事實（GitHub API 回讀）**：environment=`release`；protection rule=`required_reviewers`；
  reviewer=`CalvinLau1012`（user id `178408566`）；`prevent_self_review=false`；`wait_timer=0`；
  `deployment_branch_policy=null`；`can_admins_bypass=true`（管理員可另行明確 bypass，並非自動跳過）。
- **實際效果**：`release-archive.yml` 的 `publish` job 只有 `publish=true` 才執行，並引用
  `environment: release`；屆時會等待維護者人工批准。build job 及一般 PR gate 不經此關卡。
- **未執行**：未 merge PR、未部署、未建立 tag／Release、未執行 publish、未操作 Secrets；
  E3／E4 及首次真實 approval flow 仍未驗收。
- **決策記錄**：見 `docs/DECISIONS.md` D19。

## 14. 2026-09-23 R8：remote raw sink 接線與合併前發布路徑核對（PR #10）

> 用戶已批准按序發布（需求摘要「用戶發布授權」／DECISIONS D20）；本節只係合併前候選
> 證據（E1／E2）。未 merge、未 deploy、未切 Pages Source、未 tag／Release、未操作 Secrets；
> 生產執行交回獨立驗收後。E3／E4 仍 UNKNOWN。

### 14.1 本次修正（發布阻斷點）

| 項目 | Root cause | 修正 | 證據 |
| --- | --- | --- | --- |
| daily remote raw sink 未接線 | `daily-update.yml` fetch step 只傳 `AIRCON_EMSD_REQUIRE_RAW_SINK`／`AIRCON_EMSD_RAW_SINK_DIR`；即使平台設好 remote Secret，adapter 都收唔到 `REPO`／`TOKEN` | fetch step env 接入 `AIRCON_EMSD_RAW_REMOTE_REPO`／`_TOKEN`／`_TAG`／`_RETENTION_DAYS`，全部精確對應 `secrets.*`；註明私人 repo 識別唔准入 repo／Variables／log；local-dir 標明過渡非 durable；require 缺配置／上傳失敗維持阻斷 | `tests/test_workflow_security.py::test_daily_raw_sink_env_wiring_secret_only_and_fail_closed`；`tests/test_private_raw_sink.py`（partial config／未配置 require／下載核驗失敗 raise） |

### 14.2 merge→首次 daily→Pages→postdeploy→archive 前置與失敗條件（現行實作）

1. **merge push 自動觸發 `pages-deploy.yml`（push／production）**：checkout merge commit →
   `verify_deploy_request --event push`（HEAD==commit、master 祖先）→ 7 gates →
   `build_pages_artifact`。committed metadata 仍為 1.2.8，`models_data.VERSION=1.2.9`，
   payload 亦已由候選重建；本機實測 fail-closed（rc=1：`releasePayloadHash 唔一致`＋
   `metadata.version 1.2.8 != models_data.VERSION 1.2.9`）→ **唔會用舊 metadata 部署**。
   此為設計行為，唔可以為綠燈放寬。
2. **首個 daily（schedule 或 master `workflow_dispatch`）**：gates → EMSD 抓取
   （require sink 未配置即阻斷）→ 1.2.9 收據 hash-bound core metadata → PDF → finalize
   payload hash → validate → `verify_candidate` → 精確 allowlist stage → privacy index →
   commit＋push → `repository_dispatch`（精確新 commit＋sourceRunId／attempt）。
3. **Pages production（repository_dispatch）**：`verify_deploy_request` 有界 polling
   source run completed/success、workflow path `daily-update.yml`、單親 direct parent ==
   source head_sha、metadata `workflowRunId`／`commit` 綁定；再 7 gates＋history audit →
   `build_pages_artifact`（version 1.2.9 一致）→ deploy job（`github-pages` environment）。
   **平台前置：Pages Source 需由 legacy 切為 GitHub Actions（人手，未執行）**；否則 deploy
   job 失敗，E4 無法取得。
4. **postdeploy-verify（workflow_run）**：只接新 Pages workflow success（master／push／
   dispatch）→ 線上 metadata／payload hash／CSV／PDF／瀏覽器 runtime 比對＝E4。
5. **Release 歸檔**：E4 通過後人手 dispatch `release-archive.yml`（`environment: release`
   人工批准；`publish=true` 才建 Release）；tag 必須等於 metadata.version；先跑 GATE-08
   再打包，任何 hash／報告錯即阻斷。歸檔只讀 production payload；本輪 full acceptance
   後 worktree 保持 clean（見 14.3）。

### 14.3 本輪實數（OBSERVED / E2；commit `75f3daa`）

- `scripts/run_acceptance.py`：**ok=true、7 gates 全部 rc=0**；machine manifest commit
  `75f3daa5f33bf9b681c66acd22c0ab5355a3960b`（即 14.1 修正＋D20 之後、STATUS 本節之前；
  本節只追加文件）。
- PYTEST gate：**518 passed、0 failed、1 warning**；JUnit／log SHA-256 已寫入 repo 外報告。
- `feature-check.py --run-tests`：15 項 required、**18 個綁定節點全部 passed**（無 skip／xfail／fail）。
- D4-A `check_public_history.py --all-refs`：230 commits／1,110 blobs、**credentialFindings=0**、
  selfHostFindings=35（已知私人部署線殘餘風險，只列類型）。
- focused：`test_workflow_security.py`＋`test_private_raw_sink.py`＋`test_emsd_raw_receipt.py`
  37 passed；`test_pages_deploy.py`＋`test_verify_deploy_request.py`＋`test_receipt_metadata.py`＋
  `test_pages_artifact_e2e.py` 97 passed。
- merge push fail-closed 實測：`build_pages_artifact --check-only` rc=1（版本＋payload hash 錯配）。
- 本輪**沒有**修改 production payload；acceptance／history report 一律寫 repo 外；
  worktree 跑完 gates 後 clean。

### 14.4 仍未執行（UNKNOWN）

- merge PR #10、Pages Source 切換、首次 daily、`repository_dispatch` E3、live E4、tag／Release。
- D7 remote provider 選擇＋Secrets（repo 目前 Secrets 只有 BigGo／PricesAPI；未讀取／未新增）；
  require 模式仍未啟用，公開 raw receipt 未產生。
- 首次真實 `release` environment approval flow。

### 14.5 下一階段（由獲授權執行者；觀察標準）

1. 人手切 Pages Source＝GitHub Actions；確認 D19 `release` environment reviewer 仍在。
2. merge PR #10；預期 merge push Pages run 於 `build_pages_artifact` fail-closed（唔會 deploy）。
3. 手動 dispatch daily（master）；觀察：gates 全過、新 commit 帶 1.2.9 metadata
   （`commit` == source run head_sha、`workflowRunId` == 該 run）、push 成功、dispatch 成功。
4. 觀察 Pages run：`verify_deploy_request` ok、build／deploy success、online metadata ==
   checkout metadata（整個 object）、UI version 1.2.9／Last Deploy／Last Update 正確。
5. 觀察 postdeploy-verify success（E4）；有 issue 即停。
6. E4 後先 tag `v1.2.9`＋`release-archive.yml publish=true`（release environment 人工批准）；
   核對 CHECKSUMS／PROVENANCE 同 tag／commit 對應。任何一步失敗：停，唔好靠 bypass 或改 metadata。

### 14.6 新提交 CI 核對（OBSERVED / E3）

- PR head `174dcc2363e21d23cb8f39614208b88617d2fc4e`（即 14.1 修正＋D20＋本節 §14 之後嘅
  push）：
  - `每日偵測 · 新機分批更新` → `pull-request-gates` run `35874993642`：**success**（2m31s）；
    `update` job 正確 `skipping`（PR 唔會跑生產更新）。
  - `Pages 部署（Actions）` → `build` run `35874993620`：**success**（2m38s）；`deploy` job
    正確 `skipping`（PR 永不 deploy）。
- 以上只係 PR build／gate 嘅 E3；production `repository_dispatch` 部署路徑同 live E4 仍未發生。
