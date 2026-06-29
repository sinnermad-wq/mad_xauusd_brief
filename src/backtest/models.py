"""Backtest dataclasses: Outcome / ReplaySpec / CalibrationBucket / Reports.

設計要點：

* 全 frozen dataclass + tuples → 確保 replay deterministic、可重跑、可比較
* Outcome 包 per-signal × per-horizon 嘅一個 row；多個 horizons 自然展開成多行
* CalibrationBucket 對 fusion_confidence 嘅 binned view；ECE 等 metrics 加埋另寫
* Report wrappers 純 structural；不寫文字／排版

穩定性保證：
* 改動呢個 file 唔會 break V1-V4 因為 純新 module（無 shared import）
* Frozen → test 直接斷言 equality
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Tuple, Optional, Dict, Any


# ─── Decision constants (kept as plain strings，兼容 FusionOutput.decision) ──
DECISION_LONG = "long"
DECISION_SHORT = "short"
DECISION_NONE = "none"
VALID_DECISIONS = (DECISION_LONG, DECISION_SHORT, DECISION_NONE)


# ─── Outcome reason (per-row 標記為何無 outcome 或 outcome 點樣生成) ─────
OUTCOME_OK = "ok"
OUTCOME_OUT_OF_WINDOW = "out_of_window"             # future bar 缺
OUTCOME_DECISION_NONE = "decision_none_skip_trade"   # decision=none → 計入 distribution，跳 trade gain
OUTCOME_MISSING_FIELDS = "missing_required_fields"  # signal schema 唔夠料 → skip


# ─── Verdict 常數（用嚟寫 summary.md 頭） ───────────────────────────────
VERDICT_OK = "OK"
VERDICT_INSUFFICIENT = "INSUFFICIENT_DATA"


# ─────────────────────────────────────────────────────────────────────────
# ReplaySpec — replay 嘅 input contract
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ReplaySpec:
    """Single source of truth for replay parameters.

    All fields frozen. Made-from-CLI struct.
    Horizons default per spec = (1, 3, 5) but overridable.
    """
    horizons: Tuple[int, ...] = (1, 3, 5)
    from_date: Optional[str] = None                 # inclusive YYYY-MM-DD
    to_date:   Optional[str] = None                 # inclusive YYYY-MM-DD
    limit:     Optional[int] = None                 # latest N signals
    sources:   Tuple[str, ...] = ("fusion",)        # which history dirs to load
    require_trade_candidate: Optional[bool] = None  # None=both; True/False filter
    include_none_in_trades: bool = False            # 把 decision=none 計入 trade rows

    def __post_init__(self) -> None:
        if not self.horizons:
            raise ValueError("ReplaySpec.horizons cannot be empty")
        if any(h <= 0 for h in self.horizons):
            raise ValueError(f"horizons must be > 0, got {self.horizons}")
        if not self.sources:
            raise ValueError("ReplaySpec.sources cannot be empty")


# ─────────────────────────────────────────────────────────────────────────
# Outcome — 一行 outcome (signal × horizon)
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Outcome:
    """One row: (signal, horizon) → outcome.

    decision_correct = signed_return > 0 only when decision in {long, short}.
    direction_correct 用嚟 hit-test fusion_confidence calibration.
    """
    signal_id:        str
    signal_ts:        str                            # ISO UTC
    decision:         str                            # long/short/none
    horizon_bars:     int

    entry_price:      Optional[float]                # None if out_of_window
    exit_price:       Optional[float]

    raw_return:       Optional[float]                # (exit-entry)/entry
    signed_return:    Optional[float]                # direction-aware gain
    direction_correct: Optional[bool]                # True if trade would win
    move_abs:         Optional[float]                # |raw_return|

    confidence:       float                          # 0.0–1.0 (fusion_confidence)
    trade_candidate:  bool
    consensus_label:  str
    conflict_label:   str
    regime_tag:       Optional[str]                  # if available from engine
    timeframe:        str                            # "1D" default
    data_quality:     Optional[str]                  # from briefing / candle

    outcome_reason:   str = OUTCOME_OK

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────
# CalibrationBucket — confidence bucket summary
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CalibrationBucket:
    """Stats for one confidence bucket, e.g. [0.5, 0.6)."""
    lo: float
    hi: float
    n: int
    n_long: int
    n_short: int
    n_none: int
    hit_rate: float              # direction_correct rate (none 唔納入)
    avg_signed_return: float     # mean signed_return
    avg_raw_return: float        # mean raw_return
    avg_confidence: float        # 平均 (bucket) 嘅 confidence

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────
# CalibrationReport — 包含 ECE / Brier / buckets / verdicts
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class CalibrationReport:
    buckets:        Tuple[CalibrationBucket, ...]
    ece:            float                           # Expected Calibration Error
    brier:          float                           # mean((conf - hit)^2)
    n_total:        int
    n_long:         int
    n_short:        int
    n_none:         int
    by_trade_candidate_hit_rate: Dict[bool, float]  # True/False → hit_rate
    by_consensus_hit_rate: Dict[str, float]
    by_conflict_hit_rate: Dict[str, float]
    verdict:        str                             # OK / INSUFFICIENT_DATA

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["buckets"] = [b.to_dict() for b in self.buckets]
        return d


# ─────────────────────────────────────────────────────────────────────────
# BacktestRunSummary — top-level summary
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class BacktestRunSummary:
    spec:               ReplaySpec
    n_signals_loaded:   int
    n_outcomes:         int
    skipped:            Tuple[Tuple[str, int], ...]  # [(reason, count), ...]
    horizon_stats:      Dict[int, Dict[str, float]]  # horizon → {hit_rate, avg_return, n}
    calibration:        CalibrationReport
    verdict:            str                          # OK / INSUFFICIENT_DATA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec":              asdict(self.spec),
            "n_signals_loaded":  self.n_signals_loaded,
            "n_outcomes":        self.n_outcomes,
            "skipped":           [list(t) for t in self.skipped],
            "horizon_stats":     self.horizon_stats,
            "calibration":       self.calibration.to_dict(),
            "verdict":           self.verdict,
        }


# ─────────────────────────────────────────────────────────────────────────
# PricingSeries — price source 嘅 internal struct
# ─────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PricingSeries:
    """Time-indexed price series 以元組 stored，sortable，pure no-side-effect。

    index_desc = True 代表 descending (newest first)，
    False = ascending (oldest first). Asc 是 replay 預設.
    """
    timestamps: Tuple[str, ...]        # ISO strings, sorted ascending
    closes:     Tuple[float, ...]
    index_desc: bool = False

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.closes):
            raise ValueError("PricingSeries: timestamps/closes length mismatch")
        if len(self.timestamps) > 1:
            ts_sorted = sorted(self.timestamps)
            if list(self.timestamps) != ts_sorted:
                raise ValueError("PricingSeries: must be sorted ascending")

    def __len__(self) -> int:
        return len(self.closes)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamps": list(self.timestamps),
            "closes":     list(self.closes),
            "index_desc": self.index_desc,
        }
