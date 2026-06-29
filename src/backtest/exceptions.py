"""Backtest-specific exception types.

V5 設計原則：缺資料 / 缺 history / 缺 horizon 不 throw，
而係把 outcome_reason / verdict 寫入 report，CLI exit 仍 0。
但 pure-load failure（history dir 唔存在 / schema 不對）就要 raise。

所有 raise 都帶「why」訊息，方便 CLI 唔破壞現有 V1-V4 行為。
"""
from __future__ import annotations


class BacktestError(Exception):
    """Base class for V5 backtest 層所有 errors. 唔會流入 V1-V4."""


class PriceSourceError(BacktestError):
    """Price source 載入失敗 (file missing / schema invalid)."""


class HistorySourceError(BacktestError):
    """Signal source 載入失敗 (fusion/candle history not parseable)."""


class InconsistentDataError(BacktestError):
    """Schema 不一致: 同個 signal 出現 conflict_fileds 唔 match 等."""
