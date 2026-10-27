# ❄️ 香港空調對比報告

香港窗口式及分體式空調的公開資料整理、篩選與比較網站。

- 公開網站：<https://calvinlau1012.github.io/aircon-compare/>
- HTML：[`index.html`](index.html)
- PDF：[`空調對比報告.pdf`](空調對比報告.pdf)
- CSV：[`emsd_空調能源標籤.csv`](emsd_空調能源標籤.csv)
- Metadata：[`metadata.json`](metadata.json)

## 最新公開狀態

2026-10-27 當次證據：v1.2.9、build `B20261026.197.1`、1,782 筆 EMSD 登記、1,721 個型號。自動重建 run `40420036460`、Pages run `40421702984` 與 freshness run `40488083837` 均成功；四檔與指定 artifact commit `b1fcb68` 逐 byte 相同，Chromium GATE-08 通過。

時間敏感數值只代表該次正式發布。網站會從 metadata 顯示現行資料日期、版本及來源狀態，不以 README 的歷史數字取代 live 狀態。

## 資料與時間語義

- EMSD 欄位以香港機電工程署公開資料為基礎。
- 品牌官網資料只在有可驗證來源時補充；確實未刊登的欄位保持待補，不猜測。
- 價格是分批快照；沒有可信原始取得時間時明確顯示「未確認」，不以套用或部署時間補造。
- 每日 `00:30 HKT` 是預定觸發時間，不是資料日期，也不保證平台準時開始。

## 動態與固定值

- 必須動態：dataset date、登記／型號數、build、commit、run ID、來源時間、stage／status 及最新日誌日期。
- 可以固定：隔離單元 fixture 的 sentinel、72 小時 freshness 門檻、介面尺寸上限、hash／收據 fail-closed 規則，以及每個公開 artifact 的精確 source pin。

## 公開／非公開隔離

公開倉庫只保存公開網站所需的程式、資料、文件與驗收證據。非公開環境的資料、設定、識別、hash、收據、路徑及運作證據不會收錄。

## 文件

- [文件索引](docs/README.md)
- [公開狀態](docs/STATUS.md)
- [公開決策](docs/DECISIONS.md)
- [更新紀錄](CHANGELOG.md)
- [公開需求摘要](需求摘要.md)
- [文件／連結交付報告](reports/DOCS_CHANGELOG_LINKS_20261026.zh-Hant.md)

## 本機驗證

```bash
python scripts/check_docs_links.py --root .
python scripts/check_public_privacy.py --mode worktree
python scripts/run_acceptance.py --report /tmp/acceptance.json --log-dir /tmp/acceptance-logs
```

正式發布仍須由 GitHub Actions 重建產物、通過 Pages build／deploy／GATE-08，並核對線上四檔。
