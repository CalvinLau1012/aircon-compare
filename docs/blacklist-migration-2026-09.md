# 黑名單 canonical key 遷移報告

> HISTORICAL_CLAIM：本文件保留 2026-09-03 遷移紀錄，不是今日庫存或停售總數。
> 2026-09-21 文件核對：實施提交 [a6aeab0](https://github.com/CalvinLau1012/aircon-compare/commit/a6aeab0)，決策見 [D11](DECISIONS.md)。目前狀態見 [STATUS.md](STATUS.md)。

- **日期**：2026-09-03
- **遷移前 key 數**：1095
- **遷移後 key 數**：1095
- **matched（品牌已解析）**：1079
- **orphan（品牌未解析 → UNKNOWN|NORM）**：16
- **碰撞**：0（非零會阻斷）
- **備份**：`model_blacklist.json-bak-canonical-migration`
- **model_status.json tracking key**：遷移 1079 個，orphan 15 個

## orphan 清單

- `RC-X7U` → `UNKNOWN|RCX7U`
- `RB-24CC` → `UNKNOWN|RB24CC`
- `RB-12CC` → `UNKNOWN|RB12CC`
- `RB-09CC` → `UNKNOWN|RB09CC`
- `RB-18CC` → `UNKNOWN|RB18CC`
- `RB-07CC` → `UNKNOWN|RB07CC`
- `RB-18MB` → `UNKNOWN|RB18MB`
- `RB-24MB` → `UNKNOWN|RB24MB`
- `RB-12MB` → `UNKNOWN|RB12MB`
- `RB-07MB` → `UNKNOWN|RB07MB`
- `RB-09MB` → `UNKNOWN|RB09MB`
- `RB-24CB` → `UNKNOWN|RB24CB`
- `RB-18CB` → `UNKNOWN|RB18CB`
- `RB-12CB` → `UNKNOWN|RB12CB`
- `RB-09CB` → `UNKNOWN|RB09CB`
- `RB-07CB` → `UNKNOWN|RB07CB`

> 遷移當時 orphan 型號唔喺當時 EMSD CSV / 官方 JSON / 核心 29 入面，頁面唔會顯示，因此唔會影響停售標示；保留喺黑名單以備復核。
