# Review Actions Queue — Dashboard Panel v1

**Status:** v1 (manual-only, read-only, no auto-apply, no execution)
**Module:** `src/strategy_health/review_actions_queue.py`
**Dashboard:** `🎯 Review Actions Queue` expander under `🏥 Strategy Health`
**Tests:** `tests/test_strategy_health_review_actions_queue.py` (25 tests, all passing)
**Public API:**
```python
from strategy_health import build_review_actions_queue, build_review_actions_queue_all_windows
```

---

## 🎯 Goal

Produce a **prioritized, sortable action list** for human review — aggregating:
- Pending approvals (age-sorted)
- Repeated suggestions (frequency-sorted)
- Repeated diagnostics (frequency × severity-sorted)
- Top deteriorations (severity × trend-sorted)

**Read-only guarantee:** does not write to history, does not auto-apply anything,
does not execute trades. Pure consumer of `StrategyHealthSnapshot` + history trends.

---

## 📐 Data Model

```python
class ReviewAction:
    action_id: str          # stable id: "approval:<sid>", "diag:<name>", "sug:<kind>"
    category: str            # "approval" | "suggestion" | "diagnostic" | "deterioration"
    title: str              # human-readable summary
    detail: str = ""        # longer context / metrics snapshot
    severity: str = "info"  # "critical" | "warn" | "ok" | "info"
    priority_score: float = 0.0  # higher = more urgent
    age_days: float = 0.0   # approximate age in days
    frequency: int = 1      # occurrences in window (>= 1)
    trend: str = "stable"   # "improving" | "stable" | "degrading" | "unknown"


@dataclass
class ReviewActionsQueue:
    window_days: int
    actions: List[ReviewAction]     # sorted descending by priority_score
    has_sufficient_history: bool
    empty_reason: str = ""
    generated_at_iso: str

    # Methods
    def is_empty(self) -> bool
    def filter_by_category(self, cat: str) -> List[ReviewAction]
    def to_table(self) -> Optional[List[tuple]]   # for st.table()
    def to_markdown(self) -> str
    def to_csv(self) -> str
```

---

## 🔢 Priority Scoring

```
priority_score = severity_weight + trend_weight + frequency_weight + age_weight

severity_weight:   critical=40, warn=20, ok=5, info=0
trend_weight:      degrading=20, stable=0, improving=-10, unknown=0
frequency_weight:  min(freq / window_days * 30, 15)
age_weight:       min(age_days * 0.3, 15)
```

Higher = more urgent. Score is capped per component to keep it comparable across windows.

---

## 📊 Categories

| Category | Source | Priority basis |
|---|---|---|
| `approval` | `snap.pending_approvals` (pending status) | severity + age |
| `deterioration` | `DiagnosticTrend` where `trend != improving` | severity × days |
| `diagnostic` | `DiagnosticTrend.warn_days + critical_days > 0` | frequency × severity |
| `suggestion` | Repeated `StrategySuggestion.kind` in window | frequency × priority |

---

## 📋 Dashboard UI

```
┌────────────────────────────────────────────────────────────────┐
│ #### 🎯 Review Actions Queue                                    │
│ Prioritized action list for manual review. No auto-apply.       │
│                                                                │
│ [ Queue window: 7 day(s) ] (selectbox: 7 / 14 / 30)            │
│                                                                │
│ ┌─────────────┬──────────────┬─────────────────────────────┐  │
│ │Queue window │ Total actions│ Top priority                │  │
│ │     7d      │      4       │ Pending approval: review... │  │
│ └─────────────┴──────────────┴─────────────────────────────┘  │
│                                                                │
│ ⚠️ Insufficient history. Showing current-snapshot queue only.  │
│ (when has_sufficient_history = False)                         │
│                                                                │
│ ✅ No actions queued. All items reviewed or no issues detected. │
│ (when queue is empty)                                          │
│                                                                │
│ ┌─ markdown ──────────┐ ┌─ CSV ──────────────────────────────┐│
│ │ # Review Actions Q  │ │ window_days,priority,category,...  ││
│ │ Total actions: 4    │ │ 7,75.0,approval,critical,...      ││
│ │                     │ │                                    ││
│ └─────────────────────┘ └────────────────────────────────────┘│
│                                                                │
│ **4 action(s) — sorted by priority**                           │
│ PRIORITY | CATEGORY    | TITLE                    | SEVERITY | AGE  │
│ 75.0     | approval    | Pending approval: re... | CRITICAL  | 12d  │
│ 63.4     | deterioration| overall: critical 1d.. | CRITICAL  | 7d   │
│ 58.1     | diagnostic  | Repeated performance... | CRITICAL  | 7d   │
│ 26.3     | approval    | Pending approval: red... | WARN      | 1d   │
└────────────────────────────────────────────────────────────────┘
```

---

## 🧪 Test Coverage (25 tests)

| Class | Count | Coverage |
|---|---|---|
| `TestReviewAction` | 3 | Construction, fields, attribute access |
| `TestReviewActionsQueue` | 10 | Empty state, filter, to_table, to_csv, to_markdown, edge cases |
| `TestBuildReviewActionsQueue` | 7 | Integration: queue type, empty graceful, approvals appear, diagnostics from history, sorting, all windows |
| `TestBuildAllWindows` | 2 | Returns 7d/14d/30d keys, each queue is valid |
| `TestReadOnlyGuardrails` | 2 | No history file write, idempotent calls |

---

## 🚫 Guardrails

- ❌ No auto-apply of suggestions
- ❌ No execution / trade placement
- ❌ No writes to history JSONL
- ❌ No mutations to strategy health state
- ✅ Read-only consumer of snapshot + history trends
- ✅ Graceful empty-state when snapshot is clean + no history
- ✅ All methods return serialisable primitives (str / float / int / list)

---

## 📚 References

- `src/strategy_health/review_actions_queue.py`
- `src/daily_xauusd_brief/dashboard.py` — `🎯 Review Actions Queue` panel
- `tests/test_strategy_health_review_actions_queue.py` — 25 tests
- `docs/health/strategy_health_engine_v1.md`
- `docs/health/strategy_health_review_report_v1.md`