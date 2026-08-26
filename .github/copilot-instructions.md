# GitHub Copilot 專案指令

修改前完整讀取 `docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源）。

1. 先識別任務模式（ANALYZE / DIAGNOSE / CHANGE / RELEASE / ROLLBACK），並檢查實際倉庫。
2. 區分 `REQUIREMENT` / `HISTORICAL_CLAIM` / `TARGET_STATE` / `OBSERVED` / `UNKNOWN`，不得用歷史聲明或目標狀態代替運行證據。
3. 不得自行禁用、降級或移除 `required` 功能；不得偽造 metadata、測試證據或放寬阻斷門禁。
4. 版本號只修改 `models_data.py` 的 `VERSION`；部署時間／資料日期由受信任流水線生成的 `metadata.json` 提供，不得手填。
5. 人類決策與技術限制導致的轉向，必須在 `docs/DECISIONS.md` 按模板記錄決策與原因。
6. 完成後報告：改動、實際驗證命令與結果、未執行項、功能影響（Registry ID）、數據影響、發布狀態、風險與回滾。
7. 規則衝突或缺少高風險授權時，停止相關動作並請求人類決定。
