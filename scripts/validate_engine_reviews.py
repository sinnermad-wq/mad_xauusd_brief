#!/usr/bin/env python3
"""validate_engine_reviews.py — read-only data-quality validation for engine_reviews.csv.

Checks:
  - missing required fields
  - session / direction / outcome enum validity
  - confidence range (0-100)
  - pending outcomes count (empty outcome_label)
  - stale rows (not updated in N+ days)
  - duplicate review_id
  - sample size for weekly (>=3) / monthly (>=12) reports

Manual-only; does NOT modify the CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

DEFAULT_INPUT = "data/engine_reviews.csv"

VALID_SESSIONS = {
    "asian", "london", "new_york",
    "ny_open", "ny_close", "london_open", "asian_session",
    "overlap",
}
VALID_DIRECTIONS = {
    "bullish", "bearish", "neutral",
    "bullish_reversal", "bearish_reversal",
    "bullish_continuation", "bearish_continuation",
    "continuation", "reversal",
}
VALID_OUTCOMES = {"correct", "wrong", "partial", "pending", ""}  # empty = pending
VALID_BUCKETS = {"0-39", "40-54", "55-69", "70-84", "85-100", "very_low", "low", "medium", "high", "very_high"}
REQUIRED_FIELDS = [
    "review_id", "symbol", "session", "direction_classification",
    "bias", "confidence", "confidence_bucket", "invalidation_level",
    "created_at", "outcome_label",
]


def parse_args():
    p = argparse.ArgumentParser(description="Validate engine_reviews.csv data quality")
    p.add_argument("--input", default=DEFAULT_INPUT)
    p.add_argument("--days", type=int, default=30,
                   help="Flag rows not updated in last N days as stale")
    p.add_argument("--symbol", help="Filter by symbol (exact match)")
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero on any critical issue")
    p.add_argument("--format", choices=["text", "json"], default="text")
    return p.parse_args()


def load_csv(path: str) -> List[Dict[str, str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(2)


def check_missing_fields(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    issues = []
    for i, row in enumerate(rows, 1):
        missing = [f for f in REQUIRED_FIELDS if not row.get(f, "").strip()]
        if missing:
            issues.append({"row": i, "review_id": row.get("review_id", "—"), "missing": missing})
    return {"count": len(issues), "rows": issues[:20]}


def check_enums(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    issues = []
    for i, row in enumerate(rows, 1):
        row_issues = {}
        s = row.get("session", "").strip().lower()
        if s and s not in VALID_SESSIONS:
            row_issues["session"] = s
        d = row.get("direction_classification", "").strip().lower()
        if d and d not in VALID_DIRECTIONS:
            row_issues["direction_classification"] = d
        o = row.get("outcome_label", "").strip().lower()
        if o and o not in VALID_OUTCOMES:
            row_issues["outcome_label"] = o
        b = row.get("confidence_bucket", "").strip()
        if b and b not in VALID_BUCKETS:
            row_issues["confidence_bucket"] = b
        if row_issues:
            issues.append({"row": i, "review_id": row.get("review_id", "—"), "invalid_enums": row_issues})
    return {"count": len(issues), "rows": issues[:20]}


def check_confidence(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    issues = []
    for i, row in enumerate(rows, 1):
        c = row.get("confidence", "").strip()
        if not c:
            issues.append({"row": i, "review_id": row.get("review_id", "—"), "confidence": "MISSING"})
            continue
        try:
            v = float(c)
            if v < 0 or v > 100:
                issues.append({"row": i, "review_id": row.get("review_id", "—"), "confidence": v})
        except ValueError:
            issues.append({"row": i, "review_id": row.get("review_id", "—"), "confidence": f"NOT_A_NUMBER: {c}"})
    return {"count": len(issues), "rows": issues[:20]}


def check_duplicate_ids(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    seen: Dict[str, List[int]] = {}
    for i, row in enumerate(rows, 1):
        rid = row.get("review_id", "").strip()
        if rid:
            seen.setdefault(rid, []).append(i)
    dups = {rid: rows for rid, rows in seen.items() if len(rows) > 1}
    return {
        "count": len(dups),
        "duplicates": [{"review_id": k, "rows": v} for k, v in list(dups.items())[:20]],
    }


def check_pending_outcomes(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    pending = []
    for i, row in enumerate(rows, 1):
        o = row.get("outcome_label", "").strip()
        if not o or o == "pending":
            pending.append({
                "row": i,
                "review_id": row.get("review_id", "—"),
                "created_at": row.get("created_at", "—"),
            })
    return {"count": len(pending), "rows": pending[:20]}


def check_stale(rows: List[Dict[str, str]], days: int) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    for i, row in enumerate(rows, 1):
        ua = row.get("updated_at", "").strip()
        if not ua:
            # Use created_at as fallback
            ua = row.get("created_at", "").strip()
        if not ua:
            continue
        try:
            # Try ISO format with timezone
            if "+" in ua or ua.endswith("Z"):
                dt = datetime.fromisoformat(ua.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(ua)
            if dt.replace(tzinfo=timezone.utc) < cutoff:
                stale.append({
                    "row": i,
                    "review_id": row.get("review_id", "—"),
                    "updated_at": ua,
                    "days_since_update": (datetime.now(timezone.utc) - dt.replace(tzinfo=timezone.utc)).days,
                })
        except ValueError:
            pass
    return {"count": len(stale), "rows": stale[:20], "days_threshold": days}


def check_sample_size(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    total = len(rows)
    pending = sum(1 for r in rows if not r.get("outcome_label", "").strip() or r.get("outcome_label", "").strip() == "pending")
    resolved = total - pending
    issues = []
    if total < 3:
        issues.append(f"total rows ({total}) < 3 — insufficient for weekly report")
    if total < 12:
        issues.append(f"total rows ({total}) < 12 — insufficient for monthly report")
    return {
        "total": total,
        "pending_outcomes": pending,
        "resolved_outcomes": resolved,
        "issues": issues,
    }


def run_all_checks(input_path: str, days: int, symbol: Optional[str]) -> Dict[str, Any]:
    rows = load_csv(input_path)

    # Apply symbol filter if given
    if symbol:
        rows = [r for r in rows if r.get("symbol", "").strip() == symbol]

    return {
        "input": input_path,
        "symbol_filter": symbol,
        "total_rows": len(rows),
        "stale_threshold_days": days,
        "missing_fields": check_missing_fields(rows),
        "invalid_enums": check_enums(rows),
        "invalid_confidence": check_confidence(rows),
        "duplicate_ids": check_duplicate_ids(rows),
        "pending_outcomes": check_pending_outcomes(rows),
        "stale_rows": check_stale(rows, days),
        "sample_size": check_sample_size(rows),
    }


def is_critical(result: Dict[str, Any]) -> bool:
    """Return True if any critical issue is found."""
    checks = [
        result.get("duplicate_ids", {}).get("count", 0) > 0,
        result.get("invalid_confidence", {}).get("count", 0) > 0,
        result.get("missing_fields", {}).get("count", 0) > 0,
    ]
    return any(checks)


def format_text(result: Dict[str, Any]) -> str:
    lines = []
    lines.append("Engine Reviews Validation")
    lines.append(f"Input: {result['input']}")
    if result.get("symbol_filter"):
        lines.append(f"Symbol filter: {result['symbol_filter']}")
    lines.append(f"Total rows: {result['total_rows']}")
    lines.append(f"Stale threshold: {result['stale_threshold_days']} days")
    lines.append("")

    # Missing fields
    mf = result["missing_fields"]
    lines.append(f"[{'FAIL' if mf['count'] else 'OK'}] Missing required fields: {mf['count']}")
    if mf["count"]:
        for r in mf["rows"]:
            lines.append(f"  row {r['row']} ({r['review_id']}): missing {r['missing']}")

    # Enums
    ie = result["invalid_enums"]
    lines.append(f"[{'FAIL' if ie['count'] else 'OK'}] Invalid enum values: {ie['count']}")
    if ie["count"]:
        for r in ie["rows"]:
            lines.append(f"  row {r['row']} ({r['review_id']}): {r['invalid_enums']}")

    # Confidence
    ic = result["invalid_confidence"]
    lines.append(f"[{'FAIL' if ic['count'] else 'OK'}] Invalid confidence range: {ic['count']}")
    if ic["count"]:
        for r in ic["rows"]:
            lines.append(f"  row {r['row']} ({r['review_id']}): confidence={r['confidence']}")

    # Duplicates
    dup = result["duplicate_ids"]
    lines.append(f"[{'FAIL' if dup['count'] else 'OK'}] Duplicate review_id: {dup['count']}")
    if dup["count"]:
        for d in dup["duplicates"]:
            lines.append(f"  review_id={d['review_id']} appears at rows {d['rows']}")

    # Pending outcomes
    pend = result["pending_outcomes"]
    lines.append(f"[WARN] Pending outcomes (no outcome_label): {pend['count']}")
    if pend["count"]:
        for r in pend["rows"][:5]:
            lines.append(f"  row {r['row']} ({r['review_id']}) created={r['created_at']}")

    # Stale
    stale = result["stale_rows"]
    lines.append(f"[{'WARN' if stale['count'] else 'OK'}] Stale rows (> {stale['days_threshold']} days): {stale['count']}")
    if stale["count"]:
        for r in stale["rows"][:5]:
            lines.append(f"  row {r['row']} ({r['review_id']}): updated={r['updated_at']} ({r['days_since_update']}d ago)")

    # Sample size
    ss = result["sample_size"]
    ok_sample = len(ss["issues"]) == 0
    lines.append(f"[{'FAIL' if not ok_sample else 'OK'}] Sample size: total={ss['total']}, resolved={ss['resolved_outcomes']}, pending={ss['pending_outcomes']}")
    if ss["issues"]:
        for iss in ss["issues"]:
            lines.append(f"  - {iss}")

    lines.append("")
    critical = is_critical(result)
    lines.append(f"CRITICAL: {'YES' if critical else 'no'} {'(non-zero exit in --strict mode)' if critical else '(all clear)'}")
    return "\n".join(lines)


def main():
    args = parse_args()
    result = run_all_checks(args.input, args.days, args.symbol)

    if args.format == "json":
        output = json.dumps(result, indent=2, ensure_ascii=False)
        print(output)
        if args.strict and is_critical(result):
            sys.exit(1)
        return

    print(format_text(result))
    if args.strict and is_critical(result):
        sys.exit(1)


if __name__ == "__main__":
    main()