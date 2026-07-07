"""generate_engine_weekly_report.py — produce a weekly calibration markdown report
from data/engine_reviews.csv.

Usage
-----
    # This week (dry-run to stdout)
    python scripts/generate_engine_weekly_report.py --week-start 2026-06-30 --dry-run

    # Last full week → reports/engine_reviews/weekly-2026-06-30.md
    python scripts/generate_engine_weekly_report.py --week-start 2026-06-30

    # Custom symbol filter
    python scripts/generate_engine_weekly_report.py --week-start 2026-06-30 --symbol XAUUSD

    # Custom output path
    python scripts/generate_engine_weekly_report.py --week-start 2026-06-30 \
        --output reports/engine_reviews/my-weekly-2026-06-30.md

Step 1 (log)   : scripts/log_engine_review.py
Step 2 (update): scripts/update_engine_review.py
Step 3 (review): scripts/generate_engine_weekly_report.py
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_HKT = timezone(timedelta(hours=8))

DEFAULT_CSV = Path.home() / "projects" / "daily-xauusd-bot" / "data" / "engine_reviews.csv"
DEFAULT_OUT_DIR = Path.home() / "projects" / "daily-xauusd-bot" / "reports" / "engine_reviews"


# ── Statistical helpers ─────────────────────────────────────────────────────

def safe_rate(numerator: int, denominator: int, precision: int = 1) -> str:
    if denominator == 0:
        return "N/A"
    return f"{numerator / denominator * 100:.{precision}f}%"


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


# ── Data loading ─────────────────────────────────────────────────────────────

def load_reviews(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def filter_week(rows: list[dict], week_start: str, week_end: str,
                symbol: str | None) -> list[dict]:
    result = []
    for r in rows:
        d = r.get("date", "").strip()
        if d < week_start or d > week_end:
            continue
        if symbol and r.get("symbol", "").strip() != symbol:
            continue
        result.append(r)
    return result


# ── Metrics computation ──────────────────────────────────────────────────────

def compute_summary(rows: list[dict]) -> dict[str, Any]:
    total = len(rows)

    # outcome-label breakdown
    outcomes = {}
    for r in rows:
        lbl = r.get("outcome_label", "").strip()
        if lbl:
            outcomes[lbl] = outcomes.get(lbl, 0) + 1

    reviewed = sum(outcomes.values())
    pending = total - reviewed

    outcome_conf = [
        float(r["confidence"])
        for r in rows
        if r.get("confidence", "").strip()
    ]
    avg_conf = mean(outcome_conf)

    insufficient = sum(
        1 for r in rows
        if r.get("insufficient_context", "").strip().lower() == "true"
    )
    ma200_ok = sum(
        1 for r in rows
        if r.get("ma200_available", "").strip().lower() == "true"
    )
    ma200_rate = safe_rate(ma200_ok, total)

    return {
        "total_reviews": total,
        "correct": outcomes.get("correct", 0),
        "partially_correct": outcomes.get("partially_correct", 0),
        "wrong": outcomes.get("wrong", 0),
        "invalidated": outcomes.get("invalidated", 0),
        "no_follow_through": outcomes.get("no_follow_through", 0),
        "reviewed_outcomes_count": reviewed,
        "pending_outcomes_count": pending,
        "avg_confidence": avg_conf,
        "insufficient_context_count": insufficient,
        "ma200_available_rate": ma200_rate,
    }


def _outcome_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("outcome_label", "").strip()]


def _invalid_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("invalidation_hit", "").strip()]


def _correct_rate(subset: list[dict]) -> str:
    denom = len(subset)
    if denom == 0:
        return "N/A"
    n = sum(1 for r in subset if r.get("outcome_label", "").strip() == "correct")
    return safe_rate(n, denom)


def _invalid_rate(subset: list[dict]) -> str:
    denom = len(subset)
    if denom == 0:
        return "N/A"
    n = sum(1 for r in subset
            if r.get("invalidation_hit", "").strip().lower() in ("true", "1", "yes"))
    return safe_rate(n, denom)


def _avg_confidence(subset: list[dict]) -> str:
    vals = [float(r["confidence"]) for r in subset if r.get("confidence", "").strip()]
    m = mean(vals)
    return f"{m:.1f}" if m is not None else "N/A"


def _failure_dist(subset: list[dict]) -> dict[str, int]:
    dist = {}
    for r in subset:
        fr = r.get("failure_reason", "").strip()
        if fr and fr != "none":
            dist[fr] = dist.get(fr, 0) + 1
    return dist


def _most_common_failure(subset: list[dict]) -> str:
    dist = _failure_dist(subset)
    if not dist:
        return "N/A"
    return max(dist, key=dist.get)  # type: ignore[arg-type]


def break_by(rows: list[dict], key: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        val = r.get(key, "").strip() or "(blank)"
        buckets.setdefault(val, []).append(r)
    return buckets


def breakdown_table(rows: list[dict], key: str) -> list[dict[str, Any]]:
    out = []
    for val, bucket_rows in break_by(rows, key).items():
        outcome_rows = _outcome_rows(bucket_rows)
        correct_r = _correct_rate(outcome_rows)
        avg_c = _avg_confidence(bucket_rows)
        inv_r = _invalid_rate(_invalid_rows(bucket_rows))
        out.append({
            key: val,
            "review_count": len(bucket_rows),
            "avg_confidence": avg_c,
            "correct_rate": correct_r,
            "invalidation_rate": inv_r,
        })
    # sort by review_count desc
    out.sort(key=lambda x: x["review_count"], reverse=True)
    return out


# ── Observations ────────────────────────────────────────────────────────────

def make_observations(rows: list[dict], summary: dict) -> list[str]:
    obs: list[str] = []
    if not rows:
        obs.append("- No reviews logged for this period.")
        return obs

    # Most active session
    sess_buckets = break_by(rows, "session")
    if sess_buckets:
        top_session = max(sess_buckets, key=lambda k: len(sess_buckets[k]))
        obs.append(f"- Most active session: **{top_session}** ({len(sess_buckets[top_session])} reviews)")

    # Session correct rates
    session_rates = {
        s: _correct_rate(_outcome_rows(rs))
        for s, rs in sess_buckets.items()
    }
    with_outcomes = {s: r for s, r in session_rates.items() if r != "N/A"}
    if with_outcomes:
        best_s = max(with_outcomes, key=lambda s: float(with_outcomes[s].rstrip("%")))
        worst_s = min(with_outcomes, key=lambda s: float(with_outcomes[s].rstrip("%")))
        obs.append(f"- Best session correct_rate: **{best_s}** ({with_outcomes[best_s]})")
        obs.append(f"- Worst session correct_rate: **{worst_s}** ({with_outcomes[worst_s]})")

    # Bucket reliability
    bucket_buckets = break_by(rows, "confidence_bucket")
    bucket_rates = {
        b: _correct_rate(_outcome_rows(rs))
        for b, rs in bucket_buckets.items()
    }
    with_bucket = {b: r for b, r in bucket_rates.items() if r != "N/A"}
    if with_bucket:
        best_b = max(with_bucket, key=lambda b: float(with_bucket[b].rstrip("%")))
        worst_b = min(with_bucket, key=lambda b: float(with_bucket[b].rstrip("%")))
        obs.append(f"- Most reliable bucket: **{best_b}** ({with_bucket[best_b]} correct)")
        obs.append(f"- Least reliable bucket: **{worst_b}** ({with_bucket[worst_b]} correct)")

    # Insufficient context impact
    insuf = [r for r in rows if r.get("insufficient_context", "").strip().lower() == "true"]
    if insuf:
        insuf_outcomes = _outcome_rows(insuf)
        if insuf_outcomes:
            insuf_correct = _correct_rate(insuf_outcomes)
            obs.append(f"- Insufficient_context rows ({len(insuf)}): correct_rate = **{insuf_correct}**")
        else:
            obs.append(f"- **{len(insuf)}** rows had insufficient_context (MA200 unavailable); no outcomes yet")

    # Direction invalidation
    dir_buckets = break_by(rows, "direction_classification")
    dir_inv = {}
    for d, rs in dir_buckets.items():
        inv_rows = _invalid_rows(rs)
        if inv_rows:
            dir_inv[d] = _invalid_rate(inv_rows)
    if dir_inv:
        worst_inv_dir = max(dir_inv, key=lambda d: float(dir_inv[d].rstrip("%")))
        obs.append(f"- Highest invalidation rate by direction: **{worst_inv_dir}** ({dir_inv[worst_inv_dir]})")

    # MA200 availability
    ma200_ok = sum(1 for r in rows if r.get("ma200_available", "").strip().lower() == "true")
    if rows:
        rate_pct = ma200_ok / len(rows) * 100
        obs.append(f"- MA200 available: **{ma200_ok}/{len(rows)}** ({rate_pct:.0f}%)")

    return obs


# ── Markdown renderer ────────────────────────────────────────────────────────

BUCKETS = ["85-100", "70-84", "55-69", "40-54", "0-39"]


def _fmt_float(v: float | None, fallback: str = "N/A") -> str:
    if v is None:
        return fallback
    return f"{v:.1f}"


def render_report(
    week_start: str,
    week_end: str,
    symbol: str | None,
    rows: list[dict],
    summary: dict,
    now_iso: str,
) -> str:
    sym_line = f" | Symbol filter: **{symbol}**" if symbol else ""
    lines = [
        f"# Weekly Candlestick Engine Review",
        f"*Generated: {now_iso} (HKT)*",
        "",
        "## Period",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| week_start | {week_start} |",
        f"| week_end | {week_end} |{sym_line}",
        "",
        "## Summary",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| total_reviews | {summary['total_reviews']} |",
        f"| correct | {summary['correct']} |",
        f"| partially_correct | {summary['partially_correct']} |",
        f"| wrong | {summary['wrong']} |",
        f"| invalidated | {summary['invalidated']} |",
        f"| no_follow_through | {summary['no_follow_through']} |",
        f"| reviewed_outcomes | {summary['reviewed_outcomes_count']} |",
        f"| pending_outcomes | {summary['pending_outcomes_count']} |",
        f"| avg_confidence | {_fmt_float(summary['avg_confidence'])} |",
        f"| insufficient_context_count | {summary['insufficient_context_count']} |",
        f"| ma200_available_rate | {summary['ma200_available_rate']} |",
        "",
    ]

    # Session breakdown
    if rows:
        sess_rows = breakdown_table(rows, "session")
        lines += [
            "## Session Breakdown",
            f"| session | review_count | avg_confidence | correct_rate | invalidation_rate |",
            f"| --- | --- | --- | --- | --- |",
        ]
        for r in sess_rows:
            lines.append(
                f"| {r['session']} | {r['review_count']} | {r['avg_confidence']} | "
                f"{r['correct_rate']} | {r['invalidation_rate']} |"
            )
        lines.append("")

        # Direction breakdown
        dir_rows = breakdown_table(rows, "direction_classification")
        lines += [
            "## Direction Breakdown",
            f"| direction_classification | review_count | avg_confidence | correct_rate | invalidation_rate |",
            f"| --- | --- | --- | --- | --- |",
        ]
        for r in dir_rows:
            lines.append(
                f"| {r['direction_classification']} | {r['review_count']} | {r['avg_confidence']} | "
                f"{r['correct_rate']} | {r['invalidation_rate']} |"
            )
        lines.append("")

        # Confidence bucket breakdown
        bucket_rows = breakdown_table(rows, "confidence_bucket")
        # Sort by defined bucket order
        bucket_order = {b: i for i, b in enumerate(BUCKETS)}
        bucket_rows.sort(key=lambda x: bucket_order.get(x["confidence_bucket"], 99))
        lines += [
            "## Confidence Calibration",
            f"| confidence_bucket | review_count | avg_confidence | correct_rate | invalidation_rate | most_common_failure |",
            f"| --- | --- | --- | --- | --- | --- |",
        ]
        for r in bucket_rows:
            outcome_rows = _outcome_rows([rr for rr in rows if rr.get("confidence_bucket", "").strip() == r["confidence_bucket"]])
            fail = _most_common_failure(outcome_rows)
            lines.append(
                f"| {r['confidence_bucket']} | {r['review_count']} | {r['avg_confidence']} | "
                f"{r['correct_rate']} | {r['invalidation_rate']} | {fail} |"
            )
        lines.append("")

        # Failure reasons
        all_outcomes = _outcome_rows(rows)
        fail_dist = _failure_dist(all_outcomes)
        lines += ["## Failure Reasons", f"| failure_reason | count |", f"| --- | --- |"]
        if fail_dist:
            for fr, cnt in sorted(fail_dist.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"| {fr} | {cnt} |")
        else:
            lines.append("| _No failure_reason data yet._ | |")
        lines.append("")

        # Observations
        obs = make_observations(rows, summary)
        lines += ["## Top Observations"]
        lines += [f"- {o}" for o in obs]
        lines.append("")

    else:
        lines += [
            "## No Data",
            "No engine reviews found for the selected period.  ",
            "Log reviews with `scripts/log_engine_review.py` before generating this report.",
            "",
        ]

    # Next Adjustments
    lines += [
        "## Next Adjustments",
        "_Fill in after reviewing observations above._",
        "",
        "- Adjustment 1: ",
        "- Adjustment 2: ",
        "- Adjustment 3: ",
        "",
        "---",
        "*Candlestick Direction Engine — manual-only analytical tool. "
        "Not production trading advice. For calibration review only.*",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_date(raw: str) -> str:
    try:
        datetime.strptime(raw, "%Y-%m-%d")
        return raw
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid date format: {raw!r}. Use YYYY-MM-DD.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a weekly Candlestick Engine calibration markdown report.",
    )
    parser.add_argument("--week-start", required=True, type=_parse_date,
                        help="Start of week (YYYY-MM-DD)")
    parser.add_argument("--week-end", type=_parse_date,
                        help="End of week (YYYY-MM-DD). Defaults to week-start + 6 days.")
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV,
                        help=f"Path to engine_reviews.csv (default: {DEFAULT_CSV})")
    parser.add_argument("--output", type=Path,
                        help="Output markdown path. Auto-generated if omitted.")
    parser.add_argument("--symbol",
                        help="Filter by symbol (default: all symbols)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print markdown to stdout, do not write file")

    args = parser.parse_args()

    week_start = args.week_start
    week_end = args.week_end
    if week_end is None:
        end_dt = datetime.strptime(week_start, "%Y-%m-%d") + timedelta(days=6)
        week_end = end_dt.strftime("%Y-%m-%d")

    if week_end < week_start:
        parser.error("--week-end must not be before --week-start")

    rows = load_reviews(args.input)
    filtered = filter_week(rows, week_start, week_end, args.symbol)
    summary = compute_summary(filtered)
    now_iso = datetime.now(_HKT).isoformat(timespec="seconds")
    md = render_report(week_start, week_end, args.symbol, filtered, summary, now_iso)

    if args.dry_run:
        print(md)
        return

    out_path = args.output
    if out_path is None:
        out_path = DEFAULT_OUT_DIR / f"weekly-{week_start}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"OK|{out_path}")


if __name__ == "__main__":
    main()