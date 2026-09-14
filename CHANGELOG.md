# Changelog

本文件記錄 aircon-compare 的所有顯著變更（Keep a Changelog 風格）。
版本號遵循 SemVer（`MAJOR.MINOR.PATCH`），唯一手動來源為 `models_data.py` 的 `VERSION`。

## [Unreleased]

### Added

- `pytest.ini`：`python -m pytest tests/` 預設收集 `tests/browser_smoke.py`（12 項瀏覽器 E2 證據）
- 瀏覽器回歸測試：價位邊界、Escape／焦點、tooltip 溢出、明暗對比、metadata 小數秒與載入失敗
- `tests/test_energy_distribution.py`（8 項）：1–5 次序、核心 29 靜態表防漂移、動態全量分佈來源／總和、PDF 展開動態區塊
- `tests/test_biggo_smoke.py`（8 項）：smoke 候選本地證據、首個成功只用一次、no-price fallback、全失敗、例外唔洩漏 secret
- **持久發佈工具（D15）**：`docker/`（Dockerfile 將程式碼放入 `/opt/aircon-src` + 容器內 `run-update.sh` 以 `rsync --checksum --delete` 同步程式碼落 volume、兩階段 metadata、PDF/CSV 原子部署）
- **公開 repo 私隱 gate**：`scripts/check_public_privacy.py`（掃描 tracked HEAD 禁止個人／自建環境識別資料；只列規則 ID／檔名，不打印命中內容）＋ `tests/test_public_privacy.py`（合成樣本命中、通用示例值放行、repo HEAD 自掃 0 命中）
- **自建部署配置泛化**：`release/release-299c3e9.sh` 部署路徑／host 等一律由環境變數提供（base dir 預設 `/srv/aircon-compare`，缺失即 fail closed）；`docker/nginx.conf` 改為通用模板；`release/README.md` 改寫為通用 self-host 文檔
- **伺服器入口**：`release/release-299c3e9.sh`（preflight／build／verify／serve／apply／rollback；普通使用者啟動，確認後交由 sudo）
- **Runtime 資料準備**：`scripts/prepare_runtime_data.py`（黑名單 canonical 遷移守衛，只跑一次）＋ `tests/test_prepare_runtime_data.py`（8 項）
- **Sandbox 測試**：`release/sandbox/`（12 情境、91 斷言：dry-run、path guard、TOCTOU、備份失敗安全、stopped container、rollback 權限、sync --delete）

### Changed

- **發佈管線次序**：容器管線喺 `generate_html` 之後才跑非瀏覽器 pytest
- **程式碼同步**：`run-update.sh` 改為 `rsync -a --delete`（image 為程式碼真源；stale 程式碼清除；runtime 資料／web／快取永久排除；`deploy_payload.json` 照同步）
- **報告當前狀態數字**：`generate_html.py` 建置時同步報告內文數字；`tests/test_dynamic_counts.py` 加守衛
- **Rollback 入口**：`rollback latest` 由 root 階段解析備份（普通使用者毋須讀 root-only 備份目錄）；container 改用 `docker ps -aq`（支援 stopped）並拒絕歧義
- **停機安全**：apply 分 stopped／backup_ready／applying 階段；備份驗證（可讀 + checksum + 檔案清單 + image pre 記錄）完成後才改 volume；停機後備份未完成前失敗只安全重啟原服務；停機前必須成功建立舊 image pre tag 並驗證 ID，失敗即阻斷
- **回滾可靠性**：`rollback latest` 由 root 從新到舊挑第一個完整備份（跳過不完整並 warning）；回滾前先驗 archive + image tag 可還原，唔會用 volume-only 冒充完整成功

- 治理改善方案 M1（PR-1／PR-2／PR-3，本地分支；決策 D11-D13）：
  - **Canonical 型號鍵**：`crawl_utils.canonical_brand()`（品牌跨平台矯正）+ `canonical_model_key()`（`BRAND|NORM`）；黑名單、model_status、protected set、filter_active、record_results、revive_model 全線統一（D11）
  - **生命週期三態**：`run_price_batch()` 分開有價／乾淨無報價／網絡錯誤（網絡錯誤唔計淘汰，D8）；正常批次照 call `record_results`（batch_id 防同批重跑重複計 miss）；每批次日小額黑名單復核 quota 40（有價自動復活）；並發 3 → 2（回歸 D3）
  - **EMSD ingestion**：每頁表頭按 signature 排除（唔再靠 p==1）；`emsd_receipt.json` 記錄 pagesExpected/pagesFetched/每頁行數/終止原因；中途網絡錯誤即使累積超過下限都唔覆寫 CSV；保留全部 registration + canonical product view（D12）
  - **Metadata Schema**：新增 optional `rawRecordCount`／`registrationCount`／`modelCount`（治理文檔 v3.1.1）
  - **PDF 可重現**：`build_pdf(output_path=...)` 加輸出參數、固定 CreationDate/ModDate/ID，同輸入兩次 build byte-for-byte 相同；`test_pdf_export` 改用 tmp_path（唔再污染受追蹤 PDF），並加測試前後 repo PDF hash 不變斷言
- 比較面板加 `role="dialog"`；搜尋與下拉選單加 `aria-label`
- 比較工具列、頁腳、hero 統計、`--primary2` 等顏色調整至 WCAG AA（≥4.5:1）
- **CI 兩階段 metadata 封裝（D14）**：新增 `--stage core|finalize`；core 出同 run 核心事實（無 hash）→ PDF 用 core → PDF 完成後以 `deploy_payload.json` 明確 manifest finalize `releasePayloadHash`；hash 範圍唔再係 `--payload-dir .`
- **動態型號數字**：Hero／Open Graph／meta description 用建置時實際 `__TOTAL_MODELS__`／`__EMSD_REGISTRATIONS__`；歷史數字（1,927／1,854）只保留喺明確標示歷史嘅文檔段落
- `tests/browser_smoke.py` 移除 `pytest.importorskip`（required browser smoke 唔可以靜靜 skip）；`requirements-dev.txt` 加 `playwright>=1.40`
- **能源分佈資訊架構**：核心 29 表明確標示且固定 1→2→3→4→5 次序（0 都顯示）；新增 `<!-- AIRCON:DYNAMIC:ENERGY_DISTRIBUTION -->` 於 build 時由實際快照動態生成全量分佈（canonical model 同 registration 分兩欄），HTML 同 PDF 一齊展開
- **BigGo smoke 多候選**：`SMOKE_CANDIDATES` 集中管理 3 個跨品牌核心 29／受保護型號，依序探測、首個有價即通過；抽出 `_extract_price` 做共用過濾；`no-price`（個別型號無匹配）同 `unreachable`（網絡／限流／認證粗分類）訊息分開
- **文件透明化**：README／空調對比報告.md／需求摘要.md／AGENTS.md 加 AI 協作角色說明（Codex／DeepSeek `deepseek-flash` via Pi `ds-exec`／人類維護者）；AGENTS.md 修正唔存在嘅命令（`--full-scan`／`--blacklist` → `--force-batch`／`model_lifecycle.py`）
- `.gitignore` 加 `.agents/`：ds-exec 工作記錄同整合 worktree 唔係產品內容，防止主工作區 `git add -A` 誤提交

### Fixed

- **公開 repo 私隱**：移除／泛化自建環境識別資料（文件、release 工具、nginx 模板）；新增 `scripts/check_public_privacy.py` CI gate 及回歸測試，防止再次寫入私人 host／IP／路徑／build id
- **GitHub Pages hash 鏈**：`fetch_emsd.py` 寫 CSV 改用 `lineterminator='\n'`（原生 LF），令 worktree bytes == git index bytes == 發佈 bytes；之前 CRLF 工作樹經 `.gitattributes eol=lf` 正規化後，線上 `datasetHash`／`releasePayloadHash` 同實際 bytes 唔一致
- 新增 CI 防線 `scripts/check_payload_bytes_vs_index.py`（workflow 在 `git add -A` 之後、commit 之前阻斷任何 worktree/index bytes 不一致）
- 新增回歸測試：`tests/test_emsd_csv_lf.py`（實走 `fetch_emsd.write_csv` 斷言 LF-only + loader 可讀 + 已入庫 CSV 與 metadata.datasetHash 自洽）、`tests/test_payload_bytes_vs_index.py`（LF 通過、CRLF／未 stage／缺檔阻斷）
- 伺服器 preflight 嘅 `grep -q` + `pipefail` SIGPIPE 誤判（改為先列 tarball 清單再檢查）
- 同秒重建時備份目錄碰撞（唯一後綴；pre-image tag 跟備份名）
- 回滾時「部署前不存在」嘅檔案（PDF／CSV／receipt）冇被刪除（完整 volume 還原 + 檔案清單 + hash 比對）
- 報告內文硬編當前狀態數字（建置時動態同步；歷史更新日誌保持不變）
- 舊 volume EMSD CSV 每頁重複表頭（release 用 PR-3 ingestion 重抓；`validate_data` 阻斷）
- 停售標示失效：黑名單 canonical key 匹配修正後，頁面停售型號 289 → 1,075（黑名單 1,095 keys 中 1,079 個可解析品牌；4 個唔喺現行頁面資料）
- 保護型號失效：`protected_models()` 之前誤將 MODELS dict 整個正規化，核心 29 保護形同虛設；現改為 canonical key 集合
- EMSD CSV 混入 37 行重複表頭（已清理；1,863 筆登記 / 1,814 型號）
- 價位篩選「5以上」錯誤包含未知價型號（`core.filter`）
- 平板寬度 721–999px 導覽 tooltip 撐出頁面水平滾動（`ui.responsive`）
- 比較面板唔支援 Escape 關閉、開啟／關閉焦點唔跟隨、缺 `role`/`aria-expanded`（`ui.comparison-modal`）
- 深色模式頁腳文字對比 1.85:1；淺色模式連結／按鈕／hero 統計／最佳值標示對比不足
- 頁腳版本內嵌 `models_data.VERSION` 靜態常量（`operations.version-display`，改為只讀 `metadata.json.version`）
- Windows 本地生成 HTML 用 CRLF，與 CI／已入庫 LF 唔一致（可重現建置）
- 「只顯示已選」之下反選後，型號仍留在列表（`core.filter`）
- `AGENTS.md` 與 `validate_metadata.py` 用法示例檔名（`validate-metadata.py` 不存在，以 CI 實際命令 `validate_metadata.py` 為準）
- `validate_metadata.py` 之前拒收 RFC 3339 小數秒（`2026-09-02T19:28:14.500Z`）；內嵌 Schema `format: date-time` 本身容許，已對齊（非 UTC `Z` 或格式錯照樣拒）
- 能源分析表舊次序 `1,3,4,2,5` → 固定 `1,2,3,4,5`，2／5 級 0 都顯示；「定頻最高只有 3 級」限定為核心 29 語境，唔再同全量 1–5 級資料混淆
- BigGo smoke 單一硬編 `RA-10RF`：個別型號停售／一時無價會誤判整個 API 失敗 → 多候選 fallback，安全門禁（smoke 不過即跳過批次）不變

### Data

- `model_blacklist.json` 1,095 個 key 遷移為 canonical（matched 1,079、orphan 16；遷移報告 `docs/blacklist-migration-2026-09.md`）
- `model_status.json` tracking key 一併遷移
- `emsd_空調能源標籤.csv` 移除重複表頭（1,900 → 1,863 筆登記）
- README／需求摘要／報告計數同步實際快照（1,814 型號 · 1,809 有價 · 1,863 筆登記，截至 2026-09-03）
- metadata `recordCount` 按 D12 改為唯一型號數（1,814）；CI 另傳 optional `rawRecordCount`（1,863）／`registrationCount`（1,863）／`modelCount`（1,814）
- README 狀態分佈圖同步實際頁面：有價 674 / 停售 1,075 / 官方價 65（合共 1,814；無價 0）
- README 能源級別圖同步全量 canonical model（1,127／166／168／348／5，合共 1,814），並列 registration（1,172／166／172／348／5，合共 1,863）同核心 29 對照

### Security

- 無改動（BigGo 憑證仍只存 GitHub Secrets）
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
