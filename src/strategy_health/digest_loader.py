"""Weekly Digest Loader — read-only, graceful fallback.

Loads the latest weekly digest JSON from disk, plus helpers for
path-based loading and fallback to current snapshot summary when
no digest file is available.

Usage::

    from strategy_health.digest_loader import load_latest_weekly_digest

    digest = load_latest_weekly_digest()       # latest across all windows
    digest = load_latest_weekly_digest(window_days=7)  # specific window
    digest = load_weekly_digest_from_path(path)          # exact file

All functions return a ``dict`` or a fallback dict (never raise).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "load_latest_weekly_digest",
    "load_weekly_digest_from_path",
    "weekly_digest_fallback",
]

HKT = timezone(timedelta(hours=8))
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "weekly_digest"
_logger = logging.getLogger("digest_loader")


def now_hkt() -> datetime:
    return datetime.now(HKT)


# ── Fallback ─────────────────────────────────────────────────────────────────
def weekly_digest_fallback(reason: str = "unavailable") -> dict:
    """Return a graceful fallback dict when no digest is available."""
    return {
        "schema_version": "1.0-fallback",
        "generated_at": now_hkt().isoformat(),
        "window_days": None,
        "dominant_bias": None,
        "avg_fusion_confidence": None,
        "summary": {},
        "data_sources": {},
        "metadata": {"fallback_reason": reason},
        "_fallback": True,
    }


# ── Loader ───────────────────────────────────────────────────────────────────
def load_weekly_digest_from_path(path: str | Path) -> dict:
    """Load a digest from an exact file path. Falls back gracefully."""
    p = Path(path).resolve()
    if not p.exists():
        _logger.warning("[DigestLoader] File not found: %s — using fallback", p)
        return weekly_digest_fallback(reason=f"file not found: {p.name}")

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # Basic sanity check
        if not isinstance(data, dict) or "generated_at" not in data:
            raise ValueError("invalid digest structure")
        return data
    except Exception as exc:
        _logger.warning("[DigestLoader] Failed to read %s [%s] — using fallback", p, exc)
        return weekly_digest_fallback(reason=f"parse error: {exc}")


def load_latest_weekly_digest(
    window_days: Optional[int] = None,
    output_dir: Optional[Path] = None,
) -> dict:
    """Load the most recent weekly digest JSON.

    If ``window_days`` is specified, looks for a digest generated with that
    exact window (e.g. ``7`` looks for ``*_7d_*.json`` suffix pattern).
    If ``window_days`` is None, returns the newest file regardless of window.

    Falls back gracefully (never raises) if no file is found or file is corrupt.
    """
    odir = output_dir or DEFAULT_OUTPUT_DIR
    if not odir.exists():
        _logger.warning("[DigestLoader] Digest directory not found: %s — using fallback", odir)
        return weekly_digest_fallback(reason="digest directory does not exist")

    # Find all digest files
    digest_files = sorted(odir.glob("*_digest.json"), reverse=True)
    if not digest_files:
        _logger.warning("[DigestLoader] No digest files in %s — using fallback", odir)
        return weekly_digest_fallback(reason="no digest files found")

    # If window_days is specified, prefer a file whose window matches.
    if window_days is not None:
        window_preferred = [f for f in digest_files if f"_{window_days}d_digest.json" in f.name]
        candidates = window_preferred if window_preferred else digest_files
    else:
        candidates = digest_files

    return load_weekly_digest_from_path(candidates[0])


# ── Convenience accessors ────────────────────────────────────────────────────
def get_price_summary(digest: dict) -> dict:
    """Return the price summary sub-dict, or empty dict if unavailable."""
    return digest.get("summary", {}).get("price", {})


def get_fusion_summary(digest: dict) -> dict:
    """Return the fusion summary sub-dict, or empty dict if unavailable."""
    return digest.get("summary", {}).get("fusion", {})


def get_engine_review_summary(digest: dict) -> dict:
    """Return the engine review summary, or empty dict if unavailable."""
    return digest.get("summary", {}).get("engine_reviews", {})


def get_news_summary(digest: dict) -> dict:
    """Return the news summary, or empty dict if unavailable."""
    return digest.get("summary", {}).get("news", {})


def digest_age_hours(digest: dict) -> Optional[float]:
    """Return hours since digest.generated_at, or None if unavailable."""
    gen_str = digest.get("generated_at")
    if not gen_str:
        return None
    try:
        gen = datetime.fromisoformat(gen_str).astimezone(HKT)
        delta = datetime.now(HKT) - gen
        return delta.total_seconds() / 3600
    except Exception:
        return None