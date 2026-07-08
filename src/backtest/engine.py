"""engine.py — backtest execution engine with cost modeling.

Runs signals through a bar-history simulation.
Manual-only; no broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .strategies import Signal


@dataclass
class TradeRecord:
    trade_id: int
    entry_time: str
    exit_time: str
    direction: int           # 1=long, -1=short
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl_gross: float          # before costs
    pnl_net: float            # after costs
    pnl_r: float              # R-multiple (net / risk)
    session: str
    spread_cost: float        # points paid
    slippage_cost: float      # points paid
    total_cost: float         # spread + slippage
    mae: float                # maximum adverse excursion (points, abs)
    mfe: float                # maximum favorable excursion (points, abs)
    holding_bars: int         # number of bars held
    exit_reason: str          # sl / tp / signal_reverse / end_of_data
    confidence: float         # signal confidence at entry


@dataclass
class BacktestConfig:
    spread_points: float = 30.0    # XAUUSD spread in points (0.10 = 1 pip)
    slippage_points: float = 10.0  # simulated slippage in points
    risk_per_trade: float = 1.0     # R-multiple denominator (not used directly)
    hedge_mode: bool = False       # currently flat only


class BacktestEngine:
    """Simulate trades from a list of Signals against OHLCV bar history."""

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()
        self.trades: List[TradeRecord] = []
        self._trade_counter = 0

    def run(self, df: pd.DataFrame, signals: List[Signal],
            session_filter: Optional[str] = None) -> List[TradeRecord]:
        """Run backtest: process signals against df, return list of TradeRecords."""
        self.trades = []
        self._trade_counter = 0
        active_trade: List[Optional[dict]] = [None]  # mutable ref

        if df.empty or not signals:
            return self.trades

        # Build timestamp → bar index map for fast lookup
        ts_to_idx: Dict[str, int] = {}
        for i, ts in enumerate(df.index):
            ts_to_idx[str(ts)] = i

        for sig in sorted(signals, key=lambda s: s.timestamp):
            # Session filter
            if session_filter and sig.session != session_filter:
                continue

            if sig.timestamp not in ts_to_idx:
                continue

            sig_bar_idx = ts_to_idx[sig.timestamp]

            if active_trade[0] is not None:
                # Scan from open bar+1 up to signal bar for SL/TP
                self._scan_active_trade(
                    df, ts_to_idx, active_trade,
                    open_bar_idx=active_trade[0]["open_bar_idx"],
                    up_to_bar_idx=sig_bar_idx,
                )
                # If trade was closed mid-scan, active_trade[0] is already None

            if active_trade[0] is None:
                # Open new position from signal
                if sig.direction == 1:
                    entry_price = sig.entry_price
                else:
                    entry_price = sig.entry_price - self._spread_cost()

                self._trade_counter += 1
                active_trade[0] = {
                    "id": self._trade_counter,
                    "direction": sig.direction,
                    "entry_time": sig.timestamp,
                    "entry_price": entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                    "session": sig.session,
                    "confidence": sig.confidence,
                    "open_bar_idx": sig_bar_idx,
                    "bars_held": 0,
                    "high_price": entry_price,
                    "low_price": entry_price,
                    "mae": 0.0,
                    "mfe": 0.0,
                    "exit_reason": "",
                }

        # Close open position at last bar
        if active_trade[0] is not None:
            last_bar = df.iloc[-1]
            self._close_trade(active_trade, str(df.index[-1]),
                              last_bar["close"], "end_of_data")

        return self.trades

    def _scan_active_trade(self, df: pd.DataFrame, ts_to_idx: Dict[str, int],
                           active_trade: List[Optional[dict]],
                           open_bar_idx: int, up_to_bar_idx: int):
        """Check SL/TP from open_bar_idx+1 to up_to_bar_idx (inclusive)."""
        trade = active_trade[0]
        if trade is None:
            return
        for bar_i in range(open_bar_idx + 1, up_to_bar_idx + 1):
            bar = df.iloc[bar_i]
            price = bar["close"]
            direction = trade["direction"]
            trade["bars_held"] += 1

            # Update high/low
            if direction == 1:
                pnl = price - trade["entry_price"]
                trade["high_price"] = max(trade["high_price"], price)
                trade["low_price"] = min(trade["low_price"], price)
            else:
                pnl = trade["entry_price"] - price
                trade["high_price"] = max(trade["high_price"], price)
                trade["low_price"] = min(trade["low_price"], price)

            # MAE / MFE
            loss = min(0, pnl)
            trade["mae"] = min(trade["mae"], loss)
            trade["mfe"] = max(trade["mfe"], pnl)

            # Check SL / TP
            hit = False
            exit_price = price
            reason = ""
            if direction == 1:
                if price <= trade["stop_loss"]:
                    exit_price, reason = trade["stop_loss"], "sl"
                    hit = True
                elif price >= trade["take_profit"]:
                    exit_price, reason = trade["take_profit"], "tp"
                    hit = True
            else:
                if price >= trade["stop_loss"]:
                    exit_price, reason = trade["stop_loss"], "sl"
                    hit = True
                elif price <= trade["take_profit"]:
                    exit_price, reason = trade["take_profit"], "tp"
                    hit = True

            if hit:
                self._close_trade(active_trade, str(df.index[bar_i]),
                                   exit_price, reason)
                return

    def _close_trade(self, active_trade: List[Optional[dict]],
                     exit_time: str, exit_price: float, reason: str):
        trade = active_trade[0]
        if trade is None:
            return

        direction = trade["direction"]
        slip = self._slippage_cost()
        if direction == 1:
            actual_exit = exit_price - slip
        else:
            actual_exit = exit_price + slip

        gross_pnl = (actual_exit - trade["entry_price"]) * direction
        total_cost = self._spread_cost() + slip
        net_pnl = gross_pnl - total_cost

        risk = abs(trade["entry_price"] - trade["stop_loss"])
        pnl_r = (net_pnl / risk) if risk > 0 else 0.0

        rec = TradeRecord(
            trade_id=trade["id"],
            entry_time=trade["entry_time"],
            exit_time=exit_time,
            direction=trade["direction"],
            entry_price=trade["entry_price"],
            exit_price=actual_exit,
            stop_loss=trade["stop_loss"],
            take_profit=trade["take_profit"],
            pnl_gross=gross_pnl,
            pnl_net=net_pnl,
            pnl_r=pnl_r,
            session=trade["session"],
            spread_cost=self._spread_cost(),
            slippage_cost=slip,
            total_cost=total_cost,
            mae=abs(trade["mae"]) if trade["mae"] != 0 else 0.0,
            mfe=trade["mfe"],
            holding_bars=trade["bars_held"],
            exit_reason=reason,
            confidence=trade["confidence"],
        )
        self.trades.append(rec)
        active_trade[0] = None  # clear

    def _spread_cost(self) -> float:
        return self.config.spread_points * 0.10

    def _slippage_cost(self) -> float:
        return self.config.slippage_points * 0.10