# 項目文件導覽

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
