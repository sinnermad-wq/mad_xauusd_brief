# Engine Rule Change Protocol

> **Purpose:** Govern when and how calibration parameters or analytical rules
> in the Candlestick Direction Engine may be changed — without touching
> trading logic, review schema, or automated pipelines.

---

## 1. Scope

Applies to all parameters, thresholds, and rules defined in:

- `src/daily_xauusd_brief/direction_engine.py`
- `scripts/log_engine_review.py`
- Any shared constants / enum sets used by engine scripts

Does **NOT** apply to:
- Dashboard, cron, journal assembler, Telegram output
- `engine_reviews.csv` schema (which has its own stability rule: no new required columns without version bump)

---

## 2. When a Change Is Allowed

A change may proceed if **all** of the following are true:

1. **Justification documented** — a rule change request (see §5) has been filled out and reviewed.
2. **No trading logic changed** — the change affects only analytical parameters (thresholds, lookback periods, classification labels, indicator weights) and not any order-routing, position-sizing, or risk-management code.
3. **No new required CSV columns** — `engine_reviews.csv` schema remains backward-compatible; new columns must be optional.
4. **No automation enabled** — the engine remains manual-only; no cron / webhook / daemon wiring is introduced.
5. **Affected scripts updated** — `log_engine_review.py`, `update_engine_review.py`, `validate_engine_reviews.py` and any other consumer are synchronized to the new rule set.
6. **AGENTS.md updated** — the relevant section (e.g., parameter descriptions) is updated alongside the code change.

---

## 3. When a Change Is NOT Allowed

- ❌ Auto-scheduling the engine without a full proposal + PR review.
- ❌ Changing review schema fields without a major version bump.
- ❌ Introducing new paid data sources without explicit user consent.
- ❌ Changing the trading logic (order sizing, stop-loss, entry signals).
- ❌ Modifying `run_daily.sh` / `run_daily.bat` to include engine execution.
- ❌ Adding any webhook or daemon that runs the engine automatically.

---

## 4. Change Request Template

File as a GitHub issue or as a section in `docs/rule_changes.md`.

```
## Rule Change Request — [short title]

**Date:** YYYY-MM-DD
**Requester:** [name]
**Status:** Proposed / Approved / Rejected / Rolled Back

### What is being changed?
[Description of the parameter, threshold, or rule]

### Current value
[Current value]

### Proposed new value
[Proposed value]

### Why?
[Brief rationale]

### Evidence / Analysis
[Any backtest results, chart examples, or reasoning supporting the change]

### Risks
[Potential downsides or edge cases]

### Required after-change validation
- [ ] 1-week observation: run validate_engine_reviews.py + check metrics
- [ ] 4-week review: compare outcome_label accuracy before vs after
- [ ] Monthly report: include rule-change impact note in generate_engine_monthly_report.py output

### Rollback plan
[How to revert if the change degrades accuracy]

### Sign-off
- Requester: [ ]
- Reviewer: [ ]
```

---

## 5. Validation After a Change

Every approved change must be validated at three milestones:

### 5.1 Day 7 — 1-Week Checkpoint

Run the following manually and record results in `docs/rule_changes.md`:

```bash
python scripts/validate_engine_reviews.py --days 30 --format text
python scripts/query_engine_ops.py --summary
```

**Pass criteria:** No new critical errors, error rate stable or improved.

### 5.2 Day 28 — 4-Week Review

Generate a weekly report and compare:

```bash
python scripts/generate_engine_weekly_report.py --input data/engine_reviews.csv
```

**Pass criteria:** Outcome accuracy (correct / total resolved) not degraded by >10 percentage points vs. the 4 weeks prior to the change.

### 5.3 Monthly — Monthly Report

```bash
python scripts/generate_engine_monthly_report.py --input data/engine_reviews.csv
```

**Pass criteria:** Monthly report includes a `## Rule Changes` section citing this change and its measured impact. The change remains in effect only if the monthly review sign-off is granted.

---

## 6. Rollback Policy

If at any validation checkpoint the change shows degradation:

1. **Revert the parameter** to its previous value in the source file.
2. **Document rollback** in `docs/rule_changes.md` with:
   - Date of rollback
   - Reason (which metric degraded)
   - Observed vs. expected values
3. **Do not re-attempt the same change** within 4 weeks without new evidence.

A rollback does not require a full RFC — the rule change log entry with "Rolled Back" status is sufficient.

---

## 7. Logging & Changelog Rules

### What must be logged

Every rule change (proposed, approved, rejected, or rolled back) must be recorded in `docs/rule_changes.md`. Entries must include:

- Date
- Short description
- Before / after values
- Who approved it
- Validation checkpoint results (once available)

### What must NOT be in the changelog

- Execution traces, stack dumps, or debug output
- Personal opinions not backed by data
- Discussions that did not result in a decision

### Changelog file location

`docs/rule_changes.md` — appended to, never overwritten.

### Reference in AGENTS.md

Any new or changed parameters in `direction_engine.py` must be documented in the relevant AGENTS.md section, with a link to `docs/rule_change_protocol.md`.

---

## 8. SOP Quick Reference

```
Want to change a rule?
  1. Check §3 (NOT allowed?) → if yes, stop.
  2. Fill out §4 template → open PR or add to docs/rule_changes.md
  3. Get sign-off from reviewer
  4. Make code change + update AGENTS.md
  5. Day 7:  run validate_engine_reviews.py + query_engine_ops.py
  6. Day 28: run generate_engine_weekly_report.py — compare accuracy
  7. Monthly: run generate_engine_monthly_report.py — record in changelog
  8. If degraded: rollback per §6, wait 4 weeks
```

---

*Last reviewed: 2026-07-07*