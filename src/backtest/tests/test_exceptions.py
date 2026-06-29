"""Exception hierarchy tests for backtest layer."""
import pytest

from backtest.exceptions import (
    BacktestError,
    PriceSourceError,
    HistorySourceError,
    InconsistentDataError,
)


def test_price_source_error_is_backtest_error():
    err = PriceSourceError("file missing")
    assert isinstance(err, BacktestError)
    assert str(err) == "file missing"


def test_history_source_error_is_backtest_error():
    err = HistorySourceError("parse fail")
    assert isinstance(err, BacktestError)


def test_inconsistent_data_error_is_backtest_error():
    err = InconsistentDataError("schema mismatch")
    assert isinstance(err, BacktestError)


def test_all_errors_distinct_subclasses():
    # Ensure distinct types so callers can handle each path
    e1 = PriceSourceError("a")
    e2 = HistorySourceError("a")
    assert type(e1) is not type(e2)
    assert isinstance(e1, BacktestError) and isinstance(e2, BacktestError)
