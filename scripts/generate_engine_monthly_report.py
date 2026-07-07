"""generate_engine_monthly_report.py — monthly calibration markdown report.

Usage
-----
    python scripts/generate_engine_monthly_report.py --month 2026-07 --dry-run
    python scripts/generate_engine_monthly_report.py --month 2026-07
    python scripts/generate_engine_monthly_report.py --month 2026-07 --symbol GC=F
    python scripts/generate_engine_monthly_report.py --month 2026-07 \
        --output reports/engine_reviews/monthly-2026-07.md
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import median
from typing import Any

_HKT = timezone(timedelta(hours=8))

DEFAULT_CSV = Path.home() / "projects" / "daily-xauusd-bot" / "data" / "engine_reviews.csv"
DEFAULT_OUT_DIR = Path.home() / "projects" / "daily-xauusd-bot" / "reports" / "engine_reviews"


# ── Helpers ──────────────────────────────────────────────────────────────────

def safe_rate(n: int, d: int, decimals: int = 1) -> str:
    if d == 0:
        return "N/A"
    return f"{n / d * 100:.{decimals}f}%"


def safe_mean(vals: list[float]) -> str:
    if not vals:
        return "N/A"
    return f"{sum(vals) / len(vals):.1f}"


def safe_median(vals: list[float]) -> str:
    if not vals:
        return "N/A"
    return f"{median(vals):.1f}"


def month_boundaries(month: str) -> tuple[str, str]:
    """Return (first_day, last_day) YYYY-MM-DD for given YYYY-MM."""
    y, m = month.split("-")
    first = f"{y}-{m}-01"
    # last day: advance to next month, subtract 1 day
    y_i, m_i = int(y), int(m)
    if m_i == 12:
        next_y, next_m = y_i + 1, 1
    else:
        next_y, next_m = y_i, m_i + 1
    last_dt = datetime(next_y, next_m, 1) - timedelta(days=1)
    last = last_dt.strftime("%Y-%m-%d")
    return first, last


# ── Data loading ──────────────────────────────────────────────────────────────

def load_reviews(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def filter_month(rows: list[dict], first: str, last: str,
                 symbol: str | None) -> list[dict]:
    out = []
    for r in rows:
        d = r.get("date", "").strip()
        if d < first or d > last:
            continue
        if symbol and r.get("symbol", "").strip() != symbol:
            continue
        out.append(r)
    return out


# ── Stats helpers ────────────────────────────────────────────────────────────

def reviewed(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("outcome_label", "").strip()]


def with_invalidation(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("invalidation_hit", "").strip()]


def correct_rate(rows_: list[dict]) -> str:
    denom = len(rows_)
    if denom == 0:
        return "N/A"
    n = sum(1 for r in rows_ if r.get("outcome_label", "").strip() == "correct")
    return safe_rate(n, denom)


def invalidation_rate(rows_: list[dict]) -> str:
    denom = len(rows_)
    if denom == 0:
        return "N/A"
    n = sum(1 for r in rows_
            if r.get("invalidation_hit", "").strip().lower() in ("true", "1", "yes"))
    return safe_rate(n, denom)


def avg_conf_str(rows_: list[dict]) -> str:
    vals = [float(r["confidence"]) for r in rows_ if r.get("confidence", "").strip()]
    return safe_mean(vals)


def median_conf_str(rows_: list[dict]) -> str:
    vals = [float(r["confidence"]) for r in rows_ if r.get("confidence", "").strip()]
    return safe_median(vals)


def failure_dist(rows_: list[dict]) -> dict[str, int]:
    dist = {}
    for r in rows_:
        fr = r.get("failure_reason", "").strip()
        if fr and fr != "none":
            dist[fr] = dist.get(fr, 0) + 1
    return dist


def most_common_failure(rows_: list[dict]) -> str:
    d = failure_dist(rows_)
    if not d:
        return "N/A"
    return max(d, key=lambda k: d[k])  # type: ignore[arg-type]


# ── Bucket helper ─────────────────────────────────────────────────────────────

BUCKETS = ["85-100", "70-84", "55-69", "40-54", "0-39"]


def bucket_sort_key(label: str) -> int:
    return BUCKETS.index(label) if label in BUCKETS else 99


def break_by(rows_: list[dict], key: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows_:
        out.setdefault(r.get(key, "").strip() or "(blank)", []).append(r)
    return out


# ── Summary ──────────────────────────────────────────────────────────────────

def build_summary(rows_: list[dict]) -> dict[str, Any]:
    total = len(rows_)
    rev = reviewed(rows_)
    pending = total - len(rev)

    outcomes: dict[str, int] = {}
    for r in rev:
        outcomes[r.get("outcome_label", "").strip()] = \
            outcomes.get(r.get("outcome_label", "").strip(), 0) + 1

    insuf = sum(1 for r in rows_
                if r.get("insufficient_context", "").strip().lower() == "true")
    ma200_ok = sum(1 for r in rows_
                   if r.get("ma200_available", "").strip().lower() == "true")

    all_conf = [float(r["confidence"]) for r in rows_ if r.get("confidence", "").strip()]

    return {
        "total_reviews": total,
        "reviewed": len(rev),
        "pending": pending,
        "correct": outcomes.get("correct", 0),
        "partially_correct": outcomes.get("partially_correct", 0),
        "wrong": outcomes.get("wrong", 0),
        "invalidated": outcomes.get("invalidated", 0),
        "no_follow_through": outcomes.get("no_follow_through", 0),
        "avg_confidence": safe_mean(all_conf),
        "median_confidence": safe_median(all_conf),
        "insufficient_context_count": insuf,
        "insufficient_context_rate": safe_rate(insuf, total),
        "ma200_available_rate": safe_rate(ma200_ok, total),
    }


# ── Breakdowns ────────────────────────────────────────────────────────────────

def session_breakdown(rows_: list[dict]) -> list[dict[str, Any]]:
    out = []
    for sess, bucket in break_by(rows_, "session").items():
        rev_b = reviewed(bucket)
        inv_b = with_invalidation(bucket)
        out.append({
            "session": sess,
            "review_count": len(bucket),
            "reviewed_outcomes_count": len(rev_b),
            "avg_confidence": avg_conf_str(bucket),
            "correct_rate": correct_rate(rev_b),
            "invalidation_rate": invalidation_rate(inv_b),
        })
    out.sort(key=lambda x: x["review_count"], reverse=True)
    return out


def direction_breakdown(rows_: list[dict]) -> list[dict[str, Any]]:
    out = []
    for d, bucket in break_by(rows_, "direction_classification").items():
        rev_b = reviewed(bucket)
        inv_b = with_invalidation(bucket)
        out.append({
            "direction_classification": d,
            "review_count": len(bucket),
            "reviewed_outcomes_count": len(rev_b),
            "avg_confidence": avg_conf_str(bucket),
            "correct_rate": correct_rate(rev_b),
            "invalidation_rate": invalidation_rate(inv_b),
            "most_common_failure_reason": most_common_failure(rev_b),
        })
    out.sort(key=lambda x: x["review_count"], reverse=True)
    return out


def bucket_breakdown(rows_: list[dict]) -> list[dict[str, Any]]:
    out = []
    for b, bucket in break_by(rows_, "confidence_bucket").items():
        rev_b = reviewed(bucket)
        inv_b = with_invalidation(bucket)
        out.append({
            "confidence_bucket": b,
            "review_count": len(bucket),
            "reviewed_outcomes_count": len(rev_b),
            "avg_confidence": avg_conf_str(bucket),
            "correct_rate": correct_rate(rev_b),
            "invalidation_rate": invalidation_rate(inv_b),
            "most_common_failure_reason": most_common_failure(rev_b),
        })
    out.sort(key=lambda x: bucket_sort_key(x["confidence_bucket"]))
    return out


def failure_breakdown_table(rows_: list[dict]) -> list[dict[str, Any]]:
    d = failure_dist(rows_)
    total_fails = sum(d.values())
    out = []
    for reason, cnt in sorted(d.items(), key=lambda x: x[1], reverse=True):
        out.append({
            "failure_reason": reason,
            "count": cnt,
            "rate": safe_rate(cnt, total_fails) if total_fails else "N/A",
        })
    return out


# ── Observations ─────────────────────────────────────────────────────────────

def make_drift_notes(rows_: list[dict], summary: dict) -> list[str]:
    notes: list[str] = []
    if not rows_:
        notes.append("- No reviews logged this month.")
        return notes

    total = summary["total_reviews"]

    # Sample size warning
    if total < 10:
        notes.append(f"- ⚠️ Sample size low ({total} reviews). Conclusions may not be statistically meaningful.")

    # Pending
    if summary["pending"] > 0:
        pct = summary["pending"] / total * 100
        notes.append(f"- ⚠️ {summary['pending']} reviews ({pct:.0f}%) have pending outcomes — affects calibration accuracy.")

    # Confidence calibration
    bucket_b = bucket_breakdown(rows_)
    with_correct = {b["confidence_bucket"]: b["correct_rate"]
                    for b in bucket_b if b["correct_rate"] != "N/A"}
    if with_correct:
        best_b = max(with_correct, key=lambda k: float(with_correct[k].rstrip("%")))
        worst_b = min(with_correct, key=lambda k: float(with_correct[k].rstrip("%")))
        if len(with_correct) > 1:
            notes.append(
                f"- Most reliable confidence bucket: **{best_b}** ({with_correct[best_b]} correct). "
                f"Least reliable: **{worst_b}** ({with_correct[worst_b]} correct)."
            )

    # Session performance
    sess_b = session_breakdown(rows_)
    sess_correct = {s["session"]: s["correct_rate"]
                    for s in sess_b if s["correct_rate"] != "N/A"}
    if len(sess_correct) > 1:
        best_s = max(sess_correct, key=lambda k: float(sess_correct[k].rstrip("%")))
        worst_s = min(sess_correct, key=lambda k: float(sess_correct[k].rstrip("%")))
        notes.append(
            f"- Best session: **{best_s}** ({sess_correct[best_s]}). "
            f"Worst: **{worst_s}** ({sess_correct[worst_s]})."
        )

    # MA200 availability impact
    insuf = summary["insufficient_context_count"]
    if insuf > 0:
        insuf_rows = [r for r in rows_
                      if r.get("insufficient_context", "").strip().lower() == "true"]
        insuf_rev = reviewed(insuf_rows)
        if insuf_rev:
            rate = correct_rate(insuf_rev)
            total_rev = len(reviewed(rows_))
            if total_rev > 0:
                notes.append(
                    f"- Insufficient_context rows ({insuf}): correct_rate = **{rate}** "
                    f"({len(insuf_rev)}/{total_rev} with outcomes)."
                )
        else:
            notes.append(f"- **{insuf}** rows had insufficient_context (MA200 unavailable); no outcomes to compare.")

    # Direction invalidation
    dir_b = direction_breakdown(rows_)
    dir_inv: dict[str, str] = {}
    for d in dir_b:
        if d["invalidation_rate"] != "N/A":
            dir_inv[d["direction_classification"]] = d["invalidation_rate"]
    if dir_inv:
        worst_inv = max(dir_inv, key=lambda k: float(dir_inv[k].rstrip("%")))
        notes.append(f"- Highest invalidation rate by direction: **{worst_inv}** ({dir_inv[worst_inv]}).")

    # Outcome distribution
    total_rev = summary["reviewed"]
    if total_rev > 0:
        correct_pct = summary["correct"] / total_rev * 100
        wrong_pct = summary["wrong"] / total_rev * 100
        notes.append(f"- Outcome split: {summary['correct']} correct ({correct_pct:.0f}%), "
                     f"{summary['wrong']} wrong ({wrong_pct:.0f}%), "
                     f"{summary['invalidated']} invalidated.")
    return notes


# ── Rule change candidates ─────────────────────────────────────────────────────

def rule_change_candidates(rows_: list[dict], summary: dict) -> list[str]:
    candidates: list[tuple[str, str]] = []

    # Confidence bucket reliability
    bucket_b = bucket_breakdown(rows_)
    bucket_correct = {b["confidence_bucket"]: b["correct_rate"]
                      for b in bucket_b if b["correct_rate"] != "N/A"}
    if bucket_correct:
        worst_b = min(bucket_correct, key=lambda k: float(bucket_correct[k].rstrip("%")))
        worst_rate = float(bucket_correct[worst_b].rstrip("%"))
        if len(bucket_b) > 1 and worst_rate < 40:
            candidates.append(
                (worst_b,
                 f"Bucket {worst_b} has {bucket_correct[worst_b]} correct rate. "
                 f"Consider requiring higher confidence threshold for this bucket "
                 f"or excluding it from actionable signals.")
            )

    # Insufficient context
    if summary["insufficient_context_count"] > 0:
        insuf_rows = [r for r in rows_
                      if r.get("insufficient_context", "").strip().lower() == "true"]
        insuf_rev = reviewed(insuf_rows)
        if insuf_rev:
            rate = correct_rate(insuf_rev)
            r = float(rate.rstrip("%")) if rate != "N/A" else 50
            if r < 40:
                candidates.append(
                    ("insufficient_context rule",
                     f"Rows with insufficient_context (no MA200) have {rate} correct rate. "
                     f"Consider flagging these for manual review only.")
                )

    # Direction invalidation rate
    dir_b = direction_breakdown(rows_)
    for d in dir_b:
        if d["invalidation_rate"] != "N/A":
            r = float(d["invalidation_rate"].rstrip("%"))
            if r > 60:
                candidates.append(
                    (d["direction_classification"],
                     f"Direction '{d['direction_classification']}' has {d['invalidation_rate']} "
                     f"invalidation rate. Review whether this signal is worth acting on.")
                )

    return [f"- **{label}**: {detail}" for label, detail in candidates]


# ── Renderer ─────────────────────────────────────────────────────────────────

def render(
    month: str,
    first: str,
    last: str,
    symbol: str | None,
    rows_: list[dict],
    summary: dict,
    now_iso: str,
) -> str:
    sym_note = f" | Symbol filter: **{symbol}**" if symbol else ""
    total_reviews = summary["total_reviews"]
    sample_warn = (
        f"\n\n> ⚠️ **Sample size warning**: Only **{total_reviews}** reviews this month. "
        "Patterns may not be statistically meaningful until N ≥ 10."
        if total_reviews < 10 else ""
    )

    lines = [
        f"# Monthly Candlestick Engine Review — {month}",
        f"*Generated: {now_iso} (HKT)*",
        sample_warn,
        "",
        "## Period",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| month | {month} |",
        f"| week_start | {first} |",
        f"| week_end | {last} |{sym_note}",
        "",
        "## Summary",
        f"| Metric | Value |",
        f"| --- | --- |",
        f"| total_reviews | {summary['total_reviews']} |",
        f"| reviewed_outcomes | {summary['reviewed']} |",
        f"| pending_outcomes | {summary['pending']} |",
        f"| correct | {summary['correct']} |",
        f"| partially_correct | {summary['partially_correct']} |",
        f"| wrong | {summary['wrong']} |",
        f"| invalidated | {summary['invalidated']} |",
        f"| no_follow_through | {summary['no_follow_through']} |",
        f"| avg_confidence | {summary['avg_confidence']} |",
        f"| median_confidence | {summary['median_confidence']} |",
        f"| insufficient_context_count | {summary['insufficient_context_count']} |",
        f"| insufficient_context_rate | {summary['insufficient_context_rate']} |",
        f"| ma200_available_rate | {summary['ma200_available_rate']} |",
        "",
    ]

    if not rows_:
        lines += [
            "## No Data",
            "No reviews found for this month.",
            "Log reviews with `scripts/log_engine_review.py` before generating this report.",
            "",
        ]
    else:
        # Session breakdown
        sess = session_breakdown(rows_)
        lines += [
            "## Session Breakdown",
            f"| session | review_count | reviewed_outcomes | avg_conf | correct_rate | invalidation_rate |",
            f"| --- | --- | --- | --- | --- | --- |",
        ]
        for r in sess:
            lines.append(
                f"| {r['session']} | {r['review_count']} | {r['reviewed_outcomes_count']} | "
                f"{r['avg_confidence']} | {r['correct_rate']} | {r['invalidation_rate']} |"
            )
        lines.append("")

        # Direction breakdown
        dir_b = direction_breakdown(rows_)
        lines += [
            "## Direction Breakdown",
            f"| direction | review_count | reviewed_outcomes | avg_conf | correct_rate | invalidation_rate | most_common_failure |",
            f"| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in dir_b:
            lines.append(
                f"| {r['direction_classification']} | {r['review_count']} | {r['reviewed_outcomes_count']} | "
                f"{r['avg_confidence']} | {r['correct_rate']} | {r['invalidation_rate']} | "
                f"{r['most_common_failure_reason']} |"
            )
        lines.append("")

        # Confidence calibration
        buck = bucket_breakdown(rows_)
        lines += [
            "## Confidence Calibration",
            f"| bucket | review_count | reviewed | avg_conf | correct_rate | invalidation_rate | top_failure |",
            f"| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for r in buck:
            lines.append(
                f"| {r['confidence_bucket']} | {r['review_count']} | {r['reviewed_outcomes_count']} | "
                f"{r['avg_confidence']} | {r['correct_rate']} | {r['invalidation_rate']} | "
                f"{r['most_common_failure_reason']} |"
            )
        lines.append("")

        # Failure reasons
        fail_rows = failure_breakdown_table(reviewed(rows_))
        lines += [
            "## Failure Reasons",
            f"| failure_reason | count | rate |",
            f"| --- | --- | --- |",
        ]
        if fail_rows:
            for fr in fail_rows:
                lines.append(f"| {fr['failure_reason']} | {fr['count']} | {fr['rate']} |")
        else:
            lines.append("| _No failure_reason data yet._ | | |")
        lines.append("")

        # Drift / Pattern Notes
        notes = make_drift_notes(rows_, summary)
        lines += ["## Drift / Pattern Notes"]
        lines += [f"{n}" for n in notes]
        lines.append("")

        # Rule change candidates
        candidates = rule_change_candidates(rows_, summary)
        lines += ["## Rule Change Candidates"]
        if candidates:
            lines.append("_Review only. No automatic rule changes._")
            lines += candidates
        else:
            lines.append("_No strong candidates this month._")
        lines.append("")

    # Next Month Actions
    lines += [
        "## Next Month Actions",
        "_Fill in after reviewing Drift/Pattern Notes above._",
        "",
        "- Action 1: ",
        "- Action 2: ",
        "- Action 3: ",
        "",
        "---",
        "*Candlestick Direction Engine — manual-only analytical tool. "
        "Not production trading advice. For calibration review only.*",
    ]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _parse_month(raw: str) -> str:
    if len(raw) == 7 and raw[4] == "-" and raw[:4].isdigit() and raw[5:7].isdigit():
        y, m = int(raw[:4]), int(raw[5:7])
        if 1 <= m <= 12:
            return raw
    raise argparse.ArgumentTypeError(
        f"Invalid month: {raw!r}. Use format YYYY-MM (e.g. 2026-07)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a monthly Candlestick Engine calibration markdown report.",
    )
    parser.add_argument("--month", required=True, type=_parse_month,
                        help="Month in YYYY-MM format (e.g. 2026-07)")
    parser.add_argument("--input", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--output", type=Path,
                        help="Output markdown path. Auto-generated if omitted.")
    parser.add_argument("--symbol",
                        help="Filter by symbol (default: all symbols)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print markdown to stdout, do not write file")

    args = parser.parse_args()

    first, last = month_boundaries(args.month)
    rows = load_reviews(args.input)
    filtered = filter_month(rows, first, last, args.symbol)
    summary = build_summary(filtered)
    now_iso = datetime.now(_HKT).isoformat(timespec="seconds")
    md = render(args.month, first, last, args.symbol, filtered, summary, now_iso)

    if args.dry_run:
        print(md)
        return

    out_path = args.output
    if out_path is None:
        out_path = DEFAULT_OUT_DIR / f"monthly-{args.month}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"OK|{out_path}")


if __name__ == "__main__":
    main()