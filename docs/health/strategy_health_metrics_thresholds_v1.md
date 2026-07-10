# Strategy Health Metrics & Threshold Specification v1

> Formal specification for every metric, threshold, baseline, severity rule,
> and suggestion trigger in Strategy Health Monitor v1.
> This document is the **source of truth** — code must match these definitions.
> Any divergence is a bug to be fixed in the code.

---

## 1. Overview

### 1.1 Design Principles

| Principle | Implication |
|---|---|
| **Read-only** | No metric ever writes state or triggers execution |
| **No auto-apply** | All suggestions require human approval |
| **Graceful degradation** | Missing/stale data never raises — returns `unknown`/`warn`/`critical` with a reason |
| **Deterministic** | Same inputs always produce same outputs |
| **Frozen snapshot** | Metrics are computed once per snapshot; no streaming updates |

### 1.2 Metric Categories

| Category | File | Primary Source | Window |
|---|---|---|---|
| Performance | `diagnostics/performance.py` | backtest report `trades[]` | Rolling last N trades |
| Regime | `diagnostics/regime.py` | fusion_history signals | Rolling last N days |
| Cost | `diagnostics/cost.py` | backtest report `trades[]` | Rolling last N closed trades |
| Signal Quality | `diagnostics/signal.py` | fusion_history signals | Rolling last N signals |
| Drawdown / Risk | `diagnostics/drawdown.py` | backtest report `trades[]` | Rolling last N days |
| Freshness | `diagnostics/freshness.py` | file mtimes | Snapshot-time check |

---

## 2. Performance Metrics

### 2.1 Source

- **Input:** `backtest_report["trades"]` — list of trade dicts with `pnl_pct`
- **Fallback:** `backtest_report["outcomes"]`
- **Minimum sample:** `performance_window` trades required for valid reading

### 2.2 Metrics Computed

#### 2.2.1 Hit Rate (`hit_rate_pct`)

```
hit_rate_pct = wins / total_trades * 100
wins = count(trades where pnl_pct > 0)
total_trades = len(trades)
```

#### 2.2.2 Average Win / Average Loss (`avg_gain_pct`, `avg_loss_pct`)

```
avg_gain_pct = mean([t.pnl_pct for t in trades if t.pnl_pct > 0])
avg_loss_pct = mean([t.pnl_pct for t in trades if t.pnl_pct < 0])
# If no wins: avg_gain_pct = 0
# If no losses: avg_loss_pct = 0
```

#### 2.2.3 Expectancy (`expectancy_pct`)

```
expectancy_pct = (hit_rate_pct / 100 * avg_gain_pct) - ((1 - hit_rate_pct / 100) * abs(avg_loss_pct))
```

#### 2.2.4 Profit Factor (`profit_factor`)

```
gross_gain = sum([t.pnl_pct for t in trades if t.pnl_pct > 0])
gross_loss = abs(sum([t.pnl_pct for t in trades if t.pnl_pct < 0]))
profit_factor = gross_gain / gross_loss  if gross_loss > 0 else float('inf')
```

#### 2.2.5 Average PnL (`avg_pnl_pct`)

```
avg_pnl_pct = mean([t.pnl_pct for t in trades])
```

### 2.3 Rolling Window

- **Default:** 20 most recent closed trades
- **Config field:** `performance_window`
- Trades must be ordered oldest→newest; window takes the last N

### 2.4 Baseline Precedence

| Priority | Source | Condition |
|---|---|---|
| 1 | `backtest_report["stats"]["baseline_hit_rate_pct"]` | Key exists |
| 2 | `cfg.expected_baseline_hit_rate` | Config default |
| 3 | Hard fallback `55.0` | Always available |

### 2.5 Thresholds

| Condition | Severity | Reason |
|---|---|---|
| `hit_rate_pct < baseline - 15` OR `expectancy_pct < 0` | `critical` | Significantly underperforming |
| `hit_rate_pct < baseline - 5` OR `avg_pnl_pct < baseline * 0.9` | `warn` | Mildly underperforming |
| `sample_size < performance_window * 0.5` | `warn` | Insufficient data for reliable signal |
| All above thresholds met | `ok` | Within acceptable range |
| `total_trades == 0` | `unknown` | No data available |

### 2.6 Severity Mapping

```
hit_rate_pct >= baseline              → ok
baseline - 15 < hit_rate_pct < baseline → warn
hit_rate_pct <= baseline - 15        → critical
```

### 2.7 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `revalidate_backtest` | `critical` severity AND `cost_drag >= cost_drag_critical_pct` AND `hit_rate_pct < baseline - 10` |
| `watch_only` | `warn` severity, no critical in other diagnostics |
| `keep_running` | `ok` severity, all other diagnostics `ok` |

### 2.8 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| `backtest_report` is `None` | Return `unknown` severity, summary="No trades available" |
| `trades` key missing | Check `outcomes`; if also missing → `unknown` |
| `pnl_pct` field missing | Skip that trade, decrement denominator |
| Non-numeric `pnl_pct` | Skip that trade |
| All trades skipped | Return `unknown` severity |
| `performance_window = 0` | Return `unknown` severity |
| Negative window | Clamp to `1` |

---

## 3. Regime Metrics

### 3.1 Source

- **Input:** `fusion_signals` — list of signal dicts with `bias_regime`, `session_label`, `pnl_pct`
- **Minimum sample:** `regime_window / 2` signals required for valid comparison

### 3.2 Metrics Computed

#### 3.2.1 Bias Regime Breakdown

For each unique `bias_regime` value in the window:
```
regime_breakdown[regime] = {
    n: count(signals with this regime),
    wins: count(where pnl_pct > 0),
    losses: count(where pnl_pct <= 0),
    hit_rate_pct: wins / n * 100
}
```

#### 3.2.2 Session Breakdown

For each unique `session_label` value in the window:
```
session_breakdown[session] = {
    n: count(signals with this session),
    wins: count(where pnl_pct > 0),
    losses: count(where pnl_pct <= 0),
    hit_rate_pct: wins / n * 100
}
```

#### 3.2.3 Regime Mix Shift (`regime_mix_shift_pct`)

```
recent_half  = signals[len(signals) // 2:]
prior_half   = signals[:len(signals) // 2]

def mix(signals):
    counts = Counter([s.get("bias_regime", "unknown") for s in signals])
    total = sum(counts.values())
    return {k: v / total for k, v in counts.items()}

mix_recent = mix(recent_half)
mix_prior  = mix(prior_half)
all_regimes = set(mix_recent) | set(mix_prior)

# Implementation: max absolute difference across all regimes
regime_mix_shift_pct = (
    max(abs(mix_recent.get(r, 0.0) - mix_prior.get(r, 0.0)) for r in all_regimes)
    if all_regimes else 0.0
) * 100.0

# Result is in range [0, 100]: 0 = identical, 100 = complete swap
```

> **Spec vs code alignment:** The implementation uses `max(abs(diff))` (maximum per-regime
> shift), not the sum-of-absolutes formula. The threshold of 40% warning / 60% critical
> in this spec refers to the `max(diff)` result in percentage terms.

#### 3.2.4 Session Disabled Candidates

```
disabled_sessions_candidates = [
    session for session, data in session_breakdown.items()
    if data.n >= min_session_sample
    and data.hit_rate_pct < session_hit_rate_disable_threshold
]
```

### 3.3 Rolling Window

- **Default:** 30 signals (by count, not days)
- **Config field:** `regime_window`

### 3.4 Thresholds

| Condition | Severity | Reason |
|---|---|---|
| `regime_mix_shift_pct >= 60` | `critical` | Major regime change |
| `regime_mix_shift_pct >= 40` | `warn` | Moderate regime shift |
| Any session in `disabled_sessions_candidates` | `warn` | Session underperforming |
| `sample_size < min(regime_window, 10)` | `warn` | Insufficient data |
| `total_signals == 0` | `unknown` | No data |

### 3.5 Severity Mapping

```
critical: regime_mix_shift_pct >= 60 OR (any session_candidate AND sample >= 30)
warn:     regime_mix_shift_pct >= 40 OR disabled_sessions_candidates not empty
unknown:  signals == [] or sample < 5
ok:      none of the above
```

### 3.6 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `disable_session` | Any session in `disabled_sessions_candidates` |
| `review_parameters` | `regime_mix_shift_pct >= regime_mix_shift_warn_pct` |

### 3.7 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| `fusion_signals` is `None` or `[]` | Return `unknown` severity |
| Signal dict missing `bias_regime` | Treat as `"unknown"` — included in total count |
| Signal dict missing `session_label` | Treat as `"other"` |
| Signal dict missing `pnl_pct` | Skip from win/loss count, include in regime/session count |
| `regime_window <= 0` | Clamp to `1` |
| All signals garbage | Return `unknown` with summary of why |

---

## 4. Cost Metrics

### 4.1 Source

- **Input:** `backtest_report["trades"]` — list of trade dicts with `pnl_pct` and/or `cost_pct`
- **Fallback:** Compute cost from `pnl_pct` spread between winners/losers if `cost_pct` absent

### 4.2 Metrics Computed

#### 4.2.1 Cost Per Trade (`cost_per_trade_pct`)

```
If "cost_pct" field present:
    cost_per_trade_pct = mean([t.cost_pct for t in trades])

Else (inferred from win/loss asymmetry):
    avg_winners = mean([t.pnl_pct for t in trades if t.pnl_pct > 0])
    avg_losers  = abs(mean([t.pnl_pct for t in trades if t.pnl_pct < 0]))
    expected_win = avg_winners * hit_rate - avg_losers * (1 - hit_rate)
    # Cost drag = expected_win_without_cost - actual_expectancy
    cost_per_trade_pct = inferred cost as percentage of notional
```

#### 4.2.2 Cost Drag (`cost_drag_pct`)

```
# Cost drag = percentage of gross profit lost to costs
gross_profit = sum([t.pnl_pct for t in trades if t.pnl_pct > 0])
total_cost   = sum([abs(t.get("cost_pct", 0)) for t in trades])
cost_drag_pct = (total_cost / (gross_profit + total_cost)) * 100
              if (gross_profit + total_cost) > 0 else 0.0
```

#### 4.2.3 Average Cost Per Trade

```
avg_cost_per_trade_pct = mean([abs(t.get("cost_pct", 0)) for t in trades])
```

### 4.3 Rolling Window

- **Default:** 50 most recent closed trades
- **Config field:** `cost_window`

### 4.4 Thresholds

| Condition | Severity | Reason |
|---|---|---|
| `cost_drag_pct >= cost_drag_critical_pct (30%)` | `critical` | Execution costs are destroying edge |
| `cost_drag_pct >= cost_drag_warn_pct (10%)` | `warn` | Elevated costs |
| No cost data (all zero) | `ok` | No measurable drag |
| `total_trades == 0` | `unknown` | No data |

### 4.5 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `revalidate_backtest` | `critical` AND `performance.hit_rate_pct < baseline - 10` |
| `reduce_size` | `cost_drag_pct >= cost_drag_warn_pct` |

### 4.6 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| No `cost_pct` field and no winners/losers asymmetry | Return `ok`, cost metrics all `0.0` |
| `cost_window = 0` | Return `unknown` |
| All trades zero PnL | Return `ok`, cost `0.0` |

---

## 5. Signal Quality Drift

### 5.1 Source

- **Input:** `fusion_signals` — list of signals with `confidence`, `pnl_pct`
- **Confidence buckets:**
  - `low_conf`: `confidence < 0.55`
  - `mid_conf`: `0.55 <= confidence < 0.70`
  - `high_conf`: `confidence >= 0.70`

### 5.2 Metrics Computed

#### 5.2.1 Hit Rate by Confidence Bucket

```
def hit_rate(signals):
    wins = sum(1 for s in signals if s.get("pnl_pct", 0) > 0)
    total = len(signals)
    return (wins / total * 100) if total > 0 else None

recent_signals = signals[len(signals) // 2:]
prior_signals = signals[:len(signals) // 2]

recent_low_conf  = [s for s in recent_signals if s.get("confidence", 1) < 0.55]
prior_low_conf   = [s for s in prior_signals  if s.get("confidence", 1) < 0.55]
recent_mid_conf  = [s for s in recent_signals if 0.55 <= s.get("confidence", 1) < 0.70]
prior_mid_conf   = [s for s in prior_signals  if 0.55 <= s.get("confidence", 1) < 0.70]
```

#### 5.2.2 Low-Confidence Drift Ratio (`low_conf_recent_vs_prior_ratio`)

```
hr_recent = hit_rate(recent_low_conf)    # e.g., 45.0
hr_prior  = hit_rate(prior_low_conf)     # e.g., 60.0

low_conf_recent_vs_prior_ratio = hr_recent / hr_prior  if hr_prior > 0 else float('inf')
```

#### 5.2.3 Overall Hit Rate Shift

```
hr_overall_recent = hit_rate(recent_signals)
hr_overall_prior  = hit_rate(prior_signals)
overall_hit_rate_shift_pct = abs(hr_overall_recent - hr_overall_prior)
```

### 5.3 Rolling Window

- **Default:** 50 signals (split recent/prior halves at 25 each)
- **Config field:** `signal_window`

### 5.4 Thresholds

| Condition | Severity | Reason |
|---|---|---|
| `low_conf_recent_vs_prior_ratio < 0.5` | `critical` | Dramatic degradation of low-confidence signals |
| `low_conf_recent_vs_prior_ratio < 0.65` | `warn` | Declining low-conf quality |
| `overall_hit_rate_shift_pct > 20` | `warn` | Significant overall quality change |
| Fewer than 10 signals in either half | `warn` | Insufficient data |
| Fewer than 5 signals total | `unknown` | No meaningful data |

### 5.5 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `tighten_filter` | `low_conf_recent_vs_prior_ratio < low_conf_drift_ratio_warn (0.65)` |

### 5.6 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| `confidence` field missing | Default to `0.5` (mid-point); below `low_conf` threshold → falls into `low_conf` bucket, may trigger drift warnings. Treat unknown confidence as neutral signal. |
| `pnl_pct` missing | Skip from hit-rate calculation for that signal |
| `fusion_signals` empty | Return `unknown` |
| Only one half has signals | Return `warn`, ratio set to `float('inf')` (no degradation detected) |

---

## 6. Drawdown / Risk Stress

### 6.1 Source

- **Input:** `backtest_report["trades"]` — list of trade dicts with `pnl_pct`, ordered oldest→newest

### 6.2 Metrics Computed

#### 6.2.1 Equity Curve

```
equity = [1.0]  # starting capital = 1.0 (compounded unit); 1.0 = break-even
for t in trades:
    equity.append(equity[-1] * (1 + t.pnl_pct / 100))
```

> **Note:** Implementation starts at `1.0`, not `100.0`. All subsequent
> calculations (max drawdown, recovery factor) use this scale.

#### 6.2.2 Running Maximum (`running_max`)

```
running_max = [equity[0]]
for e in equity[1:]:
    running_max.append(max(running_max[-1], e))
```

#### 6.2.3 Drawdown Series (`drawdown_series`)

```
drawdown[i] = (equity[i] - running_max[i]) / running_max[i] * 100
# Always <= 0; 0 = at all-time high
```

#### 6.2.4 Max Drawdown (`max_dd_pct`)

```
max_dd_pct = min(drawdown_series)  # most negative value
```

#### 6.2.5 Current Drawdown (`current_dd_pct`)

```
current_dd_pct = drawdown_series[-1]
```

#### 6.2.6 Recovery Factor (`recovery_factor`)

```
total_return_pct = (equity[-1] - 1.0) * 100.0
# total_return_pct: net percentage return from starting equity of 1.0

recovery_factor = total_return_pct / max_dd_pct
                if max_dd_pct > 0 else 0.0
# Interpretation: how many percentage points of return per percentage point of max drawdown
# recovery_factor = 3.0 → earned 3x the max drawdown as net return
```

> **Spec vs code alignment:** Implementation uses percentage units for both
> numerator and denominator: `(equity[-1] - 1.0) * 100 / max_dd_pct`.

#### 6.2.7 Trade Frequency (`trades_per_day`)

```
# Uses mtime of backtest report as reference point
file_age_days = (now - mtime_of_backtest_report) in days
trades_per_day = len(trades) / file_age_days if file_age_days > 0 else 0
```

### 6.3 Rolling Window

- **Default:** 90 calendar days (equity from all trades whose timestamps fall within this window; fallback to all trades if no timestamps)
- **Config field:** `drawdown_window_days`

### 6.4 Thresholds

> **Note:** `max_dd_pct` is a **positive** value representing drawdown magnitude
> (e.g., `10.0` = 10% below peak). Thresholds use `>=` comparison.

| Condition | Severity | Reason |
|---|---|---|
| `max_dd_pct >= drawdown_critical_pct (15.0%)` | `critical` | Severe drawdown stress |
| `max_dd_pct >= drawdown_warn_pct (7.5%)` | `warn` | Elevated drawdown |
| `recovery_factor < 0.5` AND `len(trades) >= 20` | `warn` | Poor recovery |
| `total_trades == 0` | `unknown` | No data |

### 6.5 Severity Mapping

```
critical: max_dd_pct >= drawdown_critical_pct OR (recovery_factor < 0.5 AND trades >= 20)
warn:     max_dd_pct >= drawdown_warn_pct
unknown:  no trades
ok:      none of the above
```

### 6.6 Current Drawdown Addition

If current drawdown exceeds half the critical threshold (even if max_dd is lower), severity upgrades to warn:

```
if current_dd_pct >= crit_thr / 2.0:
    if severity == SEVERITY_OK:
        severity = SEVERITY_WARN
```

This captures *active* drawdown stress even when peak drawdown has recovered.

### 6.6 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `reduce_size` | `max_dd_pct >= drawdown_warn_pct` OR `cost_drag_pct >= drawdown_warn_pct` |
| `pause_strategy` | `max_dd_pct >= drawdown_critical_pct` AND 2+ other criticals |

### 6.7 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| No trades | Return `unknown` |
| `pnl_pct` missing | Skip trade |
| Negative equity (equity[-1] < 0) | Set `max_dd_pct = -100`, `current_dd_pct = -100` |
| `drawdown_window_days = 0` | Use all available trades |

---

## 7. Data Freshness

### 7.1 Source

File mtimes checked at snapshot generation time:

| Source | Expected path pattern | TTL |
|---|---|---|
| Fusion history | `data/fusion_history/*.json` (latest by mtime) | 90 min |
| Backtest report | `data/backtest/*.json` (latest by mtime) | 240 min |
| Candlestick snapshot | `data/candlestick/latest_snapshot.json` | 90 min |

### 7.2 Metrics Computed

For each source:
```
age_minutes = (snapshot_now - file_mtime).total_seconds() / 60
status = "ok"      if age_minutes <= ttl
       = "stale"   if ttl < age_minutes <= ttl * 2
       = "critical" if age_minutes > ttl * 2
```

Aggregated:
```
missing_count  = count of sources where file does not exist
stale_count    = count of sources where status == "stale"
critical_count = count of sources where status == "critical"
```

### 7.3 Thresholds

| Condition | Severity | Reason |
|---|---|---|
| Any source `critical` | `critical` | Data significantly overdue |
| Any source `stale` | `warn` | Data slightly overdue |
| Any source `missing` | `critical` | Required data source absent |
| All sources present and fresh | `ok` | Within TTL |
| All sources missing | `critical` | No data whatsoever |
| Freshness check cannot run (permissions, etc.) | `unknown` | Integrity check failed |

### 7.4 Suggestion Triggers

| Suggestion | Trigger |
|---|---|
| `revalidate_backtest` | All sources `critical` or `missing` AND performance data unavailable |

### 7.5 Missing / Invalid Data Behavior

| Situation | Behavior |
|---|---|
| File does not exist | `status = "missing"`, severity driven by `missing_count` |
| File mtime in future | Treat as `critical` (clock skew) |
| File has zero size | Treat as `missing` |
| Permissions denied | Return `unknown`, log warning |
| `now_utc` not provided | Use `datetime.now(tz=timezone.utc)` |
| Path is `None` | Skip that source entirely |

---

## 8. Health Verdict Rules

The overall `health_status` is derived from the 6 diagnostics:

```
if any(d.severity == SEVERITY_CRITICAL for d in diagnostics):
    health_status = HEALTH_RED

elif any(d.severity == SEVERITY_WARN for d in diagnostics):
    health_status = HEALTH_YELLOW

elif all(d.severity == SEVERITY_UNKNOWN for d in diagnostics):
    health_status = HEALTH_UNKNOWN

else:
    health_status = HEALTH_GREEN
```

### 8.1 Verdict → Suggestion Mapping

| Health Status | Top Suggestion(s) |
|---|---|
| `red` | `pause_strategy` (if 2+ criticals else `revalidate_backtest`) |
| `yellow` | `watch_only` or specific `disable_session`/`tighten_filter`/`review_parameters`/`reduce_size` |
| `green` | `keep_running` |
| `unknown` | `revalidate_backtest` (if all sources missing) or `keep_running` |

---

## 9. Suggestion Priority Order

When multiple suggestions fire, they are sorted by priority (lower = more urgent):

| Priority | Kind | Base Trigger |
|---|---|---|
| 1 | `pause_strategy` | `critical_count >= 2` |
| 2 | `revalidate_backtest` | `cost_drag >= critical AND hit_rate < baseline - 10` |
| 3 | `disable_session` | Any session in `disabled_sessions_candidates` |
| 4 | `tighten_filter` | `low_conf_drift_ratio < 0.65` |
| 5 | `reduce_size` | `drawdown >= warn OR cost_drag >= warn` |
| 6 | `review_parameters` | `regime_mix_shift >= 40%` |
| 7 | `watch_only` | Any warn, no critical |
| 8 | `keep_running` | All ok |

---

## 10. Config Defaults Summary

| Parameter | Default | Unit |
|---|---|---|
| `performance_window` | 20 | trades |
| `regime_window` | 30 | signals |
| `cost_window` | 50 | trades |
| `signal_window` | 50 | signals |
| `drawdown_window_days` | 90 | days |
| `fusion_history_ttl_minutes` | 90 | minutes |
| `backtest_report_ttl_minutes` | 240 | minutes |
| `candlestick_snapshot_ttl_minutes` | 90 | minutes |
| `cost_drag_warn_pct` | 10.0 | % |
| `cost_drag_critical_pct` | 30.0 | % |
| `drawdown_warn_pct` | 7.5 | % |
| `drawdown_critical_pct` | 15.0 | % |
| `min_session_sample` | 10 | signals |
| `session_hit_rate_disable_threshold` | 35.0 | % |
| `low_conf_drift_ratio_warn` | 0.65 | ratio |
| `regime_mix_shift_warn_pct` | 40.0 | % |
| `expected_baseline_hit_rate` | 55.0 | % |

---

## 11. Partial / Missing / Stale / Invalid Data Behavior (Summary)

| Scenario | Metric | Result |
|---|---|---|
| `backtest_report = None` | All backtest-based metrics | `unknown`, no crash |
| `trades = []` | Performance / Drawdown | `unknown` |
| `trades` key missing | Performance / Cost | Check `outcomes`; if missing → `unknown` |
| `fusion_signals = None or []` | Regime / Signal | `unknown` |
| Signal missing `bias_regime` | Regime | Count as `"unknown"` regime |
| Signal missing `session_label` | Regime | Count as `"other"` session |
| Signal missing `confidence` | Signal | Treat as `high_conf` bucket |
| Signal missing `pnl_pct` | All | Skip from win/loss, include in counts |
| `cost_pct` field absent | Cost | Infer from win/loss asymmetry |
| File mtime in future | Freshness | Treat as `critical` (clock skew) |
| File does not exist | Freshness | `missing` → `critical` |
| File zero-size | Freshness | Treat as `missing` |
| Window = 0 | Any metric | Clamp to `1`, or return `unknown` for freshness |
| Negative window | Any metric | Clamp to `1` |
| Non-numeric field | Any metric | Skip that record |
| All records garbage | Any metric | Return `unknown` with reason |
| `approval_file` missing | Approval store | Return `{}`, auto-create on first write |

---

## 12. Revision History

| Version | Date | Change |
|---|---|---|
| v1 | 2026-07-10 | Initial spec — aligned with implementation in `src/strategy_health/` |