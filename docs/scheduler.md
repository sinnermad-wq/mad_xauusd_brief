# Weekly Digest — Windows Task Scheduler Setup

## Overview

`generate_weekly_digest.py` reads briefings, fusion outputs, and engine reviews from the last N days and writes a structured digest to `data/weekly_digest/`. The dashboard reads the latest digest automatically on startup.

This document covers how to schedule the script via **Windows Task Scheduler**. No scheduler code lives inside Streamlit or the dashboard.

---

## Output Files

| File | Description |
|------|-------------|
| `data/weekly_digest/YYYY-MM-DDThh-mm-ss_7d_digest.json` | Dated digest (always written) |
| `data/weekly_digest/latest_weekly_digest.json` | Symlink-style copy of latest (always overwritten) |
| `data/weekly_digest/YYYY-MM-DD_weekly.md` | Human-readable markdown (only with `--output-md`) |

---

## Quick Test (Manual)

Before scheduling, verify everything works:

```cmd
cd C:\Users\madwa\projects\daily-xauusd-bot
.venv\Scripts\python.exe scripts\generate_weekly_digest.py --window-days 7 --output-md
```

Expected output:
```
14:08:47 [INFO] [Digest] Starting 7d weekly digest generation
14:08:47 [INFO] [Digest] Collected — briefings=16 fusions=1 reviews=2
14:08:47 [INFO] [Digest] Written: ...\data\weekly_digest\2026-07-11T14-08-47_7d_digest.json
14:08:47 [INFO] [Digest] Latest:  ...\data\weekly_digest\latest_weekly_digest.json
14:08:47 [INFO] [Digest] Markdown: ...\data\weekly_digest\2026-07-11_weekly.md
✅ Weekly digest (7d) written.
```

If you see `✅`, you are ready to schedule.

---

## Task Scheduler Setup (Step-by-Step)

### Step 1 — Open Task Scheduler

Press `Win + R`, type `taskschd.msc`, press **Enter**.

### Step 2 — Create Basic Task

1. Right-click **Task Scheduler Library** → **Create Basic Task…**
2. **Name:** `XAUUSD Weekly Digest` (can be anything)
3. **Description:** `Generate weekly XAUUSD digest every Sunday`
4. Click **Next**

### Step 3 — Trigger

1. Select **Weekly**
2. Click **Next**
3. Set **Start:** `2026-07-19` (first Sunday after setup)
4. Set **Time:** `20:00:00`
5. Check the day(s) you want: e.g., only **Sunday**
6. Click **Next**

### Step 4 — Action

1. Select **Start a program**
2. Click **Next**
3. Fill in:

```
Program/script:
  C:\Users\madwa\projects\daily-xauusd-bot\.venv\Scripts\python.exe

Add arguments (optional):
  scripts\generate_weekly_digest.py --window-days 7 --output-md

Start in (optional):
  C:\Users\madwa\projects\daily-xauusd-bot
```

4. Click **Next**

### Step 5 — Review and Finish

1. Review your settings
2. Check **Open the Properties dialog…** → Click **Finish** (if you want to tweak)
3. In Properties, check **Run whether user is logged on or not** (optional — runs even without login)
4. If you check that, click **OK** and enter your Windows password

---

## Alternative: Using `run_weekly_digest.bat`

If you prefer to call a `.bat` wrapper (easier to edit/switch window sizes):

```
Program/script:
  C:\Users\madwa\projects\daily-xauusd-bot\scripts\run_weekly_digest.bat

Add arguments:
  --window-days 7 --output-md

Start in:
  C:\Users\madwa\projects\daily-xauusd-bot
```

Edit `scripts\run_weekly_digest.bat` to adjust window size:
```bat
call !PYTHON_BIN! "%PROJECT_ROOT%\scripts\generate_weekly_digest.py" --window-days 7 --output-md %*
```

Change `7` to `14` or `30` for different windows.

---

## Scheduling Options

| Schedule | Trigger | Arguments |
|----------|---------|-----------|
| Weekly (Sunday 20:00) | Weekly → Sunday → 20:00 | `--window-days 7` |
| Bi-weekly (Sunday 20:00) | Weekly → Sunday → 20:00 | `--window-days 14` |
| Monthly (1st Sunday 20:00) | Monthly → first Sunday → 20:00 | `--window-days 30` |

---

## Verification After Scheduling

### Check next run time
1. Open **Task Scheduler**
2. Find `XAUUSD Weekly Digest` in the list
3. Look at **Next Run Time** column

### Manual test run
1. Right-click the task → **Run**
2. Check the output file:
   ```cmd
   dir C:\Users\madwa\projects\daily-xauusd-bot\data\weekly_digest\
   ```
3. A new `latest_weekly_digest.json` should appear with today's date

### Check last run result
1. Right-click the task → **History** tab
2. Look for Event ID 102 (task completed) or 103 (task failed)

---

## Troubleshooting

### "No module named 'strategy_health'"
The script needs `src` on the Python path. It handles this automatically. If you see this error, run manually once and check that `src/strategy_health/__init__.py` exists.

### "Digest directory not found" warning
Normal on first run — it creates `data/weekly_digest/` automatically.

### Dashboard shows "No weekly digest available"
- Run `scripts\generate_weekly_digest.py` manually first to create the first digest file
- Check that `data/weekly_digest\latest_weekly_digest.json` exists
- The dashboard reads this file on every startup

### Empty digest (0 briefings, 0 fusions)
The script reads from `data/history/*_daily.json` and `data/history/fusion/*.json`. If these are empty or don't exist, the digest will have zero entries. This is expected if no morning pipeline has run yet.

---

## Dashboard Integration

The dashboard (`dashboard.py`) automatically reads `latest_weekly_digest.json` on startup using `load_latest_weekly_digest()` from `strategy_health/digest_loader.py`. No scheduler code lives in the dashboard.

If the digest file doesn't exist, the dashboard shows an info box explaining how to generate the first digest.

---

## Schema

See `generate_weekly_digest.py` for the full schema. Key fields:

```
latest_weekly_digest.json
├── schema_version       "1.0"
├── generated_at         ISO timestamp
├── window_days          7 | 14 | 30
├── dominant_bias        "bullish" | "bearish" | "neutral" | "mixed" | null
├── avg_fusion_confidence float (0-1)
├── summary
│   ├── price            { latest, high, low, change_pct }
│   ├── fusion           { total_runs, bullish/bearish/neutral, avg_confidence, conflicts, trade_candidates }
│   ├── engine_reviews   { total_reviews, avg_confidence, outcomes_correct/incorrect, insufficient }
│   └── news             { total_articles, top_tags }
└── metadata
    ├── fallback_reason  (only if _fallback=true)
    └── _fallback        (only if no file was loaded)
```