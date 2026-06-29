"""Backtest / Validation Layer — public surface (V5).

純 read-only on V1-V4 histories. CLI surface 喺 `cli.py` (Step 6).

Public API (available after Step 2):
    exceptions     — error hierarchy
    models         — outcome / replay / calibration dataclasses
    price_source   — load PricingSeries from candlestick history
    replay         — load + sort + walk-forward signals
    evaluate       — compute Outcome rows
"""
from .exceptions import (
    BacktestError,
    PriceSourceError,
    HistorySourceError,
    InconsistentDataError,
)
from .models import (
    ReplaySpec,
    Outcome,
    CalibrationBucket,
    CalibrationReport,
    BacktestRunSummary,
    PricingSeries,
    DECISION_LONG,
    DECISION_SHORT,
    DECISION_NONE,
    VALID_DECISIONS,
    OUTCOME_OK,
    OUTCOME_OUT_OF_WINDOW,
    OUTCOME_DECISION_NONE,
    OUTCOME_MISSING_FIELDS,
    VERDICT_OK,
    VERDICT_INSUFFICIENT,
)
from .price_source import load_pricing_series_from_candles
from .replay import (
    load_signals,
    walk_forward,
)
from .evaluate import (
    evaluate_outcomes,
)

__all__ = [
    "exceptions",
    "models",
    "price_source",
    "replay",
    "evaluate",
    # (filled in later steps)
    # "calibration", "breakdown", "report", "cli",
]