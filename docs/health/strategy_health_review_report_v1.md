# Strategy Health Review Report — Dashboard Panel v1

**Status:** v1 (read-only summary, no auto-apply, no execution)
**Module:** `src/strategy_health/review_report.py`
**Dashboard section:** `📋 Review Report` (under 🏥 Strategy Health)
**Tests:** `tests/test_strategy_health_review_report.py` (23 tests, all passing)

---

## 🎯 Goal

Provide a **read-only, human-reviewable summary** in the dashboard that aggregates the
current snapshot + history trends + approvals into one consolidated view.

This is a **review interface**, not a control panel. The Review Report:
- ✅ Aggregates `StrategyHealthSnapshot` + history-derived `HealthTrendMetrics`
- ✅ Surfaces deteriorations, repeats, regime shifts, suggestions, approvals
- ✅ Supports 7d / 14d / 30d window selection
- ❌ Does **NOT** modify strategy configuration
- ❌ Does **NOT** apply, reject, or supersede suggestions
- ❌ Does **NOT** execute trades or place orders
- ❌ Does **NOT** alter health status logic — purely a consumer of health signals

---

## 🏗️ Architecture

```
Dashboard "📋 Review Report" expander
   ↓
build_review_report_all_windows(snap)   # → {"7d": ReviewReport, "14d": ..., "30d": ...}
   ↓
build_review_report(snap, days=7)       # → ReviewReport
   ↓
For each of 6 sections, build ReviewSection(findings, table):
   1. _exec_summary          — Overall status, trend, score
   2. _key_deteriorations    — Per-diagnostic severity + direction
   3. _repeated_diagnostics  — Persistence (% of days in window)
   4. _session_regime        — Dominant regime, mix shift, new sessions
   5. _suggestion_review     — Current + recurring suggestions
   6. _approval_review       — Pending / approved / rejected rates
   ↓
Aggregation via:
   compute_all_trend_windows()     # existing API from history.py
   load_history_entries()          # for fallback detail
   StrategyHealthSnapshot          # current snapshot
```

No new APIs were added at the data layer — the Review Report **reuses** the existing
history module APIs (`compute_all_trend_windows`, `HealthTrendMetrics`,
`DiagnosticTrend`, etc.). The Review Report module is purely a **view layer**.

---

## 📐 Data Classes

```python
@dataclass
class ReviewFinding:
    severity: str   # "ok" | "warn" | "critical" | "info"
    text: str

    def display(self) -> str  # returns icon-prefixed string

@dataclass
class ReviewSection:
    heading: str                       # "1. Executive Summary", etc.
    findings: List[ReviewFinding]      # bullet-point observations
    table: Optional[List[tuple]]       # row tuples for st.table rendering

    def has_content(self) -> bool      # True if any findings or table rows

@dataclass
class ReviewReport:
    window_days: int
    sections: List[ReviewSection]
    has_sufficient_history: bool       # True if window has ≥ 2 entries
    health_score: float                # composite score (-1.0 if no history)
    health_status: str                 # snapshot's current status
    health_status_trend: str           # direction over window
    generated_at_iso: str              # ISO timestamp

    def to_markdown(self) -> str       # full markdown rendering
```

---

## 📋 Section Specs

### 1. Executive Summary
Shows current health status (with icon), trend direction over the window
(↗️ improving / ↘️ degrading / → stable / ? unknown / … insufficient_data),
the composite health score (0-100), entry count breakdown
(green / yellow / red / unknown days), and severity tally from the live snapshot.

### 2. Key Deteriorations
Per-diagnostic table with: name, severity icon, trend arrow, dominant severity.
Triggers a 🔴 critical finding if any diagnostic had critical_days > 0 in window;
🟡 warn otherwise.

### 3. Repeated Diagnostics / Warnings
Filtered to diagnostics that had any warn or critical days in the window.
Table shows: diagnostic_name, warn_days, critical_days, persistence %.

### 4. Session / Regime Review
Reads from `t.regime_trend`:
- Dominant regime
- Mix shift trend direction + average / max %
- Regime distribution table (count + share %)
- New sessions appeared recently (warn if any)

### 5. Suggestion Review
Reads from `t.suggestion_trends`:
- Count of currently active suggestions
- Per-suggestion-type table: occurrences, recurring flag (yes/no)
- Note if no recurring suggestions in window

### 6. Approval Review
Reads from `t.approval_trend`:
- Pending / approved / rejected rates %
- Current backlog (current_pending, current_approved, current_rejected)
- Rate table per status

---

## 🔄 Graceful Fallback

If the history file has **< 2 entries** in the selected window:

- `review.has_sufficient_history = False`
- All sections still render, but trend-derived findings fall back to current snapshot
- Dashboard shows: ⚠️ Insufficient history (< 2 entries). Showing current-snapshot summary only.
- The Executive Summary prepends a warning note about fallback

This prevents empty/silent sections when first adopting the panel.

---

## 🎨 Dashboard Rendering

```
┌─────────────────────────────────────────────────────────────┐
│ 🏥 Strategy Health                                         │
│   ✅ Health: GREEN — All diagnostics within normal range   │
│   📈 Trend Report (7d / 14d / 30d) — [expander]             │
│   Diagnostic summary cards                                  │
│   Suggestions / Approvals                                   │
│   🔧 Snapshot details — [expander]                          │
│                                                             │
│   #### 📋 Review Report                                     │
│   Read-only summary aggregating current snapshot + history.│
│   No auto-apply, no execution.                              │
│                                                             │
│   [Review window: 7d / 14d / 30d] (selectbox, default 7)   │
│                                                             │
│   ┌──┬──────┬─────────┐                                     │
│   │Window│Health│Score │                                     │
│   │ 7d  │GREEN │ 92.5  │                                     │
│   └──┴──────┴─────────┘                                     │
│                                                             │
│   ▼ 1. Executive Summary  (expanded by default)              │
│     - 🟢 Current health status: GREEN                       │
│     - Status trend over 7d window: stable →                  │
│     - Composite health score: 92.5 / 100                     │
│                                                             │
│   ▶ 2. Key Deteriorations                                    │
│   ▶ 3. Repeated Diagnostics / Warnings                      │
│   ▶ 4. Session / Regime Review                              │
│   ▶ 5. Suggestion Review                                    │
│   ▶ 6. Approval Review                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 🧪 Test Coverage (23 tests)

| Class | Tests | Purpose |
|---|---|---|
| `TestReviewFinding` | 4 | Severity icon rendering |
| `TestReviewSection` | 3 | Empty / populated content detection |
| `TestReviewReportToMarkdown` | 3 | Markdown rendering incl. tables + fallback banner |
| `TestBuildReviewReport` | 9 | Integration: empty fallback, full report, sections, window selection |
| `TestBuildReviewReportAllWindows` | 2 | All 3 windows produce 6 sections each |
| `TestNoMutation` | 2 | Pure read — does not write to history.jsonl |

Inheritance / purity tests confirm `build_review_report(snap)` produces no file writes
or side effects on the health subsystem.

---

## 🚦 Guardrails (preserved from v1)

From `docs/health/strategy_health_guardrails.md`:
- **No execution paths** — Review Report is a reader only
- **No auto-apply** — Suggestions remain pending; Review Report surfaces but does not act
- **No health logic mutation** — Health status, severity, diagnostics all unchanged
- **History JSONL remains append-only** — Review Report does not write to history

The Review Report module **imports from history** but **does not need modifications
to history**. Future iterations (v2+) may add review-specific writes (`review_log.jsonl`)
but those are explicitly **out of scope** for v1.

---

## 📚 References

- `src/strategy_health/review_report.py` — module
- `src/strategy_health/history.py` — recomputed dependency (read)
- `tests/test_strategy_health_review_report.py` — 23 tests
- `src/daily_xauusd_brief/dashboard.py` — panel location (`#### 📋 Review Report`)
- `docs/health/strategy_health_engine_v1.md` — upstream spec
