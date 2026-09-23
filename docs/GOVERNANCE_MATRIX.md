# 治理落地矩陣（2026-09-23）

> 本文件係「要求—證據—缺口—修復／待決定—驗收」盤點，覆蓋 GATE-01..09、15 項
> required 功能、SC-001..016 及本階段全部 TODO。**證據等級**：E1 靜態／E2 本機
> 測試／E3 受信任 CI／E4 部署後觀察；未取得嘅一律標 UNKNOWN，不代猜。
> v1.2.9 仍為候選：draft PR #10 已 push；未 merge／deploy、未發 Release。D3-A 平台設定已獨立完成並由 API 回讀。

## 1. GATE-01..09

| Gate | 要求 | 現有實作 | 證據 | 缺口／下一步 |
| --- | --- | --- | --- | --- |
| GATE-01 Governance/Static | 規範區塊可提取、Schema 有效、投影無漂移 | `scripts/extract_governance.py`（6 塊唯一、dup key／NaN／Infinity／未知 marker 拒收、Registry＋成功標準完整 Schema） | E2：`tests/test_schema_validation.py`；實跑退出 0 | 治理 3.1.2 及新增綁定待 PR／Code Owner 評審（E3 未有） |
| GATE-02 Build | 可重複生成 Web/PDF | `generate_html.py`、`generate_pdf.py`（PDF 同輸入 byte-for-byte；HTML LF） | E2：`tests/test_pdf_metadata_guard.py`、`tests/test_governance.py::test_html_output_lf` | CI 實跑屬 E3；候選包未由 CI 建 |
| GATE-03 Feature Contract | required 有有效綁定、真實執行 | `scripts/feature-check.py`＋`pytest_evidence_plugin.py`：18 個綁定節點、subprocess 非零／collectionErrors／缺階段／skip／XPASS／deselect 全部 fail-closed、報告 ok 一致 | E2：`tests/test_feature_check_evidence.py`；`--run-tests` 實跑 | 排名／PDF／版本類節點仍有證據上限（見 §2）；CI E3 未有 |
| GATE-04 Dataset | 來源／結構／日期／Hash／計數 | `fetch_emsd.py`（收據 hash 綁定、交易 journal、失敗收據）、`validate_data.py`（型別／行形狀／數值契約） | E2：`tests/test_emsd_receipt.py`、`tests/test_validate_data_contracts.py` | 原始 HTTP response 快照未保存（見 ADR-001）；CI E3 未有 |
| GATE-05 Test/Smoke | 單元／瀏覽器核心／PDF | `tests/browser_smoke.py`（搜尋／篩選／排序完整性／比較／Escape／響應式／對比／metadata 顯示）、pytest 全套 | E2：本機全套通過（見 docs/STATUS.md） | Chromium 只喺 CI／本機 .venv；未喺 CI（E3）跑 |
| GATE-06 Metadata/Package | 受信任環境唯一 metadata、payload hash | 兩階段 `gen-metadata.py`（收據事實、core fail-closed、finalize 驗 CSV/rawCount、原子輸出、生產必須收據、本地 force 不可覆寫 repo metadata） | E2：`tests/test_metadata_twostage.py`、`tests/test_receipt_metadata.py`；production 必須 receipt、finalize 驗 CSV hash／rawCount | 正式 metadata 由 CI 生成（E3 未有）；核對／封裝順序見 workflow |
| GATE-07 Deploy | 受保護環境部署不可變包 | Pages：daily workflow push 後由平台 Pages 自動部署；私人自建線已移出公開 repo | E1：`.github/workflows/daily-update.yml`（allowlist、push fail-closed） | Pages root auto-deploy 與完整門禁唔係同一條信任鏈（架構缺口，待人類方案）；無 E4 |
| GATE-08 Post-deploy | 線上 metadata／payload／行為一致 | `scripts/postdeploy_check.py`（完整 object＋Schema、payload/CSV hash、PDF 同 metadata 重建一致、瀏覽器、報告寫入失敗非零）、`postdeploy-verify.yml`（接受 Pages `dynamic`、master only、read-only、persist-credentials:false） | E2：`tests/test_postdeploy_check.py` | 未對 live Pages 執行過（E4 UNKNOWN）；local loopback 只屬候選驗證 |
| GATE-09 Release Archive | 可追溯、不可 clobber、報告齊 | `scripts/archive_release.py`（實際 bytes＋zip＋CHECKSUMS＋provenance 分離、嚴格 SemVer、draft/release 等級、私隱掃描）、`release-archive.yml`（pipefail、GH_REPO、tag 指向檢查、publish 無 checkout） | E2：`tests/test_archive_release.py`；2026-09-23 平台 API 回讀 D3-A | 未發布 Release；`release` environment 已設單人 required reviewer，允許維護者自行批准 |

## 2. 15 項 required 功能（Registry）

| ID | 綁定節點（本輪） | E2 證據範圍 | 證據上限／缺口 |
| --- | --- | --- | --- |
| core.search | browser_smoke::test_search_models | 品牌搜尋結果非空且同品牌 | 只覆蓋搜尋契約，未覆蓋所有輸入組合 |
| core.filter | browser_smoke::test_filter_brand | 品牌過濾正確 | 其他篩選維度未有逐項測試 |
| core.sort | browser_smoke::test_sort_price | 排序非空、無重複、結果屬資料集、價格升序 | 分頁只渲染首 60；未驗所有欄位排序 |
| core.compare | browser_smoke::test_compare_modal | 比較面板開啟、行數 | 未逐欄比對原始記錄 |
| ui.comparison-modal | test_compare_modal ＋ test_compare_modal_escape_keyboard | 開啟、Escape、焦點、aria | 未做完整鍵盤巡覽 |
| ui.responsive | test_responsive_no_overflow ＋ test_tooltip_no_horizontal_overflow | 320–1280 無水平溢出 | 未覆蓋所有斷點／RTL |
| data.emsd-verification | test_core::test_load_models_dedup ＋ test_emsd_receipt::test_exact_multiple_of_50_pages | 去重、來源閘門、頁數收據、hash 綁定 | 原始 HTTP response 未保存（ADR-001） |
| core.ranking | test_governance::test_ranking_recommendation_sections | 章節存在、引用來源、多數型號對得上資料 | 本質屬歷史人工推薦；無演算法可重現排名證據 |
| core.recommendation | 同上 | 同上 | 同上；不擅改推薦規則 |
| report.pdf-export | test_pdf_export ＋ test_pdf_consumes_core_metadata_not_repo_metadata | 有效 PDF、可重現、同 run metadata、version 影響內容 | 未用 PDF parser 逐頁驗內容（PDF CID 字型）；以重建一致性代替 |
| operations.version-display | test_version_single_source ＋ test_metadata_display_and_fractional_time ＋ test_metadata_failure_no_hardcoded_values | 單一來源、runtime 顯示、失敗不硬編 | 未對 live Pages（E4） |
| operations.last-deploy | test_format_status_metadata_driven ＋ test_metadata_display_and_fractional_time | HKT 轉換、小數秒、失敗暫不可用 | 同上 |
| operations.dataset-update | test_format_status_metadata_driven ＋ test_metadata_display_and_fractional_time | 讀 datasetDate、不靜態回退 | 同上 |
| operations.build-metadata | test_metadata_generate_and_validate ＋ test_core_receipt_cli_overrides_wrong_date_and_hash | 生成→Schema→收據事實 | CI 實跑（E3）未有 |
| operations.github-pages-deploy | browser_smoke::test_search_models ＋ test_version_single_source | 本機生成物行為 | 唔等於 Pages 部署證據；需 GATE-08 live（E4） |

## 3. 成功標準 SC-001..016

| SC | 狀態 | 證據／缺口 |
| --- | --- | --- |
| SC-001 protected features | E2（部分） | 18 綁定節點 passed；部分功能上限見 §2；CI E3 未有（本輪無新增 Registry binding） |
| SC-002 last-deploy | E2 | 單元＋瀏覽器顯示；live E4 UNKNOWN |
| SC-003 last-update | E2 | 同上；PDF 同 metadata 重建一致 |
| SC-004 unified metadata | E2 | Schema＋consumer 追蹤測試；平台部署 E4 UNKNOWN |
| SC-005 dataset provenance | E2（部分） | 收據 hash 綁定；原始 HTTP response 快照未保存（ADR-001） |
| SC-006 CI gates | UNKNOWN | 本機 E2 全綠，受信任 CI（E3）未跑 |
| SC-007 post-deploy | UNKNOWN | 工具＋fixture；live 未核對（E4） |
| SC-008 release assets | 候選 | archive 工具＋release workflow；未發布、無 release asset |
| SC-009 rollback drill | E2 | 公開 `tests/test_restore_drill.py`；真實生產 apply／rollback 仍待維護者 |
| SC-010 AI entrypoints | E2 | AGENTS／Copilot 入口一致＋drift 檢查（`tests/test_ai_entrypoints.py`） |
| SC-011 traceability | 部分 | provenance commit/run/build/tag；Release／Deployment 未產生 |
| SC-012 governance blocks | E2 | 提取器＋Schema 測試；治理評審未完成 |
| SC-013 test integrity | E2 | feature-check 拒絕 skip／常量斷言／吞例外；無未批准 skip |
| SC-014 reproducible build | E2 | HTML／PDF 同輸入重建一致；CI release payload checksum 未封裝 |
| SC-015 trust boundary | E2（靜態） | 工作流限制 master／read-only／SHA pin；平台保護設定 UNKNOWN |
| SC-016 AI report | E2 | 本輪交付報告格式；人工評審未做 |

## 4. TODO 盤點（原 27 行／22 檔）

| TODO | 結果 |
| --- | --- |
| `PENDING-CI-BLOCKER`（Chromium 喺第一次 pytest 之後） | **已修**：Chromium 提前且只安裝一次；第一次 pytest 已包含 postdeploy 瀏覽器案例（`tests/test_workflow_security.py` 靜態驗證） |
| `PENDING-PRIVACY-GATE`（規則唔覆蓋自建線／untracked） | **已修**：gate 加 `tracked/index/worktree/tree` 模式、精確自建線標識、staged index blob 掃描、git 失敗 fail-closed；CI 用 `--mode index`；tests 覆蓋 6 種情況 |
| `PENDING-PRIVACY-SELFHOST` 系列（20 個檔案） | **已處理（候選）**：`docker/`、`release/` 及私人文件段落移出公開工作樹，暫存於 repo 外可恢復副本（未係長期儲存；commit／push 前須轉移＋核驗），詳見 `docs/STATUS.md` §7；公開文件只留中性註記 |

其他盤點（非 TODO marker 但審查發現）：archive 目錄篡改／tag-version／`validate_output(true)`／feature-check exit=2 四個缺陷已修並有負向測試；BigGo partial 推進、EMSD sidecar 半更新、PDF 靜默 `{}`、extractor NaN 均已有回歸。

## 5. 監控、平台與未知項（待人類／平台確認）

| 項目 | 狀態 | 原因 |
| --- | --- | --- |
| 監控頻率、新鮮度閾值、告警接收者／靜默 | UNKNOWN | 屬平台／組織設定，repo 無證據，不虛構 |
| Pages root branch auto-deploy 與完整門禁關係 | 缺口 | 平台部署唔經 GATE-01/03/05/06；修復需改 Pages 設定（未授權） |
| protected environment `release` required reviewers | OBSERVED / D3-A | 2026-09-23 GitHub API 回讀：reviewer `CalvinLau1012`、`prevent_self_review=false`、`can_admins_bypass=true`；workflow publish job 使用 `environment: release` |
| Git 歷史仍含私人部署線 | 待人類私隱評估 | 工作樹移出唔改寫 history；改寫／轉私人 repo 需人類決定 |
| Release／Deployment／E3／E4 | UNKNOWN | 未執行外部發布與部署 |
| 原始 EMSD HTTP response 快照與原始 hash | 缺口 | 見 `docs/adr/ADR-001-raw-emsd-snapshot.md`（候選設計，未改資料契約） |


## 6. 2026-09-22 第四輪更新（GATE-03／05／09 相關）

- GATE-03：feature-check 全面 fail-closed（subprocess rc、collectionErrors、缺階段、
  skip／XPASS／deselect、報告寫入失敗）；Registry bindings 無新增、protection 無降級。
- GATE-05／staging：`stage_artifacts.py` 拒絕 deletion／rename、manifest 用共用契約、
  stage 前驗 index；零部分 stage。
- GATE-09：release 驗收改為 machine acceptance runner（gate rc／argv／UTC／log sha256＋
  JUnit）＋feature／postdeploy 結構驗證；拒任意文字報告。
- 官網批次：wrapper 出 machine receipt、queue hash／output hash／coverage 綁定；
  queue model 無法由現有品牌目錄覆蓋時 fail-closed 保留 —— **TARGET_STATE／待決定**，
  見 `docs/adr/ADR-002-official-batch-advance-policy.md`（A 阻斷／B 發布但 queue pending／C 擴來源）。
- 測試實數：以 repo 外 machine acceptance manifest 為準（R4 當時 368 passed、
  feature-check 18 節點；R5 實數見 §7）；本文件唔再維護逐檔測試數，避免漂移。


## 7. 2026-09-22 第五輪精確返修

- staging parser：`git diff --cached --name-status -z` 嚴格 NUL token 解析；deletion／
  rename／truncated／unknown／invalid UTF-8 fail-closed；index snapshot＋原子恢復。
- release report path trust：`safe_report_path`（canonical、拒 traversal／absolute／
  backslash／symlink）；required machine report exactly-one；acceptance identity 驗證。
- official receipt：strict marker＋output evidence 綁定；ready receipt 先寫再 advance。
- acceptance runner：CLI 只固定 gate set；ID 安全 unique；未知 `--only` 非零；evidence
  必須 repo 外。
- prices_meta：真實日曆日期／時間驗證；`detect_mode` fail-closed。
- 測試實數：**399 passed**、feature-check 18 節點（以 machine manifest 為準）。

## 8. 2026-09-22 第六輪：D1-B／D2-A／D4-A／D7-A／D8-A

- **GATE-07／D2-A**：新增 `pages-deploy.yml`（PR 只驗證、master deploy `needs` build、
  `environment: github-pages`、`pages:write`＋`id-token:write`、Actions 全 SHA pin）；
  `build_pages_artifact.py` 只接受 `deploy_payload.json` 公開 allowlist，拒 symlink／
  traversal／缺檔／額外私人檔；`postdeploy-verify.yml` 綁 `Pages 部署（Actions）`
  嘅 exact `head_sha`，不再 fallback 舊 dynamic run。平台切換 Pages Source 未做，E3／E4
  仍 UNKNOWN。
- **GATE-04／D7-A**：`fetch_emsd.py` 保存逐頁實際 HTTP bytes 證據（byteLength／SHA-256／
  Last-Modified／ETag）；整批成功＋資料提交後原子寫公開 `emsd_raw_receipt.json`（只含 hash），
  成功 CSV 收據回寫 `rawReceiptHash`；私人 sink 介面在 repo 外、require 模式失敗即阻斷，
  90 日 retention 邊界已測。真正 private sink Secret／首個 live snapshot 未設定，UNKNOWN。
- **D1-B**：`run_official_batch.py` 硬失敗 vs coverage pending 分流；pending 保留 queue
  stage／models、出 `queue-kept-pending-coverage` decision；`publish_official_status.py`
  投影公開狀態，`generate_html.py` 顯示「官網規格待核」。ADR-002 由提案變已批准實作候選。
- **D4-A**：`check_public_history.py` 掃全 reachable refs／commit blobs；實跑 208 commits、
  934 blobs、credentialFindings=0，self-host 32 findings 作 residual risk。報告唔含秘密原文。
- **D8-A**：`check_freshness.py` threshold=`age > 72h`（72:00:00 pass）；`freshness-monitor.yml`
  每 6 小時＋dispatch；issue 同狀態 noop、狀態改變 update、恢復 close；missing／invalid／future
  timestamp fail-closed；線上 schema／payload hash 由 postdeploy_check 先驗。
- **D5-A**：私人 repo（名稱只喺交付報告）visibility=PRIVATE；來源包
  SHA256SUMS 先驗，push 後 fresh clone 逐檔 checksum 通過；Temp 原件保留。
- **D3 當時 pending**：本節記錄 2026-09-22 快照；其後使用者於 2026-09-23 選 D3-A，
  平台設定及 API 回讀已完成（見 `docs/DECISIONS.md` D19）。
- **D6 deferred**：self-host 線同步待公開 PR merge 後另開私人 repo 工作，公開 PR 不含私人檔案。

### 8.1 第六輪實數

- pytest 433 passed／1 skipped；focused 69 passed／1 skipped；feature-check 18 nodes；
  machine acceptance 7 gates rc=0（manifest repo 外；`ok=true`）；history audit 208 commits／
  934 blobs、credentialFindings=0、selfHostFindings=32。D3／D6／E3／E4 未完成。

## 9. 2026-09-23 PR #10 審查返修（D2-A／D7-A／D8-A 實作）

> 本地候選；未 merge、未部署、未跑 E3／E4。18 個 required feature nodes 同 7 個
> acceptance gates 無降級。

| 範圍 | 契約 | 證據 |
| --- | --- | --- |
| GATE-07 Pages artifact | `deploy_payload.json` 保持唯一 hash 範圍；`deploy_envelope.json` 加 metadata.json／一致 sidecar；封包前 Schema／version／hash／counts 錯配 fail-closed | `tests/test_pages_deploy.py`、`tests/test_pages_artifact_e2e.py`（真 HTTP＋Chromium＋PDF 重建） |
| GATE-07 deploy | PR 只 fixture 驗證；master push／master dispatch／已驗證 `repository_dispatch` 才 deploy；source run completed/success＋run_attempt／workflow path／direct parent（單親）／metadata `workflowRunId`＋`commit` 綁定，queued／in_progress 有界 polling；postdeploy 綁 Pages run head_sha 並驗祖先 | workflow 契約測試；`tests/test_verify_deploy_request.py` fake API 負向測試 |
| GATE-07 concurrency | Pages production／PR 分組；`cancel-in-progress: false`＋`queue: max`（預設 single 會以新 pending 覆蓋舊 pending）；PR 唔可以取消／阻塞生產 | workflow 契約測試 assert `queue: max` |
| GATE-07 out safety | 拒 repo 根／祖先／`.git`／link／重疊／未封印目錄；staging 安全替換，失敗唔刪既有內容 | 5 組 out safety 測試（temp-only） |
| GATE-08 | freshness＋postdeploy combined health；fingerprint 不含 ageSeconds；分類改變才 update；完全恢復 close；network／schema／API error 非零 | `tests/test_freshness_monitor.py`（main + fake GitHub API，不發真 issue） |
| D7-A raw sink | local adapter 如實標示非 durable；GitHub Release asset adapter PRIVATE 回讀／拒覆蓋／下載 hash 核驗／90 日 retention；require 缺配置阻斷 | `tests/test_private_raw_sink.py`（fake HTTP）；`docs/PRIVATE_RAW_SINK_RUNBOOK.md` |
| 私隱 | raw bytes／token／私人路徑唔入公開 log／worktree／artifact；公開 raw receipt 只有 datasetHash＋CSV 收據 rawReceiptHash 一致才收錄 | `check_public_privacy.py` gate rc=0；sink 測試檔內容掃描 |
| History audit | 全 reachable refs credentialFindings 必須 0 | commit 前 216 commits／1042 blobs、0 findings（commit 後再跑最終） |
| R7 source run 綁定 | completed polling／run attempt／daily workflow path／單親 direct parent／metadata `workflowRunId`＋`commit`；timeout／mismatch 全部 fail-closed | `tests/test_verify_deploy_request.py` 21 cases（fake API，不真等） |
| R7 base 同步 | merge `origin/master` `be43b7c`；資料檔以 master 流水線事實為準；生成物用合併後程式重建 | `git merge-base --is-ancestor origin/master HEAD`；docs／PDF／index diff |
