"""strategies.py — signal generators + registry.

Manual-only backtest research module.
No broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class Signal:
    timestamp: str          # ISO timestamp of signal bar
    direction: int           # 1 = long, -1 = short, 0 = flat
    entry_price: float      # suggested entry (close of signal bar)
    stop_loss: float        # absolute price
    take_profit: float      # absolute price
    confidence: float       # 0-1
    session: str            # asian / london / new_york / overlap
    reason: str             # human-readable trigger description


@dataclass
class StrategyConfig:
    name: str
    mode: str               # "general" | "scalp"
    params: Dict[str, float] = field(default_factory=dict)
    description: str = ""


# ── Shared indicators ────────────────────────────────────────────────────────

def add_ma(df: pd.DataFrame, periods: List[int]) -> pd.DataFrame:
    for p in periods:
        df[f"ma_{p}"] = df["close"].rolling(p).mean()
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df[f"atr_{period}"] = tr.rolling(period).mean()
    return df


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    df[f"rsi_{period}"] = 100 - (100 / (1 + rs))
    return df


def detect_session(dt_index) -> pd.Series:
    """Return session label per UTC timestamp."""
    sessions = []
    for dt in dt_index:
        h = dt.hour + dt.minute / 60
        if 0 <= h < 7:
            sessions.append("asian")
        elif 7 <= h < 12:
            sessions.append("london")
        elif 12 <= h < 16:
            sessions.append("overlap")
        else:
            sessions.append("new_york")
    return pd.Series(sessions, index=dt_index)


# ── Strategy functions ───────────────────────────────────────────────────────

def baseline_ma(df: pd.DataFrame, fast: float = 20, slow: float = 50,
                 **kwargs) -> List[Signal]:
    """MA crossover: long when ma_fast crosses above ma_slow, short vice versa."""
    signals: List[Signal] = []
    df = add_ma(df.copy(), [int(fast), int(slow)])
    col_fast, col_slow = f"ma_{int(fast)}", f"ma_{int(slow)}"
    if col_fast not in df.columns or col_slow not in df.columns:
        return signals

    pos = 0  # current position: 0=flat, 1=long, -1=short
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row[col_fast]) or pd.isna(row[col_slow]):
            continue

        cross_up = (prev[col_fast] <= prev[col_slow]) and (row[col_fast] > row[col_slow])
        cross_dn = (prev[col_fast] >= prev[col_slow]) and (row[col_fast] < row[col_slow])

        sl_pct = kwargs.get("sl_pct", 0.005)
        tp_pct = kwargs.get("tp_pct", 0.010)

        if cross_up and pos != 1:
            sl = row["close"] * (1 - sl_pct)
            tp = row["close"] * (1 + tp_pct)
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp, confidence=0.7,
                session=row.get("session", "unknown"),
                reason=f"ma_fast={row[col_fast]:.2f} crossed above ma_slow={row[col_slow]:.2f}",
            ))
            pos = 1
        elif cross_dn and pos != -1:
            sl = row["close"] * (1 + sl_pct)
            tp = row["close"] * (1 - tp_pct)
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=-1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp, confidence=0.7,
                session=row.get("session", "unknown"),
                reason=f"ma_fast={row[col_fast]:.2f} crossed below ma_slow={row[col_slow]:.2f}",
            ))
            pos = -1
        else:
            pos = 0  # reset when flat

    return signals


def breakout(df: pd.DataFrame, lookback: float = 20, atr_mult: float = 1.5,
             **kwargs) -> List[Signal]:
    """Breakout: long when close breaks above highest high of lookback bars + ATR confirmation."""
    signals: List[Signal] = []
    df = add_atr(df.copy())
    df["hh"] = df["high"].rolling(int(lookback)).max().shift(1)
    df["ll"] = df["low"].rolling(int(lookback)).min().shift(1)
    atr_col = "atr_14"

    pos = 0
    for i in range(int(lookback) + 1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        if pd.isna(row.get(atr_col)) or pd.isna(row["hh"]):
            continue

        atr = row[atr_col]
        # Long: close above 20-bar high, with ATR confirmation
        if row["close"] > row["hh"] and (row["close"] - prev["close"]) > atr_mult * atr:
            if pos != 1:
                sl = row["close"] - atr_mult * atr
                tp = row["close"] + 2 * atr_mult * atr
                signals.append(Signal(
                    timestamp=str(df.index[i]),
                    direction=1, entry_price=row["close"], stop_loss=sl,
                    take_profit=tp, confidence=0.65,
                    session=row.get("session", "unknown"),
                    reason=f"breakout above {lookback}-bar high {row['hh']:.2f}",
                ))
                pos = 1
            else:
                pos = 0  # flatten on signal
        # Short: close below 20-bar low
        elif row["close"] < row["ll"] and (prev["close"] - row["close"]) > atr_mult * atr:
            if pos != -1:
                sl = row["close"] + atr_mult * atr
                tp = row["close"] - 2 * atr_mult * atr
                signals.append(Signal(
                    timestamp=str(df.index[i]),
                    direction=-1, entry_price=row["close"], stop_loss=sl,
                    take_profit=tp, confidence=0.65,
                    session=row.get("session", "unknown"),
                    reason=f"breakout below {lookback}-bar low {row['ll']:.2f}",
                ))
                pos = -1
            else:
                pos = 0
        else:
            pos = 0

    return signals


def xauusd_scalp_reversion(df: pd.DataFrame,
                            rsi_period: float = 8,
                            ma_fast: float = 50,
                            ma_slow: float = 200,
                            rsi_oversold: float = 35,
                            rsi_overbought: float = 65,
                            **kwargs) -> List[Signal]:
    """XAUUSD scalp mean-reversion: fade extended moves when price is far from MA200."""
    signals: List[Signal] = []
    df = add_ma(df.copy(), [int(ma_fast), int(ma_slow)])
    df = add_rsi(df.copy(), int(rsi_period))
    spread_pips = kwargs.get("spread_points", 30)

    for i in range(1, len(df)):
        row = df.iloc[i]
        ma200 = row.get(f"ma_{int(ma_slow)}")
        rsi = row.get(f"rsi_{int(rsi_period)}")
        if pd.isna(ma200) or pd.isna(rsi):
            continue

        dist_pct = (row["close"] - ma200) / ma200 * 100
        spread_cost = spread_pips * 0.10  # points

        # Long: RSI oversold, price >> below MA200 (extended bearish)
        if rsi < rsi_oversold and dist_pct < -0.5:
            sl = row["close"] - spread_cost * 1.5
            tp = row["close"] + spread_cost * 2.0
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp,
                confidence=(rsi_oversold - rsi) / rsi_oversold * 0.8,
                session=row.get("session", "unknown"),
                reason=f"rsi={rsi:.1f} < {rsi_oversold}, dist={dist_pct:.2f}% from ma200",
            ))
        # Short: RSI overbought, price >> above MA200 (extended bullish)
        elif rsi > rsi_overbought and dist_pct > 0.5:
            sl = row["close"] + spread_cost * 1.5
            tp = row["close"] - spread_cost * 2.0
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=-1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp,
                confidence=(rsi - rsi_overbought) / (100 - rsi_overbought) * 0.8,
                session=row.get("session", "unknown"),
                reason=f"rsi={rsi:.1f} > {rsi_overbought}, dist={dist_pct:.2f}% from ma200",
            ))

    return signals


def xauusd_scalp_momentum(df: pd.DataFrame,
                           atr_period: float = 14,
                           atr_mult: float = 0.5,
                           rsi_period: float = 5,
                           rsi_threshold: float = 60,
                           **kwargs) -> List[Signal]:
    """XAUUSD scalp momentum: fade momentum stalls at NY open with tight ATR stops."""
    signals: List[Signal] = []
    df = add_atr(df.copy(), int(atr_period))
    df = add_rsi(df.copy(), int(rsi_period))
    spread_pips = kwargs.get("spread_points", 30)

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        atr = row.get("atr_14")
        rsi = row.get("rsi_5")
        if pd.isna(atr) or pd.isna(rsi):
            continue

        spread_cost = spread_pips * 0.10
        tick_size = 0.10

        # Long: RSI crosses above threshold with positive momentum
        if (prev.get("rsi_5", 0) < rsi_threshold) and (rsi > rsi_threshold):
            sl = row["close"] - atr_mult * atr
            tp = row["close"] + 1.5 * atr_mult * atr
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp,
                confidence=min(rsi / 100, 0.85),
                session=row.get("session", "unknown"),
                reason=f"rsi_5 momentum cross {prev.get('rsi_5',0):.1f} -> {rsi:.1f}",
            ))
        # Short: RSI crosses below (100 - threshold)
        elif (prev.get("rsi_5", 100) > (100 - rsi_threshold)) and (rsi < (100 - rsi_threshold)):
            sl = row["close"] + atr_mult * atr
            tp = row["close"] - 1.5 * atr_mult * atr
            signals.append(Signal(
                timestamp=str(df.index[i]),
                direction=-1, entry_price=row["close"], stop_loss=sl,
                take_profit=tp,
                confidence=min((100 - rsi) / 100, 0.85),
                session=row.get("session", "unknown"),
                reason=f"rsi_5 momentum cross down {prev.get('rsi_5',0):.1f} -> {rsi:.1f}",
            ))

    return signals


# ── Strategy registry ────────────────────────────────────────────────────────

STRATEGIES: Dict[str, Callable] = {
    "baseline_ma": baseline_ma,
    "breakout": breakout,
    "xauusd_scalp_reversion": xauusd_scalp_reversion,
    "xauusd_scalp_momentum": xauusd_scalp_momentum,
}

STRATEGY_MODES: Dict[str, str] = {
    "baseline_ma": "general",
    "breakout": "general",
    "xauusd_scalp_reversion": "scalp",
    "xauusd_scalp_momentum": "scalp",
}

STRATEGY_DEFAULTS: Dict[str, Dict[str, float]] = {
    "baseline_ma": {"fast": 20, "slow": 50, "sl_pct": 0.005, "tp_pct": 0.010},
    "breakout": {"lookback": 20, "atr_mult": 1.5},
    "xauusd_scalp_reversion": {
        "rsi_period": 8, "ma_fast": 50, "ma_slow": 200,
        "rsi_oversold": 35, "rsi_overbought": 65,
    },
    "xauusd_scalp_momentum": {
        "atr_period": 14, "atr_mult": 0.5,
        "rsi_period": 5, "rsi_threshold": 60,
    },
}


def get_strategy(name: str) -> Callable:
    if name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{name}'. Available: {list(STRATEGIES.keys())}"
        )
    return STRATEGIES[name]


def apply_strategy(df: pd.DataFrame, name: str, params: Optional[Dict] = None) -> List[Signal]:
    """Apply named strategy to dataframe, returning list of Signal objects."""
    fn = get_strategy(name)
    defaults = STRATEGY_DEFAULTS.get(name, {})
    overrides = params or {}
    merged = {**defaults, **overrides}
    return fn(df, **merged)