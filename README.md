# daily-xauusd-brief

> 每日黃金簡報 Bot — 自動整理 **XAU/USD** 現價、技術指標與宏觀新聞，並以 Telegram 訊息 + 本地 Markdown 報告兩種形式交付。

## ✨ 目標

- **專注 XAUUSD**（金價，美元 / 盎司）。日後可擴充其他商品。
- 每天（或自訂排程）自動抓取價格、技術指標、新聞頭條。
- 結構化輸出，**適合手機閱讀**。
- 全程研究與摘要用途，**不做任何自動下單**。

## 🏗️ 架構

```
┌──────────────────┐
│ 排程層             │
│ (Hermes cron /   │
│  Linux crontab / │
│  Win Task Sched) │
└─────────┬────────┘
          ▼
┌──────────────────────────────────┐
│ scripts/run_daily.{sh,bat}       │
│ (single-entry wrapper)           │
└─────────┬────────────────────────┘
          ▼
┌──────────────────────────────────────────┐
│ python -m daily_xauusd_brief.main        │
│   1. fetch_time_series (Twelve Data)     │
│   2. compute_indicators (MA20/MA50..)    │
│   3. fetch_news (newsapi.ai + RSS)       │
│   4. dedupe → rank → summarize           │
│   5. format_markdown + format_telegram   │
│   6. write to reports/gold/YYYY-MM-DD.md │
│   7. send_via Bot API (if token)         │
└─────────┬────────────┬───────────────┐
          ▼            ▼               ▼
   reports/gold/    logs/         Telegram
   *.md            *.log          chat
```

## 📂 專案結構

```
daily-xauusd-bot/
├── src/daily_xauusd_brief/
│   ├── __init__.py
│   ├── config.py              # 設定載入 (.env)
│   ├── main.py                # entry point，含 --dry-run / --send-only / --no-send
│   ├── fetch_data.py          # 抓行情 (Twelve Data)
│   ├── fetch_news.py          # 抓新聞 (newsapi.ai Event Registry + RSS)
│   ├── compute_indicators.py  # MA20/MA50/MA200 + MACD + RSI + 布林通道
│   ├── news_ranker.py         # 去重 + 排名
│   ├── news_summarizer.py     # 繁中 1–2 句摘要
│   ├── format_report.py       # 組裝 Markdown + Telegram
│   ├── telegram_sender.py     # Telegram Bot API + Hermes route
│   ├── cache.py               # DailyReport cache（--send-only 用）
│   ├── verify_newsapi_key.py  # 驗 newsapi.ai / newsapi.org key
│   └── models.py              # pydantic 資料模型
├── tests/
│   ├── test_compute_indicators.py
│   ├── test_format_report.py
│   ├── test_mvp_v1.py
│   ├── test_v2.py
│   ├── test_fetch_news.py
│   ├── test_telegram_sender.py
│   └── test_scheduling_scripts.py
├── scripts/
│   ├── run_daily.sh           # Linux/macOS 手動 / 包裝入口
│   ├── run_daily.bat          # Windows 手動 / 包裝入口
│   ├── setup_cron_linux.sh    # Linux 排程安裝 / 移除
│   ├── setup_task_windows.ps1 # Windows Task Scheduler 安裝
│   ├── setup_task_windows.bat # .bat 入口
│   └── update_env.py          # 寫 .env 的 workaround helper
├── docs/
│   └── scheduling.md          # 詳細排程指南
├── reports/gold/              # 每日 Markdown 報告輸出
├── data/raw/                  # 原始 API 回應 JSON
├── logs/                      # 程式 + cron 日誌
├── .env.example
├── requirements.txt
├── pyproject.toml
├── AGENTS.md
└── README.md
```

## 🚀 快速開始

### 1. 取得 API Keys（全部免費）

| 服務 | 用途 | 免費額度 | 連結 |
|---|---|---|---|
| **Twelve Data** | XAUUSD 行情 + 指標 | 800 req/day, 8 req/min | https://twelvedata.com/ |
| **newsapi.ai (Event Registry)** | 黃金相關新聞 | 200 req/day | https://newsapi.ai/ |

> **注意**：原先 setup 嗰陣以為用 newsapi.org，但實際 newsapi.ai 同 newsapi.org 係兩間公司。詳見 `docs/scheduling.md` 同 SKILL `newsapi-ai-vs-newsapi-org`。

### 2. 安裝

```bash
cd ~/projects/daily-xauusd-bot

# 用 uv（推薦）
uv pip install -e .

# 或用 pip + venv
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 設定環境變數

```bash
cp .env.example .env
# 編輯 .env，填入：
#   TWELVE_DATA_API_KEY=*** 32-char hex>
#   NEWSAPI_KEY=*** UUID v4>
# 可選（如果走 Telegram Bot API 而非 Hermes send_message）：
#   TELEGRAM_BOT_TOKEN=***
#   TELEGRAM_CHAT_ID=***
```

### 4. 本地試跑

```bash
# 不送 Telegram，只產生 Markdown + 預覽
bash scripts/run_daily.sh --dry-run

# 正式產生 Markdown + Telegram
bash scripts/run_daily.sh
```

Windows 對應：
```bat
scripts\run_daily.bat
scripts\run_daily.bat --dry-run
```

### 5. 只重發 cache（不重抓）

如果你想 retry Telegram 發送而唔抓 API：

```bash
bash scripts/run_daily.sh --send-only
```

或者完全唔送 Telegram：

```bash
bash scripts/run_daily.sh --no-send
```

## ⏰ 自動排程

本 Bot 內建 **三種排程方式**：

| 排程器 | 用法 script | 適合 |
|---|---|---|
| **Hermes cron**（推薦）| `cron.job(...)` or see `docs/scheduling.md` 3. 節 | 已有 Hermes 用戶 |
| **Linux / macOS / WSL crontab** | `bash scripts/setup_cron_linux.sh` | Unix 系主機 |
| **Windows Task Scheduler** | `.\scripts\setup_task_windows.ps1` | Windows 主機 |

詳細設定步驟見 **[`docs/scheduling.md`](docs/scheduling.md)**：
- Linux / cron 排程安裝 / 移除 / 狀態查
- Windows Task Scheduler 排程安裝 / 移除 / 狀態查
- Hermes cron prompt 範本
- 時區處理
- 除錯常見問題

## 🛡️ 容錯策略

- 每個外部 API 呼叫用 `tenacity` 包裝，預設 3 次指數退避重試。
- 單一資料源失敗不會中斷整個流程 — 同時嘗試 main + RSS fallback。
- `TelegramSendError` / `FetchError` 都用明確 message + log write 出嚟。
- 日誌輸出至 `logs/daily-xauusd-brief.log`。
- `--send-only` 走 cache 重發，唔會重抓 API。

## 🧪 測試

```bash
pytest tests/ -v
```

`56+ tests passed`，覆蓋：indicators / formatter / fetch_news / telegram / cache / 排程 wrapper。

## 📁 重要檔案

| 路徑 | 內容 |
|---|---|
| `reports/gold/YYYY-MM-DD.md` | 每日 Markdown 報告 |
| `data/cache/latest_report.pkl` | 供 `--send-only` 用嘅 cache |
| `logs/daily-xauusd-brief.log` | 應用層 log |
| `logs/cron.log` | cron / task scheduler嘅 stdout/stderr |

## 📜 License

MIT
