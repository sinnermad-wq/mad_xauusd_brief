#!/usr/bin/env python3
"""generate_weekly_digest.py — XAUUSD Weekly Digest Generator.

Reads daily briefings, fusion outputs, and engine reviews from the last 7/14/30
days and produces a structured weekly digest (JSON + optional Markdown).

Usage:
    python scripts/generate_weekly_digest.py                    # 7d, JSON only
    python scripts/generate_weekly_digest.py --window-days 14  # 14d
    python scripts/generate_weekly_digest.py --window-days 7 --output-md
    python scripts/generate_weekly_digest.py --dry-run           # preview only

Output:
    data/weekly_digest/<date>T<time>_digest.json   — always written
    data/weekly_digest/latest_weekly_digest.json   — symlink / copy
    data/weekly_digest/<date>_weekly.md            — only with --output-md
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
import textwrap
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

HKT = timezone(timedelta(hours=8))
SCHEMA_VERSION = "1.0"
LOGGER = logging.getLogger("weekly_digest")


def now_hkt() -> datetime:
    return datetime.now(HKT)


def today_hkt() -> date:
    return now_hkt().date()


# ── graceful fallback helpers ────────────────────────────────────────────────
class DigestGenerationError(Exception):
    """Raised when critical data is missing but graceful degradation is possible."""


def _safe_read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_read_csv(path: Path) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


# ── data aggregation ────────────────────────────────────────────────────────
def _collect_daily_briefings(base_dir: Path, days: int) -> list[dict]:
    """Read all *_<mode>.json files from history/ within the last `days` days."""
    brief_dir = base_dir / "data" / "history"
    if not brief_dir.exists():
        return []

    cutoff = datetime.now(HKT) - timedelta(days=days)
    results = []
    for fp in sorted(brief_dir.glob("*_daily.json"), reverse=True):
        try:
            ts_str = fp.stem.split("_")[0]  # "2026-07-10T08-33-14"
            dt = datetime.strptime(ts_str, "%Y-%m-%dT%H-%M-%S").replace(tzinfo=HKT)
            if dt < cutoff:
                continue
            data = _safe_read_json(fp)
            if data:
                results.append(data)
        except Exception:
            continue
    return results


def _collect_fusion_outputs(base_dir: Path, days: int) -> list[dict]:
    """Read fusion output JSONs within the last `days` days."""
    fusion_dir = base_dir / "data" / "history" / "fusion"
    if not fusion_dir.exists():
        return []

    cutoff = datetime.now(HKT) - timedelta(days=days)
    results = []
    for fp in sorted(fusion_dir.glob("*.json"), reverse=True):
        try:
            dt = datetime.strptime(fp.stem, "%Y-%m-%d").replace(tzinfo=HKT)
            if dt < cutoff:
                continue
            data = _safe_read_json(fp)
            if data:
                results.append(data)
        except Exception:
            continue
    return results


def _collect_engine_reviews(base_dir: Path, days: int) -> list[dict]:
    """Read engine_reviews.csv within the last `days` days."""
    csv_path = base_dir / "data" / "engine_reviews.csv"
    if not csv_path.exists():
        return []

    rows = _safe_read_csv(csv_path)
    cutoff = datetime.now(HKT) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    return [r for r in rows if r.get("date", "") >= cutoff_str]


# ── analytics ────────────────────────────────────────────────────────────────
def _summarise_price(briefings: list[dict]) -> dict:
    if not briefings:
        return {"latest": None, "high": None, "low": None, "change_pct": None}
    prices = [(b["xauusd_price"], b.get("daily_change_pct", 0)) for b in briefings if b.get("xauusd_price")]
    if not prices:
        return {"latest": None, "high": None, "low": None, "change_pct": None}
    latest_price = prices[0][0]
    highs = [p[0] for p in prices]
    lows = highs[:]
    first_price = prices[-1][0] if len(prices) > 1 else latest_price
    return {
        "latest": latest_price,
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
        "change_pct": round(((latest_price - first_price) / first_price) * 100, 2) if first_price else None,
    }


def _summarise_fusion(fusions: list[dict]) -> dict:
    if not fusions:
        return {
            "total_runs": 0,
            "bullish_count": 0,
            "bearish_count": 0,
            "neutral_count": 0,
            "avg_confidence": None,
            "trade_candidate_count": 0,
            "conflict_none_count": 0,
            "conflict_any_count": 0,
            "consensus_insufficient_count": 0,
        }
    biases = {"bullish": 0, "bearish": 0, "neutral": 0}
    confs = []
    trade_candidates = 0
    conflict_none = 0
    conflict_any = 0
    insufficient = 0
    for f in fusions:
        b = f.get("fusion_bias", "").lower()
        if b in biases:
            biases[b] += 1
        confs.append(f.get("fusion_confidence", 0))
        if f.get("trade_candidate"):
            trade_candidates += 1
        cl = f.get("conflict_label", "none")
        if cl == "none":
            conflict_none += 1
        else:
            conflict_any += 1
        if f.get("consensus_label") == "insufficient_context":
            insufficient += 1
    avg_conf = sum(confs) / len(confs) if confs else None
    return {
        "total_runs": len(fusions),
        "bullish_count": biases["bullish"],
        "bearish_count": biases["bearish"],
        "neutral_count": biases["neutral"],
        "avg_confidence": round(avg_conf, 4) if avg_conf else None,
        "trade_candidate_count": trade_candidates,
        "conflict_none_count": conflict_none,
        "conflict_any_count": conflict_any,
        "consensus_insufficient_count": insufficient,
    }


def _summarise_engine_reviews(reviews: list[dict]) -> dict:
    if not reviews:
        return {
            "total_reviews": 0,
            "avg_confidence": None,
            "outcomes_filled": 0,
            "outcomes_correct": 0,
            "outcomes_incorrect": 0,
            "invalidations_hit_count": 0,
            "insufficient_context_count": 0,
        }
    confs = [float(r["confidence"]) for r in reviews if r.get("confidence", "").replace(".", "", 1).isdigit()]
    outcomes = [r.get("outcome_label", "") for r in reviews if r.get("outcome_label")]
    invalidations = sum(1 for r in reviews if r.get("invalidation_hit", "").lower() == "true")
    insufficient = sum(1 for r in reviews if r.get("insufficient_context", "").lower() == "true")
    return {
        "total_reviews": len(reviews),
        "avg_confidence": round(sum(confs) / len(confs), 2) if confs else None,
        "outcomes_filled": len(outcomes),
        "outcomes_correct": sum(1 for o in outcomes if o in ("correct", "partially_correct")),
        "outcomes_incorrect": sum(1 for o in outcomes if o in ("incorrect", "failed")),
        "invalidations_hit_count": invalidations,
        "insufficient_context_count": insufficient,
    }


def _summarise_news(briefings: list[dict]) -> dict:
    """Aggregate top_news across all briefings in window."""
    all_news: list[dict] = []
    for b in briefings:
        for n in b.get("top_news", []):
            all_news.append(n)
    tag_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for n in all_news:
        tag = n.get("tag", "unknown")
        src = n.get("source", "unknown")
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        source_counts[src] = source_counts.get(src, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda x: -x[1])[:5]
    top_sources = sorted(source_counts.items(), key=lambda x: -x[1])[:5]
    return {
        "total_articles": len(all_news),
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        "top_sources": [{"source": s, "count": c} for s, c in top_sources],
    }


def _summarise_weekly_themes(briefings: list[dict]) -> list[str]:
    """Extract the most common trend labels from briefings."""
    trends: dict[str, int] = {}
    for b in briefings:
        t = b.get("trend", "unknown")
        trends[t] = trends.get(t, 0) + 1
    return [f"{t} ({n} days)" for t, n in sorted(trends.items(), key=lambda x: -x[1])]


# ── markdown rendering ───────────────────────────────────────────────────────
def _render_markdown(digest: dict, window_days: int) -> str:
    gen = digest.get("generated_at", "unknown")
    summary = digest.get("summary", {})

    lines = [
        f"# XAUUSD Weekly Digest — {gen[:10]}",
        "",
        f"**Window:** last {window_days} days  |  "
        f"**Generated:** {gen}  |  "
        f"**Schema:** v{SCHEMA_VERSION}",
        "",
        "---",
        "",
        "## 📊 Price Summary",
        "",
    ]

    price = summary.get("price", {})
    if price.get("latest"):
        change = price.get("change_pct", 0)
        arrow = "🟢" if change >= 0 else "🔴"
        lines.extend([
            f"| Latest Price | High | Low | {window_days}d Change |",
            f"|---|---|---|---|---|",
            f"| ${price['latest']:.2f} | ${price['high']:.2f} | ${price['low']:.2f} | "
            f"{arrow} {change:+.2f}% |",
            "",
        ])
    else:
        lines.append("*Price data unavailable.*\n")

    # Fusion
    fusion = summary.get("fusion", {})
    lines.extend([
        "## 🧠 Fusion Engine",
        "",
        f"- Total runs: **{fusion.get('total_runs', 0)}**",
        f"- Bullish: **{fusion.get('bullish_count', 0)}**  |  "
        f"Bearish: **{fusion.get('bearish_count', 0)}**  |  "
        f"Neutral: **{fusion.get('neutral_count', 0)}**",
        f"- Avg confidence: **{fusion.get('avg_confidence', 'N/A')}**",
        f"- Trade candidates: **{fusion.get('trade_candidate_count', 0)}**",
        f"- Conflicts (any): **{fusion.get('conflict_any_count', 0)}**",
        f"- Insufficient context: **{fusion.get('consensus_insufficient_count', 0)}**",
        "",
    ])

    # Engine Reviews
    reviews = summary.get("engine_reviews", {})
    if reviews.get("total_reviews", 0) > 0:
        lines.extend([
            "## 🛰️ Engine Reviews",
            "",
            f"- Total reviews: **{reviews.get('total_reviews', 0)}**",
            f"- Avg confidence: **{reviews.get('avg_confidence', 'N/A')}**",
            f"- Outcomes filled: **{reviews.get('outcomes_filled', 0)}** "
            f"(✅ {reviews.get('outcomes_correct', 0)} / ❌ {reviews.get('outcomes_incorrect', 0)})",
            f"- MA200 insufficient: **{reviews.get('insufficient_context_count', 0)}**",
            "",
        ])

    # News
    news = summary.get("news", {})
    if news.get("total_articles", 0) > 0:
        tag_str = " / ".join(f"`{t['tag']}` ({t['count']})" for t in news.get("top_tags", [])[:5])
        lines.extend([
            "## 📰 News Coverage",
            "",
            f"Total articles: **{news.get('total_articles', 0)}**  |  "
            f"Top tags: {tag_str}",
            "",
        ])

    # Weekly themes
    themes = summary.get("weekly_themes", [])
    if themes:
        lines.extend([
            "## 📈 Trend Themes",
            "",
            "\n".join(f"- {t}" for t in themes),
            "",
        ])

    lines.extend([
        "---",
        f"*Generated by `generate_weekly_digest.py` v{SCHEMA_VERSION} — "
        f"read-only / manual-only*",
    ])
    return "\n".join(lines)


# ── main generator ─────────────────────────────────────────────────────────
def generate_weekly_digest(
    base_dir: Path,
    window_days: int = 7,
    output_dir: Optional[Path] = None,
    output_md: bool = False,
    dry_run: bool = False,
) -> dict:
    """Build the weekly digest structure and write JSON (+ optional MD)."""
    LOGGER.info("[Digest] Starting %dd weekly digest generation", window_days)

    output_dir = output_dir or (base_dir / "data" / "weekly_digest")
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── collect data ─────────────────────────────────────────────────────
    briefings = _collect_daily_briefings(base_dir, window_days)
    fusions = _collect_fusion_outputs(base_dir, window_days)
    reviews = _collect_engine_reviews(base_dir, window_days)

    LOGGER.info(
        "[Digest] Collected — briefings=%d fusions=%d reviews=%d",
        len(briefings), len(fusions), len(reviews)
    )

    # ── build summary ────────────────────────────────────────────────────
    price_sum = _summarise_price(briefings)
    fusion_sum = _summarise_fusion(fusions)
    reviews_sum = _summarise_engine_reviews(reviews)
    news_sum = _summarise_news(briefings)
    themes = _summarise_weekly_themes(briefings)

    # ── determine bias from fusion consensus ──────────────────────────────
    if fusions:
        bias_counts: dict[str, int] = {}
        for f in fusions:
            b = f.get("fusion_bias", "neutral")
            bias_counts[b] = bias_counts.get(b, 0) + 1
        dominant_bias = max(bias_counts, key=bias_counts.get)
        avg_conf = fusion_sum.get("avg_confidence")
    else:
        dominant_bias = None
        avg_conf = None

    digest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_hkt().isoformat(),
        "window_days": window_days,
        "data_range": {
            "earliest": (datetime.now(HKT) - timedelta(days=window_days)).strftime("%Y-%m-%d"),
            "latest": today_hkt().strftime("%Y-%m-%d"),
        },
        "dominant_bias": dominant_bias,
        "avg_fusion_confidence": avg_conf,
        "summary": {
            "price": price_sum,
            "fusion": fusion_sum,
            "engine_reviews": reviews_sum,
            "news": news_sum,
            "weekly_themes": themes,
            "briefings_in_window": len(briefings),
            "fusions_in_window": len(fusions),
            "reviews_in_window": len(reviews),
        },
        "data_sources": {
            "briefing_files": len(briefings),
            "fusion_files": len(fusions),
            "engine_review_rows": len(reviews),
        },
        "metadata": {
            "generated_by": "generate_weekly_digest.py",
            "repo_root": str(base_dir),
        },
    }

    if dry_run:
        LOGGER.info("[Digest] Dry-run — skipping file writes")
        return digest

    # ── write JSON ───────────────────────────────────────────────────────
    timestamp = now_hkt().strftime("%Y-%m-%dT%H-%M-%S")
    json_name = f"{timestamp}_{window_days}d_digest.json"
    json_path = output_dir / json_name
    json_path.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also write latest_weekly_digest.json
    latest_path = output_dir / "latest_weekly_digest.json"
    latest_path.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")

    LOGGER.info("[Digest] Written: %s", json_path)
    LOGGER.info("[Digest] Latest:  %s", latest_path)

    # ── write Markdown ────────────────────────────────────────────────────
    if output_md:
        md_name = f"{today_hkt().strftime('%Y-%m-%d')}_weekly.md"
        md_path = output_dir / md_name
        md_text = _render_markdown(digest, window_days)
        md_path.write_text(md_text, encoding="utf-8")
        LOGGER.info("[Digest] Markdown: %s", md_path)

    return digest


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    ap = argparse.ArgumentParser(description="XAUUSD Weekly Digest Generator")
    ap.add_argument(
        "--window-days", "-w",
        type=int, default=7,
        help="Number of days to look back (default: 7)",
    )
    ap.add_argument(
        "--output-dir",
        type=lambda p: Path(p).resolve(),
        default=None,
        help="Override output directory (default: data/weekly_digest/)",
    )
    ap.add_argument(
        "--output-md",
        action="store_true",
        help="Also write Markdown summary alongside JSON",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and print digest without writing files",
    )
    args = ap.parse_args()

    base_dir = REPO_ROOT
    try:
        digest = generate_weekly_digest(
            base_dir=base_dir,
            window_days=args.window_days,
            output_dir=args.output_dir,
            output_md=args.output_md,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(json.dumps(digest, indent=2, ensure_ascii=False))
        else:
            print(f"✅ Weekly digest ({digest['window_days']}d) written.")
            print(f"   Latest: data/weekly_digest/latest_weekly_digest.json")
    except Exception as exc:
        LOGGER.error("Digest generation failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())