# 公開狀態

> 觀察日期：2026-10-27。只描述公開 GitHub、GitHub Pages 及公開四檔；非公開環境狀態不收錄。

## 1. 公開發布狀態

| 項目 | 結果 |
| --- | --- |
| Source merge | PR #34，merge commit `dcc602c03138afe98f0fc5d221821de36251826c` |
| 自動重建 | run `40420036460` success |
| Artifact commit | `b1fcb68d4fb9e8df20767f466f7d64e2859bd3c3` |
| Pages | run `40421702984`，build／deploy／GATE-08 success |
| Freshness | run `40488083837`，configured／ok／fresh，failures 空 |
| Live version | v1.2.9／build `B20261026.197.1` |
| Dataset | 2026-10-27；1,782 筆登記／1,721 個型號 |
| Release payload | `sha256:1d5960b2badee1d3a61655d78ad93002262425a52a07ec6198a18df73e948584` |

公開 HTML、PDF、CSV、metadata 與 artifact commit 逐 byte 相同。Root Chromium 部署後檢查涵蓋搜尋、品牌篩選、價錢排序、比較視窗、Escape、響應式、console 及 failed request，全部通過。

## 2. Fail-closed 證據

第一輪重建 run `40418450703` 在最終 push 遇 GitHub HTTP 500；所有 gates 雖已通過，工作流仍正確停止，沒有 dispatch Pages 或發布未推送 commit。第二輪從最新 master 重新建包後成功。

## 3. 動態值與固定契約

- 動態：dataset date、取得／套用時間、登記／型號數、build、commit、run ID、stage、來源狀態及最新日誌日期。
- 隔離 fixture：可使用固定 sentinel 驗證 schema、渲染及錯誤拒絕，但不得與 live candidate 綁定。
- 固定契約：`00:30 HKT` 排程、72 小時 freshness、介面尺寸上限、hash／收據 fail-closed，以及每個公開 artifact 的精確 source pin。

## 4. 來源狀態

- EMSD：公開 metadata 顯示成功套用及 hash-bound snapshot。
- 官網補充規格：可信缺刊保持 pending；真正取得、TLS、parser、收據、hash 或發布錯誤仍須失敗。
- 價格：分批快照；原始取得時間缺可信紀錄時顯示 unknown，不以其他時間補造。

## 5. 公開資料邊界

- 本輪文件發布沒有重新取得 EMSD、品牌官網或價格資料。
- 沒有修改公開自動化設定或 `00:30 HKT` 排程。
- 非公開環境資料、設定、識別、hash、收據、路徑及運作證據不進入公開 repo 或網站。

## 6. 最新本機候選驗收

- 完整 7 gates：全部 rc=0。
- Pytest：1,419 tests，零 failure／error／skip。
- Chromium 文件／網站／PDF／更新日誌／hero：19/19。
- Markdown：25 files、76 links、19 fragments、0 broken。
- 公開私隱 gate：0 命中。

本節是候選證據；正式更新仍須經 PR exact-head CI、重建、Pages、GATE-08 及 freshness 再次確認。
