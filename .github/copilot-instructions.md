# GitHub Copilot 專案指令

修改前完整讀取 `docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源）。

1. 先識別任務模式（ANALYZE / DIAGNOSE / CHANGE / RELEASE / ROLLBACK），並檢查實際倉庫。
2. 區分 `REQUIREMENT` / `HISTORICAL_CLAIM` / `TARGET_STATE` / `OBSERVED` / `UNKNOWN`，唔得用歷史聲明或目標圖代替運行證據。
3. 唔得自行禁用、降級或移除 `required` 功能；唔得偽造 metadata、測試證據或軟化阻斷門禁。
4. 版本號只改 `models_data.py` 嘅 `VERSION`；部署時間/資料日期由受信任流水線生成嘅 `metadata.json` 提供，唔得手填。
5. 完成後報告：改動、實際驗證命令與結果、未執行項、功能影響（Registry ID）、數據影響、發布狀態、風險與回滾。
6. 規則衝突或缺少高風險授權時，停止相關動作並請求人類決定。
