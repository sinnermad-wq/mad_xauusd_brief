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

---

## 🛰️ Candlestick Direction Engine (manual-only)

`src/daily_xauusd_brief/direction_engine.py` — multi-timeframe direction
classification for XAUUSD.

### 模式：manual-only（截至 2026-07-07）

- ❌ **NOT** hooked into `run_daily.{sh,bat}`
- ❌ **NOT** bound to any cron / scheduled task
- ❌ **NOT** wired to the daily brief / journal assembler
- ✅ 只用嚟 manual invocation，對 HKT 21:00 NY session ad-hoc check 或
  backtest case-by-case 用。

### 資料來源 / 計算窗口

- D1 fetch — yfinance `GC=F` (futures front-month)。**預設 period 5y**
  (`DIRECTION_ENGINE_PERIOD=5y`) 確保 MA200 有完整 200 trading-day warm-up。
- 內建 in-memory TTL cache 5 分鐘，避免 short-loop 短期內重覆打 Yahoo。
- Retry：exponential backoff 1s → 2s → 4s → 8s（cap）。
- 若 5y fetch 最終失敗：fallback 至 6mo (FALLBACK_PERIOD)；若兩者都失敗，
  RuntimeError raised 含 both error messages，**永不 silent**。

### Long-term context flags (snapshot dict)

- `period_used`         `"5y"` | `"6mo"`
- `primary_ok`          boolean — 是否用緊 primary 5y
- `provenance`          `"primary"` | `"fallback"`
- `bars`                D1 bars count
- `d1_close`, `d1_ma20`, `d1_ma50`, `d1_ma200`
- `sufficient_for_ma50`、`sufficient_for_ma200`
- `insufficient_context` — `True` 時代表 MA200 唔可信 (bars < 210)

### 使用範例

```python
from src.daily_xauusd_brief.direction_engine import build_engine_snapshot
snap = build_engine_snapshot()                       # 預設 GC=F, 5y
print(snap["d1_ma200"], snap["insufficient_context"])  # None / False (正常)
```

如要換 symbol / period:
```python
snap = build_engine_snapshot("XAUUSD=X", "5y")
```

### 覆寫 env defaults

- `DIRECTION_ENGINE_SYMBOL`        (default `"GC=F"`)
- `DIRECTION_ENGINE_PERIOD`        (default `"5y"`)
- `DIRECTION_ENGINE_FALLBACK_PERIOD` (default `"6mo"`)

### 現階段 NOT production trading

- 純 analytical / read-only。
- 唔 emit Telegram。
- 唔 write `reports/`。
- 唔 alert / 唔 webhook。
- 任何 auto-scheduling 嘅提案，必須先喺 AGENTS.md 提議擴張章節先 enable。

---

## 🛰️ Candlestick Direction Engine Review Log (v1)

`data/engine_reviews.csv` — structural log of every engine analysis session.
Write path: `scripts/log_engine_review.py`.  Read path: dashboard
`🛰️ Engine Reviews` section (read-only).

### CSV 欄位

| 欄位 | 說明 |
|---|---|
| `review_id` | auto-generated, 格式 `GC-F_YYYY-MM-DD_HHMMSS_<6hex>` |
| `confidence_bucket` | auto: 0-39 / 40-54 / 55-69 / 70-84 / 85-100 |
| `support_levels` / `resistance_levels` | 分號分隔 |
| `insufficient_context` | true/false — MA200 可用性 |
| `ma200_available` | true/false |
| `outcome_label` | 可留空；日後手動或 script 更新 |
| 完整 36 欄 | 見 `scripts/log_engine_review.py` header docstring |

### 寫入方式

```bash
# dry-run（不寫入）
python scripts/log_engine_review.py --input /path/to/snapshot.json --dry-run

# 實際寫入
python scripts/log_engine_review.py --input /path/to/snapshot.json

# 或 stdin
cat /path/to/snapshot.json | python scripts/log_engine_review.py --input -
```

### Dashboard `🛰️ Engine Reviews` 頁面 (read-only)

- **KPI cards**：Total reviews / Avg confidence / Correct % / Invalidation Hit %
- **Latest 20 Reviews table**：date, hkt_time, session, direction, bias, confidence, bucket, invalidation level, outcome
- **Breakdowns**：by session / by direction_classification / by confidence_bucket
- **Data quality**：insufficient_context count / MA200 available ratio

### 手動更新 outcome_label（not auto-filled）

日後想更新某筆 `review_id` 的結果，例如 `GC-F_2026-07-07_103257_14afec`：

```python
import csv, datetime
path = "data/engine_reviews.csv"
rows = list(csv.DictReader(open(path)))
for r in rows:
    if r["review_id"] == "GC-F_2026-07-07_103257_14afec":
        r["outcome_label"]      = "correct"
        r["review_score"]       = "85"
        r["failure_reason"]     = "none"
        r["lesson"]             = "..."
        r["updated_at"]         = datetime.datetime.now().isoformat()
writer = csv.DictWriter(open(path,"w",newline=""), fieldnames=rows[0].keys())
writer.writeheader(); writer.writerows(rows)
```

### 手動-only 規則（仍然生效）

- ❌ 唔自動接 `run_daily.sh`
- ❌ 唔新增 cron
- ❌ 唔自動寫入 journal assembler
- ✅ `log_engine_review.py` 需要人喺 chat 主動 trigger

### Rule Change Protocol

任何 calibration 或 analytical rule 的改動，必須跟 `docs/rule_change_protocol.md` 的流程：
提出 → 審批 → 1週驗證 → 4週驗證 → 月報記錄。
Rule changes 記錄喺 `docs/rule_changes.md`（append-only）。

### Backtest & Scalping Research Module (manual-only)

`src/backtest/` + `scripts/run_backtest.py` — research-only backtest CLI，唔做任何 execution。

General strategies: `baseline_ma`, `breakout`
Scalp strategies: `xauusd_scalp_reversion`, `xauusd_scalp_momentum`

```bash
# general
python scripts/run_backtest.py --mode general --strategy baseline_ma \\
    --start 2026-01-01 --end 2026-03-31 --format both

# scalp with cost sensitivity
python scripts/run_backtest.py --mode scalp --strategy xauusd_scalp_momentum \\
    --start 2026-01-01 --end 2026-03-31 \\
    --spread-points 5 --slippage-points 2 \\
    --session new_york \\
    --output-format both

# 參數預設
python scripts/run_backtest.py --strategy baseline_ma --show-params
```

Outputs:
- `data/backtests/YYYYMMDD_{strategy}_{mode}.csv` — trade-level with MAE/MFE/pnl_r
- `reports/backtests/YYYYMMDD_{strategy}_{mode}.md` — full markdown report

Rule Change Protocol 適用於任何 strategy parameter 的改動。

### update_engine_review.py — 驗證窗口期滿後更新結果

```bash
# 驗證期滿後，更新 outcome（例如 4h / session_close / next_day 後）
python scripts/update_engine_review.py \
    --review-id GC-F_2026-07-07_103257_14afec \
    --outcome-label correct \
    --review-score 4 \
    --validation-price 4168.3 \
    --max-favorable-move 22.1 \
    --max-adverse-move 8.4 \
    --invalidation-hit false \
    --failure-reason none \
    --lesson "bearish_continuation confirmed at NY close"

# 部分更新（只填你確定的欄位）
python scripts/update_engine_review.py \
    --review-id GC-F_2026-07-07_103257_14afec \
    --outcome-label correct \
    --review-score 4

# 強制覆蓋（已有 outcome_label 仍要改）
python scripts/update_engine_review.py --review-id ... --outcome-label wrong --force
```

規則：
- 找不到 `review_id` → exit 1
- 已有 `outcome_label` 未加 `--force` → 拒絕覆蓋
- `--force` → 可強制覆蓋
- 所有更新寫入 temp file 再 replace（atomic）
- `updated_at` 自動更新為 ISO timestamp

### generate_engine_weekly_report.py — 每週 calibration review

```bash
# 打印到 stdout（dry-run）
python scripts/generate_engine_weekly_report.py --week-start 2026-06-30 --dry-run

# 寫入 reports/engine_reviews/weekly-2026-06-30.md
python scripts/generate_engine_weekly_report.py --week-start 2026-06-30

# 自訂 output path
python scripts/generate_engine_weekly_report.py \
    --week-start 2026-06-30 \
    --week-end 2026-07-06 \
    --symbol GC=F \
    --output reports/engine_reviews/weekly-2026-06-30-custom.md
```

報告 sections：
Period / Summary / Session Breakdown / Direction Breakdown /
Confidence Calibration / Failure Reasons / Top Observations / Next Adjustments

規則：
- `reports/engine_reviews/` 預設寫入，`reports/` 在 `.gitignore` 內（本地输出）
- 週內冇資料 → "No Data" section，仍生成檔案
- `--week-end` 預設 = `--week-start + 6 days`
- `--week-end` 不可早於 `--week-start`（error）
- 手動-only，唔自動排程

### generate_engine_monthly_report.py — 每月的 calibration review

```bash
# 打印到 stdout（dry-run）
python scripts/generate_engine_monthly_report.py --month 2026-07 --dry-run

# 寫入 reports/engine_reviews/monthly-2026-07.md
python scripts/generate_engine_monthly_report.py --month 2026-07

# 自訂 output / symbol filter
python scripts/generate_engine_monthly_report.py --month 2026-07 --symbol GC=F \
    --output reports/engine_reviews/monthly-2026-07.md
```

報告 sections：
Period / Summary（median_conf, insufficient_context_rate, ma200_rate）/ Session Breakdown /
Direction Breakdown / Confidence Calibration / Failure Reasons / Drift Pattern Notes /
Rule Change Candidates / Next Month Actions

規則：
- 冇資料 → 仍生成 "No Data" section
- `total_reviews < 10` → ⚠️ Sample size warning
- 非法 month 格式 → exit 2
- Manual-only，唔自動排程，唔改 dashboard 寫入邏輯

---

## 🛰️ Workflow Observability

每個 engine review script 執行時，會自動 append 一條 JSONL event 落 `data/engine_ops_events.jsonl`，記錄 script run 結果。

### 共用 helper

`scripts/engine_ops_logger.py` 提供兩個 API：
- `log_event(...)` — 手動 append 事件，唔拋例外
- `track(script_name, ...)` — context manager，自動捕捉 `started_at` / `duration_ms` / status / exception type

### Event Schema (每行一個 JSON object)

| Field | Type | Description |
|---|---|---|
| event_type | string | 永遠 `script_run` |
| script_name | string | e.g. `log_engine_review.py` |
| started_at | ISO timestamp (HKT) | script 開始時間 |
| finished_at | ISO timestamp (HKT) | script 結束時間 |
| duration_ms | int | 執行 millisecond |
| status | `success` | `error` |
| error_type | string | 例外類別（error 時） |
| error_message | string | 例外訊息（error 時） |
| review_id | string | review 行 ID（若適用） |
| output_path | string | 寫入嘅 file path or `stdout` |
| rows_affected | int | changed/added rows |
| metadata | object | script-specific metadata (dry_run, week, month, symbol etc.) |

### 已記錄 events 嘅 scripts

- `log_engine_review.py`
- `update_engine_review.py`
- `generate_engine_weekly_report.py`
- `generate_engine_monthly_report.py`

### query_engine_ops.py — observability query CLI

`scripts/query_engine_ops.py` — ad-hoc CLI to query and summarize engine ops events.
Reads `data/engine_ops_events.jsonl` (default; overridable via `--input`).

```bash
# default: summary (last 7 days)
python scripts/query_engine_ops.py

# explicit modes
python scripts/query_engine_ops.py --summary
python scripts/query_engine_ops.py --recent                # last 20 events
python scripts/query_engine_ops.py --errors                 # error breakdown
python scripts/query_engine_ops.py --slowest                # top 20 by duration_ms

# filters (apply to all modes)
python scripts/query_engine_ops.py --days 30
python scripts/query_engine_ops.py --script-name log_engine_review.py
python scripts/query_engine_ops.py --status error
python scripts/query_engine_ops.py --limit 10 --recent
python scripts/query_engine_ops.py --format json            # machine-readable output
```

**Output fields:**
- `--summary` → time window, total events, success/error counts + rates, per-script
  count/success/error/avg_ms/max_ms, top error types, duration avg/p50/p95/max in ms
- `--recent` → last N events with finished_at, script_name, status, duration_ms, review_id, output_path, error_type
- `--errors` → groups by error_type with count + latest_finished_at + latest_script
- `--slowest` → ranks by duration_ms desc with full event context

Manual-only query CLI; does not touch engine / pipeline / cron.

### validate_engine_reviews.py — read-only CSV data-quality validator

`scripts/validate_engine_reviews.py` — validates `data/engine_reviews.csv` without
writing anything. Useful before generating weekly/monthly reports.

```bash
# default text output
python scripts/validate_engine_reviews.py

# machine-readable output
python scripts/validate_engine_reviews.py --format json

# strict mode — exit non-zero on duplicate_id / bad confidence / missing fields
python scripts/validate_engine_reviews.py --strict

# filter by symbol / stale threshold
python scripts/validate_engine_reviews.py --symbol GC=F --days 7
```

**Checks performed:**
- Missing required fields
- Invalid session / direction_classification / outcome_label / confidence_bucket enums
- Confidence out of 0-100 range
- Duplicate review_id
- Pending outcomes (empty outcome_label)
- Stale rows (not updated in N+ days)
- Sample size for weekly (>=3 rows) / monthly (>=12 rows) reports

**Exit codes:** 0 = OK, 1 = critical issue (--strict), 2 = file not found

Manual-only; does NOT modify CSV / dashboard / cron / run_daily.sh.

### 保證

- ✅ Success + error 都記錄，唔少任何 event
- ✅ Logger 失敗 warn 唔 crash 主流程
- ✅ 保留原有 stdout/exit contract（`OK|...`、`[DRY RUN]` 等依然有效）
- ✅ Manual-only：唔接 cron / webhook / daemon
- ✅ Dashboard + `run_daily.sh` 完全 unchanged
