# AGENTS.md — daily-xauusd-brief 專案規則

> 本檔案定義所有 AI agent / 協作者 在這個 repo 上工作時必須遵守的規則。
> 寫入後預期由人 + AI 共同遵循；任何修改都應提交 PR 並說明理由。

---

## 🎯 核心規則（不可妥協）

### 1. 專注 XAUUSD（金價）
- 主要商品固定為 **`XAU/USD`**（黃金現貨，單位 USD per troy ounce）。
- 任何新功能、新資料源、新指標，**預設只服務 XAUUSD**。
- 若要擴充到其他商品（白銀、指數、原油…）：
  - 先在 `AGENTS.md` 提案新增章節
  - 維持「單一商品 = 單一 report 資料夾 / 單一 Telegram 訊息」結構
  - 不能把多商品塞進同一份報告
- 共用模組（HTTP client、indicator engine、formatter）可以拆出來重用，但**進入點仍以 XAUUSD 為主**。

### 2. 程式碼品質
- **清晰**：函式單一職責，命名直白（英文），避免單字母變數（除 iterator i / j）。
- **模組化**：
  - `fetch_data.py` — 只負責外部 API 呼叫
  - `compute_indicators.py` — 只負責指標運算
  - `format_report.py` — 只負責組裝輸出
  - `main.py` — 只負責 orchestration
- **可測試**：
  - 純函式優先（無 side effect）
  - 副作用（HTTP、檔案 I/O、Telegram 發送）必須可以被注入 / mock
  - 每個新模組至少有 1 個對應的 `tests/test_*.py`
- **型別安全**：使用 type hints；資料結構用 `pydantic.BaseModel`。
- **錯誤處理**：用 `tenacity` 做重試；任何外部呼叫失敗要明確 raise 特定 exception，不要靜默吞掉。

### 3. 優先使用免費或開放資料源
- API 選擇順序：免費 → 開源（自己 host）→ 付費。
- 預設資料源：Twelve Data（行情）+ NewsAPI（新聞）。
- 不可引入需要付費訂閱的資料源，除非有明確的 user 同意。
- Rate limit 要寫進 `config.py` 或註解，作為文件一部分。

### 4. Telegram 輸出規範
- **簡潔**：目標長度 < 1500 字；上限不超過 4096。
- **適合手機閱讀**：
  - 短行（建議 ≤ 60 字）
  - 善用 emoji：🟢 上漲 / 🔴 下跌 / ⚪ 中性 / ⚠️ 注意
  - 段落用空行分隔，不用密集表格
- **結構固定**：
  ```
  🥇 XAUUSD 每日簡報 — {日期}
  
  💰 現價：{price} ({change} {pct}%)
  📊 技術指標：
    • RSI(14) ...
    • MACD ...
    ...
  📰 新聞焦點：
    • ...
    • ...
  
  🕗 報告產生於 {HH:MM} HKT
  ```
- **重要**：Markdown 的 `**bold**` / `*italic*` 在 Telegram 可以用；但複雜表格語法會壞掉 — 避免。

### 5. 只做研究與摘要，不做自動下單
- **絕對禁止**：
  - 任何連接到交易平台（MT4/MT5/Binance/Interactive Brokers 等）的程式碼
  - 下單 / 平倉 / 改單 的 API 呼叫
  - 「跟單」「自動倉位」「建議下單張數」等表述
- **允許**：
  - 顯示價格、指標、新聞
  - 標示「偏多 / 偏空 / 中性」的技術觀察（純敘述）
  - 「以上為研究摘要，不構成投資建議」之類的 disclaimer
- 回顧 commit 時若發現違規，必須立刻 revert 並補上測試守門。

---

## 📐 開發慣例

### 套件管理
- 使用 `uv`（已安裝）；鎖檔用 `uv.lock`。
- 不主動升級 major 版本 — 升級需寫理由。

### Commit 訊息
```
feat: 新增 RSI 指標計算
fix: Twelve Data 超時時回傳空值而非 raise
docs: 補充 NewsAPI 額度說明
refactor: 抽出 format_indicators_text 純函式
test: 補上 MACD 邊界值測試
```

### 路徑約定
- 原始 API 回應：`data/raw/YYYY-MM-DD/{source}.json`
- 報告輸出：`reports/gold/YYYY-MM-DD.md`
- 日誌：`logs/daily-xauusd-brief.log`

### 環境變數
- 所有 secret 透過 `.env` 讀取（`python-dotenv`）。
- **永遠不要 commit 真實 key** — `.env` 已在 `.gitignore`。

---

## 🚦 PR / Merge 前檢查清單

- [ ] 改動只觸碰 XAUUSD 相關邏輯？（若否，需擴充提案）
- [ ] 新增 / 修改的函式都有 type hints？
- [ ] 新增邏輯都有對應測試？
- [ ] Telegram 訊息模板仍符合「簡潔、手機友善」？
- [ ] 沒有任何交易下單相關程式碼？
- [ ] 沒有引入需要付費的資料源？
- [ ] `.env.example` 有同步更新（若有新增環境變數）？

---

## 📚 參考資源

- Twelve Data API docs: https://twelvedata.com/docs
- NewsAPI docs: https://newsapi.org/docs
- Telegram Bot API (Markdown): https://core.telegram.org/bots/api#markdown-style
- pandas-ta: https://github.com/twop...t/pandas-ta

---

## 🗒️ Daily Journal Assembler

`scripts/assemble_journal.py` 會喺每次 scheduled run (XAUUSD `fc5b9c31a1fd` /
HK `264e15bbd8dc`) 成功之後自動產出:

- `daily_inputs/YYYY-MM-DD.md` — 結構化原始資料（cron outputs、pipeline log、gold report、Hermes CLI status）
- `reports/daily/YYYY-MM-DD.md`  — 整理後嘅 markdown 日誌

### Inputs

- `~/AppData/Local/hermes/cron/output/<job_id>/YYYY-MM-DD_HH-MM-SS.md`
- `logs/daily-xauusd-brief.log`
- `logs/last_dryrun.log`
- `reports/gold/YYYY-MM-DD.md`（如存在）

### Status priority

1. `hermes cronjob list` CLI（single source of truth；cached per run）
2. Fallback: scan raw cron output for `FAILED` keyword

### Failure semantics

- 兩條 cron 都 missing → fail-fast `exit 2`（避免 fabricate journal）
- 輸出已存在 → append HTML re-run comment（idempotent）

### Hook point

`scripts/run_daily.sh` / `scripts/run_daily.bat` — 喺 `main.py` exit code
係 0 後自動召喚 assembler；不過手動單跑亦可:

```bash
python scripts/assemble_journal.py                # today HKT
python scripts/assemble_journal.py 2026-07-06     # 指定日期
```

Hooked into:
- `scripts/run_daily.bat` / `run_daily.sh` — invoked after the
  08:30 HKT scheduled run succeeds (see cron prompt for fc5b9c31a1fd).
