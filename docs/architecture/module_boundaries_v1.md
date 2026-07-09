# Module Boundaries v1 (companion to xauusd_system_architecture_v1.md)

> Status: **v1 (snapshot 2026-07-08)** — companion file.
> One-line-per-module contract; see main file for full context.

---

## Boundary Contract Summary

| Module | Reads | Writes | External Calls | Manual-only |
|---|---|---|---|---|
| `briefing` (`src/daily_xauusd_brief/main.py`) | Twelve Data, NewsAPI | `data/history/*`, `reports/gold/*` | optional Telegram | live (cron) |
| `refresh` (`generate_xauusd_refresh.py`) | Twelve Data, NewsAPI | `data/xauusd_refresh/*/` | none | live (cron) |
| `candlestick` (`run_candle_engine.py` + `src/candlestick/`) | yfinance, optional CSV | `data/candle_engine/<tf>/` | none | Manual-only |
| `fusion` (`run_fusion_engine.py` + `src/fusion/`) | `data/xauusd_refresh/`, `data/candle_engine/`, optional `data/history/` | `data/fusion/` | none | Manual-only |
| `dashboard` (`daily_xauusd_brief/dashboard.py`) | all `data/` | none (browser only) | none | live |
| `backtest` (`run_backtest.py` + `src/backtest/`) | optional CSV, yfinance | `data/backtests/` | none | Manual-only |
| `review` (`log_engine_review.py`) | engine snapshot | `data/engine_reviews.csv` | none | Manual-only |
| `ops` (`engine_ops_logger.py`) | n/a (logs own events) | `data/engine_ops_events.jsonl` | none | live |


---

## Cross-Module Data Flow Rules

1. **Briefing -> Candlestick / Fusion**: read-only via `data/history/`. Candlestick and Fusion treat briefing JSON as opaque input.
2. **Refresh -> Fusion**: read-only via `data/xauusd_refresh/`. Fusion treats refresh JSON as opaque input (same schema as briefing for the fields it cares about).
3. **Candlestick -> Fusion**: read-only via `data/candle_engine/<tf>/`.
4. **All `data/` -> Dashboard**: read-only. Dashboard NEVER writes anywhere except session state.
5. **Backtest <-> Historical**: Backtest can READ historical `data/history/`, `data/candle_engine/` to construct backtest inputs but does NOT mutate those. Output is only into `data/backtests/`.
6. **Review** log only writes its own CSV. Reads OPS events for context (optional).


---

## Hard-Coded Boundaries (enforced by review)

- Any module that imports `pandas-ta` / `ta.momentum` cannot be wired into dashboard (only display-only).
- Any module that imports `telegram_sender` or `requests.post(<broker>)` cannot be sold as 'manual-only'.
- `dashboard*` should `import` nothing that has side-effects outside `data/` reading.

---

End of v1.
