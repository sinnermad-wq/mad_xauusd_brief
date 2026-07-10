# Strategy Health Monitor + Suggestion Engine v1

> Read-only diagnostics layer. No auto-trade. No broker execution.
> Manual review + human approval required before any parameter change.

## Overview

The engine produces a `StrategyHealthSnapshot` from six rolling diagnostic
categories:

| Diagnostic | Source | Primary metrics |
|---|---|---|
| `performance` | backtest report / trades | hit-rate, expectancy, avg gain/loss |
| `regime` | fusion_history signals | bias_regime, session breakdown, mix shift |
| `cost` | backtest cost fields | cost_drag_pct, avg cost/trade |
| `signal` | fusion_history signals | low-conf hit-rate drift ratio |
| `drawdown` | equity curve from trades | max_dd_pct, current_dd_pct, recovery_factor |
| `freshness` | file mtimes + structure | age per source, missing_count |

## Suggestion kinds (priority order)

| Priority | Kind | Trigger |
|---|---|---|
| 1 | `pause_strategy` | 2+ critical diagnostics |
| 2 | `revalidate_backtest` | cost_drag ≥ 30% + hit-rate degradation |
| 3 | `disable_session` | session hit-rate < threshold |
| 4 | `tighten_filter` | low-conf drift ratio < 0.65 |
| 5 | `reduce_size` | drawdown ≥ 7.5% or cost_drag ≥ 7.5% |
| 6 | `review_parameters` | regime mix shift ≥ 40% |
| 7 | `watch_only` | any warn diagnostic, no critical |
| 8 | `keep_running` | all diagnostics ok |

## Approval workflow

1. `load_latest_snapshot()` or `load_snapshot_from_paths(...)` → snapshot
2. Review `snapshot.suggestions` and `snapshot.pending_approvals`
3. Edit `data/strategy_health/approvals.json` manually:
   ```json
   {
     "sug-20260710-xxx": {
       "suggestion_id": "sug-20260710-xxx",
       "kind": "review_parameters",
       "status": "approved",   // pending | approved | rejected | superseded
       "note": "Reviewed — shift is structural, not noise.",
       "resolved_by": "mad",
       "created_at": "2026-07-10T09:00:00Z",
       "updated_at": "2026-07-10T09:05:00Z"
     }
   }
   ```
4. Engine on next run reads updated status; already-resolved suggestions
   are dropped from `pending_approvals`.

## Usage

```python
from strategy_health import load_latest_snapshot, load_snapshot_from_paths

# Auto-discover latest data files
snap = load_latest_snapshot()
print(snap.health_status)          # green | yellow | red | unknown
print(snap.suggestions)           # tuple of StrategySuggestion

# With explicit paths (recommended for cron/CLI)
snap = load_snapshot_from_paths(
    fusion_history_path="data/fusion_history/2026-07-10.json",
    backtest_report_path="data/backtest/2026-07-10_backtest.json",
)
print(snap.diagnostics_by_severity("critical"))
```

## Rolling windows (configurable via StrategyHealthConfig)

| Parameter | Default | Description |
|---|---|---|
| `performance_window` | 20 | Most-recent closed trades |
| `regime_window` | 30 | Fusion history signals (days) |
| `cost_window` | 50 | Closed trades for cost calc |
| `signal_window` | 50 | Signals for drift comparison |
| `drawdown_window_days` | 90 | Days for equity curve |
| `fusion_history_ttl_minutes` | 90 | Max file age before warn |
| `backtest_report_ttl_minutes` | 240 | Max file age before warn |

## Thresholds

| Metric | Warn | Critical |
|---|---|---|
| `cost_drag_pct` | 10% | 30% |
| `drawdown_pct` | 7.5% | 15% |
| `regime_mix_shift_pct` | 40% | — |
| `low_conf_hit_rate_ratio` | 0.65 | — |
| `session_hit_rate_disable` | — | 35% |
| `expected_baseline_hit_rate` | 55% | — |

## Output structure

```
StrategyHealthSnapshot
  snapshot_id, generated_at, health_status
  diagnostics[6]  → StrategyDiagnostic (name/severity/summary/metrics/reasons)
  suggestions[N]  → StrategySuggestion (kind/priority/title/rationale/metrics/actions)
  pending_approvals[N] → StrategyApprovalState (suggestion_id/status/note)
  config_snapshot  → dict of active thresholds
  warnings  → tuple of string codes
  source_inventory  → dict of bools
```

## Data source priority

If both `fusion_history` signals AND `backtest` trades are available,
they are merged for the drawdown/equity-curve diagnostic. If only one is
present, that single source is used. If neither is present, freshness
diagnostic goes critical immediately.