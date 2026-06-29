# Daily-xauusd-brief 排程設置指南

本 Bot 提供 **3 種執行方式**：
1. **手動觸發**：在你嘅 shell / terminal 跑一行指令
2. **作業系統排程**：Windows Task Scheduler / Linux `crontab` 直接接管
3. **Hermes cron**：Hermes 本身嘅 cronjob 調度，自動送到 Telegram home channel

詳細請參閱下方。

---

## 0. 共通前置作業

無論用咩排程，**都要先做**：

```bash
cd ~/projects/daily-xauusd-bot
cp .env.example .env
# 編輯 .env，填入：
#   TWELVE_DATA_API_KEY=...
#   NEWSAPI_KEY=...       (newsapi.ai UUID v4；legacy newsapi.org 32-char hex 都接受)
#   TELEGRAM_BOT_TOKEN=... (optional；無則走 Hermes send_message route)
#   TELEGRAM_CHAT_ID=...   (optional)

# 安裝依賴
uv venv .venv && source .venv/scripts/activate
uv pip install -e .
```

之後必須通過：

```bash
python -m daily_xauusd_brief.main --dry-run
```

無 error / 唔 crash 嘅話，可以往前設置排程。

---

## 1. 手動觸發

### Linux / macOS

```bash
bash scripts/run_daily.sh                # default full pipeline
bash scripts/run_daily.sh --dry-run      # 預覽，不送 Telegram
bash scripts/run_daily.sh --send-only    # 從 cache 重發，不重抓
bash scripts/run_daily.sh --no-send      # 只生成 MD，不送 Telegram
```

### Windows (cmd.exe)

```bat
scripts\run_daily.bat
scripts\run_daily.bat --dry-run
scripts\run_daily.bat --send-only
```

### Windows (PowerShell)

```powershell
& scripts\run_daily.bat
& scripts\run_daily.bat --dry-run
```

`run_daily.{sh,bat}` 會自動偵測 `uv` / `.venv/Scripts/python.exe` / 系統 `python`，優先順序為：

```
uv run python  >  .venv\Scripts\python.exe  >  python (fallback)
```

---

## 2. 作業系統排程

### 2.1 Linux / WSL / macOS（crontab）

**安裝**：
```bash
cd ~/projects/daily-xauusd-bot
bash scripts/setup_cron_linux.sh
```

預設時間：`30 8 * * *`（**每天 08:30 HKT**，假設你嘅 server 在 HKT timezone）。

如果 server UTC：

```bash
# 08:30 HKT = 00:30 UTC（同日）
# 用 crontab 時直接寫 server 本地時間：
CRON_SCHEDULE="30 8 * * *" bash scripts/setup_cron_linux.sh

# 若你只可以 UTC：08:30 HKT = 00:30 UTC（即 cron "30 0 * * *"，但翌日）
```

**查看狀態**：
```bash
bash scripts/setup_cron_linux.sh --status
```

**預覽不安裝**：
```bash
bash scripts/setup_cron_linux.sh --dry-run
```

會印出：
```
# daily-xauusd-brief-managed
30 8 * * * cd /home/madwa/projects/daily-xauusd-bot && bash scripts/run_daily.sh >> logs/cron.log 2>&1
```

**解除安裝**：
```bash
bash scripts/setup_cron_linux.sh --uninstall
```

**Log**：stdout/stderr 全部 redirect 落 `logs/cron.log`。

**server timezone 強制 HKT**：
```bash
# 設定系統 TZ 為 HKT（適合 Docker / linux）
export TZ=Asia/Hong_Kong

# 或在 cron line 入面直接設置該行嘅環境：
CRON_SCHEDULE="30 8 * * * TZ=Asia/Hong_Kong bash scripts/run_daily.sh" bash scripts/setup_cron_linux.sh
```

### 2.2 Windows（Task Scheduler）

**安裝**（**必須用 Administrator 身份** 開 PowerShell）：
```powershell
cd C:\Users\madwa\projects\daily-xauusd-bot
.\scripts\setup_task_windows.ps1
```

預設時間：每天 **08:30**。

如果想 自訂時間：

```powershell
.\scripts\setup_task_windows.ps1 -Hour 8 -Minute 30
```

**查看狀態**：
```powershell
.\scripts\setup_task_windows.ps1 -Status
```

**預覽不安裝**：
```powershell
.\scripts\setup_task_windows.ps1 -DryRun
```

**解除安裝**：
```powershell
.\scripts\setup_task_windows.ps1 -Uninstall
```

或用 `.bat`：
```bat
scripts\setup_task_windows.bat status
scripts\setup_task_windows.bat uninstall
scripts\setup_task_windows.bat dry-run
```

**Log 位置**：stdout/stderr 落到 `logs\cron.log`。

**Task Scheduler 後台查看**：
- 開 `taskschd.msc` → 找 `DailyXauusdBrief` job
- 也可以喺 PowerShell 用 `Get-ScheduledTask -TaskName DailyXauusdBrief`

---

## 3. Hermes Cron（推薦）

Hermes 自己嘅 cronjob 對於 Telegram 派送最友善：agent 跑完會自動 deliver 到你 home channel，**你唔需要 setup Bot token / chat id**。

### 3.1 建立 cronjob

最簡方法：用 Hermes 直接 command：

```
scheduled-briefing skill：
  name: gold-daily-brief
  schedule: 30 8 * * *     :: daily 08:30 (server tz = Asia/Hong_Kong)
  prompt: |
    Executing the daily XAUUSD briefing via the project's CLI:
    `cd ~/projects/daily-xauusd-bot && bash scripts/run_daily.sh`

    The script handles:
    - fetch_price (Twelve Data)
    - compute_indicators (MA20/MA50/MA200 + MACD + RSI + Bollinger Bands)
    - fetch_news (newsapi.ai via Event Registry + RSS fallback)
    - generate_markdown (reports/gold/YYYY-MM-DD.md)
    - send_telegram (Bot API route if TELEGRAM_BOT_TOKEN set,
                      Hermes send_message otherwise)

    After completion, summarize: did all 4 steps succeed?
    Were any steps skipped?  Show the latest MD file path.
    If Telegram send failed, show error from logs.
```

或者**用 cronjob 工具**：

```python
cronjob(action="create",
        name="gold-daily-brief",
        schedule="30 8 * * *",
        prompt="<貼上面嗰段 prompt>",
        script="~/projects/daily-xauusd-bot/scripts/run_daily.sh")
```

詳細請參閱 `~/.hermes/skills/cron-jobs/`。

### 3.2 用 files wrapper 而非 inline script

如果Hermes cron 接受 path 引導 `script:` 欄：

```
script: /c/Users/madwa/projects/daily-xauusd-bot/scripts/run_daily.sh
prompt: |
  上面 script have done：fetch price、compute MA20、fetch news、gen MD、send Telegram。
  請確認該次執行的所有 commands 都返回 0，並總結到 Telegram。
```

---

## 4. 排程驗證清單

完成後，請檢查：

- [ ] 手動跑 `bash scripts/run_daily.sh --dry-run` 沒有 error
- [ ] 手動跑 `bash scripts/run_daily.sh --send-only` 能否重發 cache report
- [ ] 排程啟動後 `logs/cron.log` 有 log entry
- [ ] `reports/gold/YYYY-MM-DD.md` 有生成
- [ ] Telegram 收到（如果你用 Bot API route）
- [ ] process exit code = 0（否則記低 error）

---

## 5. 常見問題

| Q | A |
|---|---|
| Cron 跑但係 .env 唔 pick up | 確保 crontab entry 入面有 `cd <project>` 步驟，否則工作 directory 唔係 root，找唔到 .env。|
| PowerShell execution policy 阻擋 | 用 `powershell -ExecutionPolicy Bypass -File ...`，或者 setup 一次 `Set-ExecutionPolicy RemoteSigned`。|
| Task Scheduler 唔 work 但手動 work | 多半是 Windows Account 没登入 (Task 狀態「running」但 process 没 exit)；設 `Run whether user is logged on or not` + 設 startup trigger。|
| Telegram Bot API 返 401 / 403 | Token 過期 / 唔 bind 該 chat id。去 `@BotFather` / chat 重檢查。|
| 想多機下線 retry | OS scheduler 本身已含 `RestartCount 3` （Win task）/唔 retry（cron 用 `--send-only` 手動觸發重試）。|

---

## 6. 時區

無論 OS / Hermes，**預設離香港時區**。如果你 process 跑喺另一個時區：

- Linux `crontab`：set `TZ=Asia/Hong_Kong` 入 cron line（見上）
- Windows Task Scheduler：在 trigger 用 HKT time 直接 set
- Hermes cron：守 Server tz（已為 HKT）

如果你唔肯定，**用 `--send-only`** 手動補發亦算 corrective。
