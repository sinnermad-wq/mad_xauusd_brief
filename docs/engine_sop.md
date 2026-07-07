# Candlestick Engine SOP

## Daily
1. Run engine manually.
2. Log snapshot immediately: `python scripts/log_engine_review.py --input <snapshot.json>`
3. If validation window expires, update outcome:
   `python scripts/update_engine_review.py --review-id <id> --outcome-label correct`

## Weekly
1. Generate weekly report:
   `python scripts/generate_engine_weekly_report.py --week-start YYYY-MM-DD --dry-run`
2. Review:
   - session performance
   - direction performance
   - confidence calibration
   - failure reasons
3. Write 1–3 adjustments for next week in the report's "Next Adjustments" section.

## Monthly
1. Review the 4 weekly reports.
2. Check drift in confidence and outcome quality.
3. Decide whether any rule needs to change.
4. Keep manual-only unless calibration is stable.

---

## Review Workflow (3 steps)

```
Step 1  log:        python scripts/log_engine_review.py --input <snapshot.json>
Step 2  update:     python scripts/update_engine_review.py --review-id <id> --outcome-label correct
Step 3  weekly:     python scripts/generate_engine_weekly_report.py --week-start YYYY-MM-DD --dry-run
```

## Data Locations

| File | Purpose |
|---|---|
| `data/engine_reviews.csv` | All logged reviews |
| `reports/engine_reviews/weekly-YYYY-MM-DD.md` | Weekly calibration reports (local, gitignored) |

## Manual-Only Reminder

All steps require manual initiation. The engine and its review tooling
are read-only analytical tools — never auto-scheduled or wired into
`run_daily.sh` / cron / daily pipeline.