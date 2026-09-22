# ADR-002：官網核實批次「未覆蓋 queue model」時嘅推進政策（候選）

- **日期**：2026-09-22
- **狀態**：**人類已批准 D1-B**（EMSD 照常發布、queue 保留 pending coverage）；本機候選已實作，未部署、未跑 E3／E4。
- **背景**：
  - `scripts/run_official_batch.py` 會驗證每個 stage 嘅腳本 rc、machine receipt、
    輸出 evidence 同 queue model 覆蓋；只有所有 queue model 都有本輪成功樣本
    （或已有有效既存 evidence）才 `advance_queue`。
  - 但現有六個抓取腳本（Panasonic／HITACHI／COMFEE／Rasonic／Carrier／GENERAL／
    核心 29 specs）只覆蓋各自品牌目錄；EMSD 新機型號若屬品牌目錄以外，或無法由
    腳本 parser 映射到 queue model，就永遠唔會有 `covers` 證據。
  - 因此本輪實作會 **fail-closed 保留 queue 並 exit 1**；CI step 失敗 → 目前等同
    「阻斷當日 EMSD 發布」（Option A 行為）。
- **選項**：
  - **A（現行 fail-closed 行為）**：queue 未完全核實 → 整個 daily job 失敗，
    EMSD 數據當日唔發布。優點：未核實新機唔會被靜默跳過；缺點：一個未映射型號
    可以永久阻斷每日更新。
  - **B**：EMSD 可以照發布（每日 CSV/Web/PDF 更新），但 enrichment queue 保留
    stage 及 models，明確標示 `pending coverage`；queue 只在有覆蓋證據時推進。
    優點：資料日期保持新鮮，未核實型號有審計狀態；缺點：新機規格 enrichment
    可能長期 pending，需要在頁面／報告標示「未核實」。
  - **C**：加 fallback 核實來源（例如更廣嘅品牌搜尋／人工覆核清單），令覆蓋率
    提升後再推進。優點：真正處理來源缺口；缺點：需要新抓取目標同 parser 投資。
- **影響分析（資料日期／可用性／治理證據）**：
  - A：datasetDate 會停止前進（阻斷），可用性下降；治理證據最嚴格。
  - B：datasetDate 正常前進；queue pending 需要 machine receipt + status 顯示，
    治理證據分開「已發布資料」同「pending enrichment」。
  - C：視乎新來源，可能改變資料來源契約（需 R3 批准）。
- **建議（待人類決定）**：B（保持每日資料新鮮，同時 queue 保留同明示 pending），
  但必須先補「pending queue 顯示／告警」設計；在此之前維持 A 嘅 fail-closed。
- **需要決定嘅人**：人類維護者（產品政策）；涉及產品行為（A vs B）同 R3 資料來源
  變更（C）需 Code Owner 批准。
- **本輪已實作、非此 ADR 決定範圍**：machine receipt、queue hash 綁定、
  zero-attempt 需 alreadyVerified＋evidence、output hash 重驗、queue 保留。

## D1-B 已批准實作補充（2026-09-22）

- `scripts/run_official_batch.py` 分開 hard failure 與 coverage missing：
  壞 queue／receipt 缺失／schema 錯／`failed > 0`／腳本非零／輸出無效／output or queue
  hash race／phantom covers／資料或 metadata／privacy／feature gates 失敗一律
  `queue-kept-fail-closed` 非零阻斷；只有「腳本／receipt／輸出本身成功，純 coverage 不足」
  才寫 `queue-kept-pending-coverage` 並原樣保留 queue stage／models。
- `scripts/publish_official_status.py` 投影公開 `official_batch_status.json`；
  `generate_html.py` build 時顯示「官網規格待核」，不冒充已核實。
- 仍必須保留 advance 才可清 queue：pending 路徑不會呼叫 `advance_queue.py`，亦不會部分
  覆寫輸出快照。
