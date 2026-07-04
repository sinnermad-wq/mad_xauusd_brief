"""
Market Data — Phase 1 Polling Adapter (Twelve Data).

Architecture
────────────
  MarketDataPort (abstract interface)
      └── PollingMarketDataAdapter  ← E5 implementation
              - Twelve Data REST /price + /time_series
              - Quota-aware: tracks daily credits, respects 8-req/min limit
              - Exponential backoff on HTTP 429
              - In-memory CandleStore (latest N candles per timeframe)

  Future: WebSocketMarketDataAdapter ← Phase 2

Responsibilities (E5 scope)
───────────────────────────
✅  Fetch real-time price for XAU/USD
✅  Fetch OHLC bars for 1m / 5m / 15m / 1h / 4h / 1D
✅  Output unified CandleDTO
✅  Quota guard (500 credits/day, 90% budget = 450 safe budget)
✅  Per-request rate limiting (sequential, 1.5s gap)
✅  Exponential backoff on 429 (2s base, 64s max)
✅  In-memory cache (last N candles per timeframe)
✅  Graceful degradation (quota exhausted → raise QuotaExceeded, don't crash)

E5 exclusions
─────────────
❌  Dashboard polling loop (Phase 2 responsibility)
❌  Signal overlay
❌  Fusion logic changes
❌  WebSocket (Phase 2)
❌  Persistent storage (data is in-memory only)
"""

from __future__ import annotations

import math
import os
import time
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import requests

# ── Load .env once at module level (safe to call multiple times) ─────────────
from dotenv import load_dotenv
load_dotenv()

# ── Key lookup (config.py already does this, but we need it here directly) ────
def _get_api_key() -> str:
    key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not key:
        # Try to load from project .env file
        from pathlib import Path
        for line in (Path(__file__).parent.parent.parent / ".env").read_text().splitlines():
            if line.startswith("TWELVE_DATA_API_KEY"):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        raise ValueError("TWELVE_DATA_API_KEY is not set in .env")
    return key


# ── Exceptions ────────────────────────────────────────────────────────────────


class MarketDataError(Exception):
    """Base exception for all market data errors."""
    pass


class QuotaExceeded(MarketDataError):
    """Raised when Twelve Data daily or per-minute quota is exhausted."""
    def __init__(self, daily_remaining: int, minute_remaining: int):
        self.daily_remaining = daily_remaining
        self.minute_remaining = minute_remaining
        super().__init__(
            f"Quota exhausted: {daily_remaining} daily credits remaining, "
            f"{minute_remaining} requests/min remaining"
        )


class RateLimitHit(MarketDataError):
    """Raised when per-minute request count limit is hit (HTTP 429)."""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limit hit, retry after {retry_after:.1f}s")


class MarketDataHTTPError(MarketDataError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"[{status}] {message}")


# ── Candle DTO ────────────────────────────────────────────────────────────────


@dataclass
class Candle:
    """Unified OHLC candle DTO — adapter-agnostic, used by all consumers."""
    symbol:    str
    timeframe: str          # e.g. "1min", "5min", "1h", "4h", "1day"
    datetime:  datetime     # UTC, bar open time
    open:      float
    high:      float
    low:       float
    close:     float
    # Metadata
    source:    str = "twelvedata"  # or "mock", "websocket"

    def __post_init__(self):
        assert self.high >= self.close,  f"H={self.high} < C={self.close}"
        assert self.high >= self.open,   f"H={self.high} < O={self.open}"
        assert self.low  <= self.close,  f"L={self.low}  > C={self.close}"
        assert self.low  <= self.open,   f"L={self.low}  > O={self.open}"

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    @classmethod
    def from_twelvedata_bar(cls, symbol: str, timeframe: str,
                              bar: dict, source: str = "twelvedata") -> Candle:
        """Parse a Twelve Data /time_series bar dict → CandleDTO.

        Twelve Data datetime format:
          • Intraday (1min/5min/15min/1h/4h):  "2026-06-29 23:02:00"
          • Daily (1day):                        "2026-05-02"  (no time component)
        """
        dt_str = bar.get("datetime", "")
        if " " in dt_str:                          # has time component
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        else:                                      # daily bar — date only
            dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return cls(
            symbol=symbol,
            timeframe=timeframe,
            datetime=dt,
            open= float(bar["open"]),
            high= float(bar["high"]),
            low=  float(bar["low"]),
            close=float(bar["close"]),
            source=source,
        )

    @classmethod
    def from_price(cls, symbol: str, price: float) -> Candle:
        """Build a single-point 'price' candle (no OHLC range)."""
        return cls(
            symbol=symbol,
            timeframe="price",
            datetime=datetime.now(timezone.utc),
            open=price, high=price, low=price, close=price,
            source="twelvedata",
        )


# ── Quota Tracker ─────────────────────────────────────────────────────────────


@dataclass
class QuotaTracker:
    """Tracks Twelve Data quota state with safe-budget guard.

    Twelve Data free tier:
      • 500 credits / day
      • 8 API requests / minute (shared across all endpoints)
      • Credit cost varies by endpoint: /price=1, /time_series=3-5 per call

    Safety budget: 90% of daily limit (450 credits).
    Once budget exhausted, all methods raise QuotaExceeded.
    """
    daily_limit:      int = 500
    safe_budget_pct: float = 0.90
    min_gap_seconds: float = 1.5    # sequential request gap

    daily_used:     float = 0.0
    minute_used:    int   = 0
    minute_window_start: float = 0.0   # unix timestamp
    last_request_time: float = 0.0    # unix timestamp

    # Per-call credit cost estimates (conservative; actual varies by interval)
    CREDIT_COST: dict[str, float] = field(default_factory=lambda: {
        "price":   1.0,
        "1min":   3.0,
        "5min":   3.0,
        "15min":  3.0,
        "1h":     5.0,
        "4h":     5.0,
        "1day":   5.0,
    })

    @property
    def daily_budget(self) -> float:
        return self.daily_limit * self.safe_budget_pct

    @property
    def daily_remaining(self) -> float:
        return max(0.0, self.daily_budget - self.daily_used)

    @property
    def minute_remaining(self) -> int:
        return max(0, 8 - self.minute_used)

    def _reset_minute_if_needed(self) -> None:
        now = time.monotonic()
        if now - self.minute_window_start >= 60.0:
            self.minute_used = 0
            self.minute_window_start = now

    def reserve(self, endpoint_or_timeframe: str) -> float:
        """Reserve credits for one API call. Returns credit cost.

        Raises QuotaExceeded if daily budget or per-minute limit hit.
        """
        self._reset_minute_if_needed()

        if self.minute_used >= 8:
            raise QuotaExceeded(
                daily_remaining=int(self.daily_remaining),
                minute_remaining=0,
            )

        cost = self.CREDIT_COST.get(endpoint_or_timeframe, 3.0)
        if self.daily_used + cost > self.daily_budget:
            raise QuotaExceeded(
                daily_remaining=int(self.daily_remaining),
                minute_remaining=self.minute_remaining,
            )

        # Enforce minimum gap between sequential requests
        now = time.monotonic()
        gap = now - self.last_request_time
        if gap < self.min_gap_seconds:
            time.sleep(self.min_gap_seconds - gap)

        self.daily_used   += cost
        self.minute_used  += 1
        self.last_request_time = time.monotonic()
        return cost

    def note_429(self, retry_after: float) -> float:
        """Called when a 429 is received. Returns the retry_after value to use."""
        # Decrement minute_used since the request didn't actually consume a credit
        # but we still waited; backoff handles the delay
        return retry_after

    def reset_daily(self) -> None:
        """Reset daily counter. Called once per day (e.g., at midnight or startup)."""
        self.daily_used = 0.0


# ── HTTP Client ───────────────────────────────────────────────────────────────


class _TwelveDataClient:
    BASE_URL = "https://api.twelvedata.com"

    def __init__(self, api_key: str, quota: QuotaTracker):
        self.api_key = api_key
        self.quota   = quota

    def _request(self, endpoint: str, params: dict,
                 max_retries: int = 3) -> dict:
        """Make an HTTP GET to Twelve Data with quota + backoff handling."""
        url = f"{self.BASE_URL}/{endpoint}"
        params["apikey"] = self.api_key

        last_exc: Exception = MarketDataError("unknown")

        for attempt in range(max_retries):
            try:
                self.quota.reserve(endpoint)   # raises if quota exhausted

                r = requests.get(url, params=params, timeout=15)
                status = r.status_code

                if status == 200:
                    return r.json()

                if status == 429:
                    # Parse retry-after from response
                    retry_after = 2.0
                    try:
                        msg = r.json().get("message", "")
                        # "Wait for n seconds" or use Retry-After header
                        ra = r.headers.get("Retry-After")
                        if ra:
                            retry_after = float(ra)
                        elif "second" in msg.lower():
                            import re
                            m = re.search(r"(\d+)", msg)
                            if m:
                                retry_after = float(m.group(1))
                    except Exception:
                        pass

                    # Exponential backoff: 2s * 2^attempt, max 64s
                    backoff = min(64.0, 2.0 * (2 ** attempt) + random.uniform(0, 1))
                    time.sleep(backoff)
                    self.quota.note_429(retry_after)
                    continue   # retry

                if status == 400:
                    msg = r.json().get("message", r.text)
                    raise MarketDataHTTPError(400, msg)

                # 500 / 503 — retry with backoff
                time.sleep(1.5 * (attempt + 1))
                last_exc = MarketDataHTTPError(status, r.text[:200])

            except requests.RequestException as e:
                last_exc = e
                time.sleep(2 ** attempt)

        raise last_exc   # type: ignore

    # ── Public endpoints ──────────────────────────────────────────────────────

    def get_price(self, symbol: str = "XAU/USD") -> float:
        data = self._request("price", {"symbol": symbol})
        return float(data["price"])

    def get_time_series(self, symbol: str, timeframe: str,
                        outputsize: int = 60) -> list[dict]:
        # Normalise short dashboard format (1m/5m/15m/1D) to Twelve Data format
        _interval_map = {
            "1m": "1min", "5m": "5min", "15m": "15min",
            "1D": "1day", "2h": "2h", "8min": "8min",
        }
        interval = _interval_map.get(timeframe, timeframe)
        data = self._request(
            "time_series",
            {"symbol": symbol, "interval": interval, "outputsize": outputsize},
        )
        return data.get("values", [])


# ── In-Memory Candle Store ─────────────────────────────────────────────────────


@dataclass
class CandleStore:
    """In-memory ring buffer of the latest N candles per timeframe.

    Thread-unsafe by design (single-threaded Streamlit context).
    """
    MAX_CANDLES: int = 200   # per timeframe

    _candles: dict[str, list[Candle]] = field(default_factory=dict)

    def store(self, candles: list[Candle]) -> None:
        for c in candles:
            tf = c.timeframe
            if tf not in self._candles:
                self._candles[tf] = []
            self._candles[tf].append(c)
            # Keep ring buffer bounded
            if len(self._candles[tf]) > self.MAX_CANDLES:
                self._candles[tf] = self._candles[tf][-self.MAX_CANDLES:]

    def get_latest(self, timeframe: str) -> Optional[Candle]:
        bars = self._candles.get(timeframe, [])
        return bars[-1] if bars else None

    def get_bars(self, timeframe: str, limit: int = 60) -> list[Candle]:
        bars = self._candles.get(timeframe, [])
        return bars[-limit:]

    def all_timeframes(self) -> list[str]:
        return sorted(self._candles.keys())


# ── PollingMarketDataAdapter ──────────────────────────────────────────────────


class PollingMarketDataAdapter:
    """Phase 1 Market Data Adapter — Twelve Data REST polling.

    Single instance: call `.refresh()` to update all timeframes,
    then read from `.store` any time via `.get_bars()`.

    Quota-aware: daily budget (450 safe credits) + per-minute rate limit (8 req/min).
    Raises QuotaExceeded → caller decides whether to skip or abort.

    Usage in cron job (daily briefing pipeline):
        adapter = PollingMarketDataAdapter()
        adapter.refresh()           # fetch all timeframes; raises QuotaExceeded if exhausted
        bars = adapter.get_bars("1h", limit=20)

    Usage in dashboard (on-demand, not continuous polling):
        adapter = PollingMarketDataAdapter()
        try:
            price = adapter.get_price()   # lightweight 1-credit call
        except QuotaExceeded:
            price = adapter.store.get_latest("price")
    """

    SYMBOL = "XAU/USD"
    # E5 required timeframes + 1day for completeness
    TIMEFRAMES = ["1min", "5min", "15min", "1h", "4h", "1day"]

    def __init__(self, api_key: Optional[str] = None,
                 quota: Optional[QuotaTracker] = None,
                 store: Optional[CandleStore] = None):
        self._key   = api_key or _get_api_key()
        self._quota = quota   or QuotaTracker()
        self._client = _TwelveDataClient(api_key=self._key, quota=self._quota)
        self._store = store or CandleStore()
        self._last_refresh: Optional[datetime] = None
        # Phase 2A: 30s TTL cache for intrabar price freshness
        self._price_cache: dict = {"price": None, "timestamp": None}  # {price: float, timestamp: datetime}

    @property
    def store(self) -> CandleStore:
        """Read-only access to the in-memory candle store."""
        return self._store

    @property
    def quota(self) -> QuotaTracker:
        return self._quota

    # ── Core refresh ─────────────────────────────────────────────────────────

    def refresh(self, timeframes: Optional[list[str]] = None,
                outputsize: int = 60) -> dict[str, list[Candle]]:
        """Fetch latest candles for all timeframes and store in memory.

        Args:
            timeframes: list of intervals to fetch (default: all E5 timeframes)
            outputsize: number of historical bars to fetch per timeframe

        Returns:
            dict of {timeframe: [Candle, ...]} for all fetched timeframes

        Raises:
            QuotaExceeded: if daily budget is exhausted before completion
            MarketDataError: on unrecoverable HTTP errors

        E5 behaviour:
          • Fetches ALL timeframes sequentially (respects per-minute limit)
          • Each failed timeframe raises immediately (no partial results silently dropped)
          • If you only need ONE timeframe, call refresh(timeframes=["1h"]) instead
        """
        if timeframes is None:
            timeframes = self.TIMEFRAMES

        results: dict[str, list[Candle]] = {}
        for tf in timeframes:
            bars = self._fetch_timeframe(tf, outputsize=outputsize)
            results[tf] = bars
            self._store.store(bars)

        self._last_refresh = datetime.now(timezone.utc)
        return results

    def refresh_single(self, timeframe: str, outputsize: int = 60) -> list[Candle]:
        """Fetch one timeframe only (lowest quota cost)."""
        bars = self._fetch_timeframe(timeframe, outputsize=outputsize)
        self._store.store(bars)
        self._last_refresh = datetime.now(timezone.utc)
        return bars

    # ── Convenience read API ─────────────────────────────────────────────────

    def get_price(self) -> float:
        """Get latest real-time price (1 credit)."""
        return self._client.get_price(self.SYMBOL)

    def get_price_info(self, ttl_seconds: int = 30) -> dict:
        """Get latest price with freshness metadata (Phase 2A intrabar path).

        Uses a 30-second TTL cache to avoid redundant 1-credit API calls
        during rapid auto-refresh cycles. Falls back to stale cache if the
        API call fails, so callers always get {price, timestamp, fresh} even
        under degraded conditions.

        Returns:
            dict with keys: price (float), timestamp (datetime), fresh (bool)
        """
        now = datetime.now(timezone.utc)
        cached_price = self._price_cache.get("price")
        cached_ts    = self._price_cache.get("timestamp")

        if cached_price is not None and cached_ts is not None:
            age = (now - cached_ts).total_seconds()
            if age <= ttl_seconds:
                return {"price": cached_price, "timestamp": cached_ts, "fresh": True}

        # Cache miss or stale — try live fetch
        try:
            price = self._client.get_price(self.SYMBOL)
            self._price_cache = {"price": price, "timestamp": now}
            return {"price": price, "timestamp": now, "fresh": True}
        except (QuotaExceeded, MarketDataError):
            # Graceful fallback: return stale cache even if expired
            if cached_price is not None:
                return {"price": cached_price, "timestamp": cached_ts, "fresh": False}
            raise  # no cache, no choice but to propagate

    def get_bars(self, timeframe: str, limit: int = 60) -> list[Candle]:
        """Read cached bars from the in-memory store.

        Returns empty list if store is empty (no refresh() called yet).
        """
        return self._store.get_bars(timeframe, limit=limit)

    def get_latest_bar(self, timeframe: str) -> Optional[Candle]:
        """Get the most recent candle for a timeframe."""
        return self._store.get_latest(timeframe)

    def get_latest_price_candle(self) -> Candle:
        """Get latest price as a single-point CandleDTO.

        Falls back to latest stored candle if quota exhausted.
        """
        try:
            price = self.get_price()
            return Candle.from_price(self.SYMBOL, price)
        except QuotaExceeded:
            latest = self._store.get_latest("price")
            if latest is not None:
                return latest
            raise

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fetch_timeframe(self, timeframe: str, outputsize: int) -> list[Candle]:
        raw = self._client.get_time_series(
            symbol=self.SYMBOL,
            timeframe=timeframe,
            outputsize=outputsize,
        )
        # Twelve Data returns newest-first; reverse for chronological order
        candles = [
            Candle.from_twelvedata_bar(
                symbol=self.SYMBOL,
                timeframe=timeframe,
                bar=bar,
            )
            for bar in reversed(raw)
        ]
        return candles

    def __repr__(self) -> str:
        tfs = self._store.all_timeframes()
        return (
            f"PollingMarketDataAdapter(timeframes={tfs}, "
            f"daily_remaining={self._quota.daily_remaining:.0f}, "
            f"minute_remaining={self._quota.minute_remaining})"
        )


# ── Mock Adapter (for tests / E4 fixture replacement) ────────────────────────


class MockMarketDataAdapter:
    """Deterministic mock for tests. Never calls Twelve Data.

    Replace real adapter with this in tests:
        adapter = MockMarketDataAdapter(anchor_close=4080.90)
        bars = adapter.get_bars("1D", limit=60)
    """

    def __init__(self, anchor_close: float = 4080.0,
                 anchor_time: Optional[datetime] = None):
        self._store = CandleStore()
        self._price = anchor_close
        self._anchor_time = anchor_time or datetime(2026, 6, 28, 5, 18, 12, tzinfo=timezone.utc)
        self._quota = QuotaTracker()
        self._quota.daily_used = 0.0   # never exhausted in mock

    @property
    def store(self) -> CandleStore:
        return self._store

    @property
    def quota(self) -> QuotaTracker:
        return self._quota

    def refresh(self, timeframes: Optional[list[str]] = None,
                outputsize: int = 60) -> dict[str, list[Candle]]:
        import random as _r
        if timeframes is None:
            timeframes = PollingMarketDataAdapter.TIMEFRAMES

        now = datetime.now(timezone.utc)
        results = {}
        for tf in timeframes:
            cfg = _TIMEFRAME_SECONDS.get(tf, 86400)
            bars = []
            for i in range(outputsize):
                t = now.timestamp() - cfg * (outputsize - 1 - i)
                dt = datetime.fromtimestamp(t, tz=timezone.utc)
                r = _r.Random(int(t))
                delta = 0.003 * self._price
                o = self._price + r.uniform(-delta, delta)
                c = self._price + r.uniform(-delta, delta)
                h = max(o, c) + r.uniform(0, delta * 0.3)
                l = min(o, c) - r.uniform(0, delta * 0.3)
                bars.append(Candle(
                    symbol="XAU/USD", timeframe=tf,
                    datetime=dt,
                    open=round(o, 5), high=round(h, 5),
                    low=round(l, 5), close=round(c, 5),
                    source="mock",
                ))
            self._store.store(bars)
            results[tf] = bars

        return results

    def get_price(self) -> float:
        return self._price

    def get_bars(self, timeframe: str, limit: int = 60) -> list[Candle]:
        return self._store.get_bars(timeframe, limit=limit)

    def get_latest_bar(self, timeframe: str) -> Optional[Candle]:
        return self._store.get_latest(timeframe)

    def get_latest_price_candle(self) -> Candle:
        return Candle.from_price("XAU/USD", self._price)

    def __repr__(self) -> str:
        return f"MockMarketDataAdapter(price={self._price})"


# ── Timeframe seconds lookup ──────────────────────────────────────────────────

_TIMEFRAME_SECONDS: dict[str, int] = {
    "1min":  60,
    "5min":  300,
    "15min": 900,
    "1h":    3600,
    "4h":    14400,
    "1day":  86400,
}