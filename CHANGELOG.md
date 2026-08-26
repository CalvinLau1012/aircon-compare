# Changelog

本文件記錄 aircon-compare 的所有顯著變更（Keep a Changelog 風格）。
版本號遵循 SemVer（`MAJOR.MINOR.PATCH`），唯一手動來源為 `models_data.py` 的 `VERSION`。

## [Unreleased]

## [1.2.8] - 2026-08-26

### Added

- 治理落地：`docs/AIRCON_COMPARE_GOVERNANCE.md`（唯一治理源，內嵌六個規範區塊）
- 決策記錄 `docs/DECISIONS.md`（人類決策項與技術轉向，D1-D10）
- PDF 報告導出（`generate_pdf.py`，reportlab 內置中文字體）
- `scripts/extract_governance.py` / `scripts/feature-check.py` / `scripts/gen-metadata.py` / `scripts/validate_metadata.py`
- CI 門禁 GATE-01/03/05/06（Block 級）+ 頁面 runtime fetch `metadata.json`
- BigGo 官方認證（client credentials → access_token，55 分鐘快取；免登入通道關閉後轉向，D10）
- README 數據統計章節（狀態/機型/能源/類型/匹數/價位/品牌分佈 mermaid 圖）+ 治理架構一覽與 DevOps 目標清單
- 治理文檔四類架構圖（整體架構/AI 流程/AI Agent 權限/DevOps 流水線）

### Changed

- BigGo 價源：網頁 scrape → 官方 JSON API（`api.biggo.com`，D1）
- 說明文檔全面改為書面語（README/需求摘要/要求/報告/AGENTS/CONTRIBUTING/copilot-instructions）
- 決策 D2：日常價錢更新全自動；啟動初期一次性數據可本地處理後上傳（D9）
- `fetch_biggo.py`：全局冷卻 + 最小請求間隔 + 降並發（D3）

### Fixed

- 比較器「狀態」（有價/官方價/無價/停售）與「價位」篩選恢復（v1.2.5 功能於皮膚重構時遺失）
- 治理 marker 前綴、metadata pattern、subprocess 編碼等多個 CI 問題

### Data

- BigGo 有價型號 731 → 742（本地全量復核，零網絡錯誤）
- 淘汰黑名單 1,103 → 1,095（復活 8 個重有市售報價型號）

## [1.2.7] - 2026-08-25

### Changed

- 皮膚/深色模式全面恢復（Blue Fantasy 壁紙 + whale-girl 吉祥物 + 元件級對比度）
- 響應式適配修正（手機下拉溢出/吉祥物重疊，8 裝置 × 深淺色 16 組合測試）

### Fixed

- `SPECS_OVERRIDE` 9 個重複 key（P0）

## [1.2.6] - 2026-08-25

### Changed

- 代碼重構：抽離 `models_data.py`、`batch_utils.py`；`fetch_*` 統一 `crawl_utils.fetch`
- 深色模式對比度修正（目錄/表格 hover/code/引用/卡片/按鈕）
- 網頁 hero 顯示成功更新時間（`last_deploy` 香港時間）

### Fixed

- `generate_html.py` 黑名單／價格 JSON 只讀一次（生成效能）
- `fetch_emsd.py` 安全閘門（抓不齊不覆寫）+ 原子寫入

## [1.2.5] - 2026-08-18

### Changed

- 停售/官方價/有價/無價狀態標籤 + 價位標籤；黑名單以「停售」展示

## [1.2.4] - 2026-08-18

### Data

- 本地全量 BigGo 搜索：有價 734；第一版淘汰黑名單 1,103 個

## [1.2.3] - 2026-08-18

### Added

- BigGo 線上 smoke 防護；型號淘汰黑名單機制（`model_lifecycle.py` + `model_blacklist.json`）

## [1.2.2] - 2026-08-16

### Added

- 每日檢查打卡（`last_check`）、BigGo 驗證閘門、`price_utils.py` 重用工具

## [1.2.1] - 2026-08-16

### Changed

- 主力價錢源確定為 BigGo 官方 JSON API；PricesAPI 改核心 29 驗收/後備

## [1.2.0] - 2026-08-16

### Changed

- 主力價錢源改用 PricesAPI（後再調整）；BigGo/Price 舊快照後備

## [1.1.1] - 2026-08-16

### Fixed

- TOSOT/Gree 規格被重複 dict key 覆蓋丟失
- 代碼重構（crawl_utils 共用 + 單元測試 + XSS 加固 + 版本號單一來源）

## [1.1.0] - 2026-08-16

### Added

- BigGo 官方 JSON API 全量實抓（731 型號）+ 深海女仆主題 UI

## [1.0.0] - 2026-08-15

### Added

- 正式版：全量 1,854 型號 + 官網核實 220 + 互動比較器 + 論壇討論精華 + GitHub Pages

## [0.2.0] - 2026-08-12

### Data

- EMSD 全量 1,927 型號核實（能源/雪種/耗電）

## [0.1.0] - 2026-08-11

### Added

- 報告初版（29 型號統合對比）
