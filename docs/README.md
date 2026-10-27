# 公開文件索引

本目錄只收錄公開網站及 GitHub Pages 所需文件。非公開環境資料、設定與運作證據不在公開倉庫保存。

| 文件 | 用途 |
| --- | --- |
| [STATUS.md](STATUS.md) | 最新公開 workflow、Pages、freshness 及 live artifact 狀態 |
| [DECISIONS.md](DECISIONS.md) | 公開資料、測試、發布及隔離決策 |
| [AIRCON_COMPARE_GOVERNANCE.md](AIRCON_COMPARE_GOVERNANCE.md) | 公開機器治理規範 |
| [AIRCON_COMPARE_GOVERNANCE.zh-Hant.md](AIRCON_COMPARE_GOVERNANCE.zh-Hant.md) | 繁體書面語導讀 |
| [GOVERNANCE_MATRIX.md](GOVERNANCE_MATRIX.md) | 要求、gate 與證據矩陣 |
| [ADR](adr/) | 公開架構決策 |
| [architecture](architecture/) | 公開架構說明 |
| [文件同步 inventory](DOCS_SYNC_INVENTORY_20261026.json) | 2026-10-26 文件盤點 |

產品入口見 [README](../README.md)，更新歷史見 [CHANGELOG](../CHANGELOG.md)，公開需求見 [需求摘要](../需求摘要.md)，本輪交付證據見 [報告](../reports/DOCS_CHANGELOG_LINKS_20261026.zh-Hant.md)。

## 驗證

```bash
python scripts/check_docs_links.py --root .
python scripts/check_public_privacy.py --mode worktree
python scripts/run_acceptance.py --report /tmp/acceptance.json --log-dir /tmp/acceptance-logs
```

公開文件內的日期、筆數、build 與 run 只作所列發布的當次證據；現行狀態以 live metadata 及最新成功 Actions 為準。
