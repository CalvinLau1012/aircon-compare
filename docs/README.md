# 項目文件導覽

> **2026-10-26 現行入口（append-only）**：本輪已同步網站更新日誌、部署／來源／BigGo／官網待核／測試驗收文件，並修正私人站文件導覽連結。最新 OBSERVED 事實見 [STATUS §32](STATUS.md#32-2026-10-26-現況同步append-only)；逐份文件的 action／理由見 [DOCS_SYNC_INVENTORY_20261026.json](DOCS_SYNC_INVENTORY_20261026.json)。以下 2026-10-01／09-23 段落保留為歷史，不代表現況。

## 2026-10-26 權威來源與文件層級

| 層級 | 文件 | 角色 |
| --- | --- | --- |
| 規範 | [AIRCON_COMPARE_GOVERNANCE.md](AIRCON_COMPARE_GOVERNANCE.md) | 唯一機器規範源；本輪無 Schema／Registry／門禁變更，故不改內文。 |
| 現況 | [STATUS.md](STATUS.md) §32 | 指定觀察時間的 OBSERVED／UNKNOWN；不是即時監控。 |
| 決策 | [DECISIONS.md](DECISIONS.md) D42 | 本輪文件、網站日誌、PDF 連結及 private 導覽決策；舊決策保留。 |
| 需求 | [../需求摘要.md](../需求摘要.md) | 公開需求摘要；2026-10-26 追加節為最新同步。 |
| 變更 | [../CHANGELOG.md](../CHANGELOG.md) | release／部署／文件變更索引；`[Unreleased]` 舊段保留歷史。 |
| 網站日誌 | [../空調對比報告.md](../空調對比報告.md) | 網站 HTML／PDF 更新日誌的唯一內容來源；生成物不手改。 |
| 歷史 | ADR、`architecture/2026-10-18-*`、`architecture/2026-10-19-*`、blacklist migration | 寫作當日快照；保留原文，不當現行狀態。 |

**本輪網站連結政策**：私人站只服務四檔與 runtime JSON，不提供 repo `.md` 檔；因此站內文件導覽必須指向可公開解析的 GitHub 文件 URL，不可使用同源 `README.md`／`docs/*.md`。公開站與私人站共用生成器／報告來源，但各自用已部署 metadata 顯示資料日期。`scripts/check_site_links.py` 是同源內部連結回歸入口；外部 URL 只列清單，不因反爬或暫時錯誤當成內部壞鏈。


> **本轮文件／版面已部署（2026-10-01）**：[PR #23](https://github.com/CalvinLau1012/aircon-compare/pull/23) 已合併；[重建 run 36817792681](https://github.com/CalvinLau1012/aircon-compare/actions/runs/36817792681) 與 [Pages run 36817991532](https://github.com/CalvinLau1012/aircon-compare/actions/runs/36817991532) 均成功。 獨立線上 26/26 pass；最新完成狀態見 STATUS §29.5。元文件標題已採用「對比目的」；下方候選或舊核對基準保留為歷史。

## 最新閱讀入口（2026-10-01 追加）

**v1.2.9 已正式發布，PR #19／#20／#22 維護修復已部署。** 下方 9 月 23 日介紹與候選表是歷史快照；最新完成事實見 [STATUS §29](STATUS.md)，最新選擇見 DECISIONS D31／D32，正式版本與維護記錄見 CHANGELOG 發布與部署索引。

- 目前資料核對：2026-10-01，1,816 登記／1,755 型號；即時部署以線上 metadata 為準。
- raw sink 已有首次 live 驗收；Pages Actions 與 E4 已完成；官網純 coverage pending 分流已生效。不是舊表列出的「未部署」。
- active BigGo 跨 writer live、官方配額、私人 server 正式更新仍未驗收；不能由單元測試代替。
- 治理規範與 blacklist migration 歷史檔的角色不變，不改寫原規範或歷史遷移數字。


本目錄分開保存規範、決策、歷史證據與目前狀態。文件核對日期：2026-09-23；v1.2.9 候選再加入 D1-B／D2-A／D4-A／D7-A／D8-A 及 PR #10 返修（D18），全部本機 E2，未 merge／deploy。

| 文件 | 用途 | 閱讀方式 |
| --- | --- | --- |
| [AIRCON_COMPARE_GOVERNANCE.md](AIRCON_COMPARE_GOVERNANCE.md) | 唯一治理源；六個規範區塊、15 項 required 功能、Metadata Schema、成功標準 | REQUIREMENT；規範要求不等於已實現 |
| [DECISIONS.md](DECISIONS.md) | D1–D18 已記錄決策及原因（D17 為 2026-09-22 D1-B..D9-A；D18 為 PR #10 返修） | 保留決策日期；實施狀態須配合證據核對 |
| [STATUS.md](STATUS.md) | 指定基準的部署、資料、候選實作與差距 | OBSERVED / UNKNOWN；不是另一份治理規範或即時監控 |
| [blacklist-migration-2026-09.md](blacklist-migration-2026-09.md) | 2026-09-03 canonical key 遷移紀錄 | HISTORICAL_CLAIM；不得當作最新停售數字 |
| [GOVERNANCE_MATRIX.md](GOVERNANCE_MATRIX.md) | GATE-01..09／15 features／SC-001..016／TODO 要求—證據—缺口矩陣 | 現況盤點；未取得 E3/E4 就標 UNKNOWN |
| [adr/ADR-001-raw-emsd-snapshot.md](adr/ADR-001-raw-emsd-snapshot.md) | 原始 EMSD HTTP response 快照設計 | D7-A 已批准；本機候選已實作，真正 private sink／live snapshot 待平台 |
| [adr/ADR-002-official-batch-advance-policy.md](adr/ADR-002-official-batch-advance-policy.md) | 官網批次 queue model 未覆蓋時嘅推進政策 | D1-B 已批准；pending coverage 候選已實作，硬失敗仍阻斷 |
| [adr/ADR-003-pages-envelope-and-daily-handoff.md](adr/ADR-003-pages-envelope-and-daily-handoff.md) | Pages deployment envelope 分層＋daily→Pages dispatch 銜接 | D2-A 實作返修；本機候選，未 E3／E4 |
| [adr/ADR-004-pluggable-private-raw-sink.md](adr/ADR-004-pluggable-private-raw-sink.md) | 可插拔私人 raw sink（local／GitHub Release asset 候選） | D7-A 實作返修；remote live 啟用未批准（UNKNOWN） |
| [PRIVATE_RAW_SINK_RUNBOOK.md](PRIVATE_RAW_SINK_RUNBOOK.md) | 私人 raw sink 啟用前置、首次 live 驗收與回滾 | 未完成前唔可以講 remote 完成 |

產品說明見 [README](../README.md)，需求見 [需求摘要](../需求摘要.md)，變更歷史見 [CHANGELOG](../CHANGELOG.md)。

## 更新方法

- 修改要求以治理源為準；不得為符合現有程式而降低 required 功能或門禁。
- 決策記錄保留原決策日期；補記狀態時寫明核對日期與提交／測試依據，不虛構新的批准。
- 狀態記錄必須標明 commit、資料口徑、觀察日期與證據限制；快照日期不代表每個價格或規格都在當日重查。
- 產品版本唯一手動來源是 `models_data.py` 的 `VERSION`；治理文檔版本獨立。每日資料更新及本次修復候選不虛構新產品 Release。
- 生產 metadata 只能由流水線生成；本機候選 HTML／PDF 變更後，不能沿用舊 hash 宣稱已發布。

## 本機檢查

```bash
python scripts/extract_governance.py           # 六規範區塊 + Registry/成功標準完整 Schema
python scripts/feature-check.py --run-tests    # pytest collection + 實際執行證據（skip 即失敗）
python -m pytest tests/ -q
python validate_data.py
python scripts/validate_metadata.py            # 正式 metadata（--core 驗未 finalize 核心事實）
python scripts/postdeploy_check.py --help      # GATE-08 部署後核對（本機可對 localhost fixture）
python scripts/check_public_privacy.py --mode worktree   # 公開私隱（tracked+untracked；CI 用 --mode index）
python scripts/archive_release.py --help       # GATE-09 長期歸檔（同 tag 唔同 bytes 拒絕 clobber）
python scripts/check_public_history.py --all-refs --report <repo 外 path>  # D4-A 全歷史秘密審計
python scripts/check_freshness.py --metadata-file metadata.json --report <repo 外 path>  # D8-A 72h
python scripts/build_pages_artifact.py --check-only  # D2-A Pages artifact manifest 驗證（需 payload 同 metadata 一致；PR 用 scripts/make_fixture_release.py）
python -m pytest tests/test_restore_drill.py -q  # 公開 fixture-only 應用＋數據恢復演練
```

上述命令各有範圍：`feature-check.py` 需要 Playwright 先可以跑瀏覽器綁定；正式 Metadata 驗證需要 `jsonschema[format]`；部署後核對與歸檔要受信任環境同實際部署先有 E4 證據。限制與待辦見 STATUS。

## 2026-09-24 狀態更新（追加；上文 2026-09-23 記述原文保留）

- **v1.2.9 已發布並上線**：tag `v1.2.9` → `f546e2f`；GATE-09 Release run 35886358387
  success（`archive-v1.2.9.zip`／`CHECKSUMS.sha256`／`PROVENANCE.json`，24 個 CHECKSUMS
  獨立重算全 OK）；production daily 35942488710 → build `B20260924.106.1`，其後自動更新
  35946820790 → build `B20260924.110.1`（deployTime 2026-09-24T02:24:19Z）。
- **E3／E4 已取得**：Pages production run 35944781623 依序 build → deploy → 同 workflow
  GATE-08 全部 success；手動 exact-ref fallback run 35943298448 全 PASS。
- **決策範圍**：`DECISIONS.md` 已追加至 **D24**（D19 `release` reviewer、D20 發布授權、
  D21 Node24／bounded smoke、D22 GATE-08 同 workflow、D23 BigGo 按需、D24 md-only commit）。
  上文表格「D1–D18」同 ADR／runbook「候選、UNKNOWN」字句係 2026-09-23 當時記述，現況以本節
  同 `STATUS.md` §15–§17、`GOVERNANCE_MATRIX.md` §10 為準。
- **ADR／RUNBOOK 現況**：ADR-001／D7-A raw sink 已 live（公開收據 `durableRemote=true`）；
  ADR-002／D1-B 已上線；ADR-003／D2-A 已取得 E3／E4；ADR-004 remote live 已啟用；
  `PRIVATE_RAW_SINK_RUNBOOK.md` 啟用前置已完成，回滾章節仍有效。
- **文檔政策（2026-09-24 用戶指示）**：文檔只可追加、不可刪除或改寫既有記述；歷史快照保留，
  更新以追加節記錄；需求以 `需求摘要.md`（元文件）為準。見 `docs/DECISIONS.md` D25 同
  `AGENTS.md` 規則 10。

## 2026-10-12 最新入口（追加）

**現行不可變 payload：v1.2.9／build `B20261010.142.1`，artifact 來源 `7994e7b`（非 merge commit）。**
[PR #24](https://github.com/CalvinLau1012/aircon-compare/pull/24) 已合併（merge `742404b`）；
[Pages run 38185435570](https://github.com/CalvinLau1012/aircon-compare/actions/runs/38185435570)
success（GATE-08 37 checks／14 browser）。BigGo 唯讀診斷
[run 38187063148](https://github.com/CalvinLau1012/aircon-compare/actions/runs/38187063148)
success（恰好 1 GET、token／search 0、identity 與 server 一致；不證明寫入權限／provider 連線）。
daily workflow `335052362` 已 active（UTC cron `30 16`＝HKT 00:30 預定觸發）。

- 上方「最新閱讀入口（2026-10-01）」及表中「未 merge／未 E3／E4」字句屬歷史；最新 OBSERVED／UNKNOWN 見
  [STATUS §31](STATUS.md)，決策背景見 [DECISIONS D35](DECISIONS.md)。
- **排程語義**：GitHub 官方明示 schedule 可於高負載時延遲甚至丟棄；00:30 HKT 唔係準時保證。
  本項目未有延遲量測（UNKNOWN）；唔會自動補跑、重複查價或改 cron。
- **獨立部署**：維護者私人部署已恢復（獨立排程 00:30 HKT 運行中）；同 GitHub Actions 獨立，
  共用 coordinator 只協調 BigGo stage，非整體主備、不保證先後。公開 repo 只泛稱，不記錄私人識別。
- **網頁文案**：報告 md／HTML_TEMPLATE 已改為官方 schedule 語義；本分支未提交 `index.html`／PDF，
  由 D31 verified snapshot rebuild 全量重生後再核對 Pages／GATE-08。
- ADR／runbook 歷史決策文字全部保留；raw sink 契約與 90 日 retention 不變。

## 2026-10-13 追加：繁體書面語閱讀版

- 新增 [AIRCON_COMPARE_GOVERNANCE.zh-Hant.md](AIRCON_COMPARE_GOVERNANCE.zh-Hant.md)：2026-10-13 閱讀版，經使用者審閱，倉庫整合尚待 Root 驗收。
- 原治理 [AIRCON_COMPARE_GOVERNANCE.md](AIRCON_COMPARE_GOVERNANCE.md) 仍為唯一機器規範來源；閱讀版不內嵌機器區塊，只以相對連結指向原治理；原治理文件尾已追加非規範導讀。
- 使用者本次批准只涵蓋文件表述；詳見 DECISIONS D36、CHANGELOG 2026-10-13 及需求摘要。
