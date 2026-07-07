#!/usr/bin/env python3
"""query_engine_ops.py — query & summary CLI for Candlestick Direction Engine observability events."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

DEFAULT_INPUT = "data/engine_ops_events.jsonl"


def load_events(path, days, script_name, status):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    events = []
    invalid = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1
                    continue
                if ev.get("event_type") != "script_run":
                    continue
                fa = ev.get("finished_at")
                if not fa:
                    continue
                try:
                    finished_at = datetime.fromisoformat(fa.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    invalid += 1
                    continue
                if finished_at < cutoff:
                    continue
                if script_name and ev.get("script_name") != script_name:
                    continue
                if status and ev.get("status") != status:
                    continue
                events.append(ev)
    except FileNotFoundError:
        print(f"ERROR: input file not found: {path}", file=sys.stderr)
        return [], 0
    return events, invalid


def percentile(values, pct):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def build_summary(events, invalid_lines):
    total = len(events)
    starts = [e["started_at"] for e in events if e.get("started_at")]
    ends = [e["finished_at"] for e in events if e.get("finished_at")]
    success = sum(1 for e in events if e.get("status") == "success")
    error = sum(1 for e in events if e.get("status") == "error")
    durations = [float(e.get("duration_ms", 0)) for e in events if e.get("duration_ms") is not None]

    scripts = {}
    for e in events:
        s = e.get("script_name", "unknown")
        if s not in scripts:
            scripts[s] = {"count": 0, "success": 0, "error": 0, "max_ms": 0, "durations_sum": 0.0}
        sc = scripts[s]
        sc["count"] += 1
        if e.get("status") == "success":
            sc["success"] += 1
        elif e.get("status") == "error":
            sc["error"] += 1
        d = e.get("duration_ms")
        if d is not None:
            sc["durations_sum"] += float(d)
            sc["max_ms"] = max(sc["max_ms"], float(d))

    error_types = Counter()
    latest_by_type = {}
    for e in events:
        if e.get("status") != "error":
            continue
        et = e.get("error_type") or "unknown"
        error_types[et] += 1
        cur = latest_by_type.get(et)
        if cur is None or (e.get("finished_at") or "") > (cur.get("finished_at") or ""):
            latest_by_type[et] = e

    script_view = {}
    for name, sc in scripts.items():
        c = sc["count"]
        avg = (sc["durations_sum"] / c) if c else 0.0
        script_view[name] = {
            "count": c,
            "success": sc["success"],
            "error": sc["error"],
            "avg_ms": round(avg, 1),
            "max_ms": sc["max_ms"],
        }

    return {
        "time_window": {
            "start": starts[0] if starts else None,
            "end": ends[-1] if ends else None,
            "days_filter": None,
            "total_events": total,
            "invalid_lines_skipped": invalid_lines,
        },
        "status_counts": {
            "success": success,
            "error": error,
            "success_rate": round((success / total) * 100, 1) if total else 0,
            "error_rate": round((error / total) * 100, 1) if total else 0,
        },
        "script_counts": script_view,
        "error_types": dict(error_types.most_common()),
        "duration": {
            "avg_ms": round(sum(durations) / len(durations), 1) if durations else 0,
            "p50_ms": round(percentile(durations, 50) or 0, 1) if durations else 0,
            "p95_ms": round(percentile(durations, 95) or 0, 1) if durations else 0,
            "max_ms": max(durations) if durations else 0,
        },
        "latest_error_per_type": {
            et: {
                "finished_at": e.get("finished_at"),
                "script_name": e.get("script_name"),
                "error_message": e.get("error_message"),
            }
            for et, e in latest_by_type.items()
        },
    }


def format_text_summary(s):
    tw = s["time_window"]
    sc = s["status_counts"]
    dur = s["duration"]
    out = []
    out.append("Engine Ops Summary")
    out.append(f"Window: {tw['start']} -> {tw['end']}")
    out.append(f"Events: {tw['total_events']}")
    out.append(f"Success: {sc['success']} ({sc['success_rate']}%)")
    out.append(f"Error: {sc['error']} ({sc['error_rate']}%)")
    out.append(f"Invalid lines skipped: {tw['invalid_lines_skipped']}")
    out.append("")
    if s["script_counts"]:
        out.append("By Script")
        for name, d in s["script_counts"].items():
            out.append(f"- {name}: count={d['count']}, success={d['success']}, error={d['error']}, avg_ms={d['avg_ms']}, max_ms={d['max_ms']}")
        out.append("")
    if s["error_types"]:
        out.append("Top Error Types")
        for et, c in s["error_types"].items():
            out.append(f"- {et}: {c}")
        out.append("")
    out.append("Duration")
    out.append(f"- avg_ms: {dur['avg_ms']}")
    out.append(f"- p50_ms: {dur['p50_ms']}")
    out.append(f"- p95_ms: {dur['p95_ms']}")
    out.append(f"- max_ms: {dur['max_ms']}")
    return "\n".join(out)


def format_text_recent(events, limit):
    out = ["Recent Events"]
    recent = sorted(events, key=lambda e: e.get("finished_at") or "", reverse=True)[:limit]
    for e in recent:
        out.append(
            f"- {e.get('finished_at')} | {e.get('script_name')} | {e.get('status')} | "
            f"{e.get('duration_ms')}ms | rid={e.get('review_id') or '-'} | "
            f"out={e.get('output_path') or '-'} | et={e.get('error_type') or '-'}"
        )
    return "\n".join(out)


def format_text_errors(events):
    errs = [e for e in events if e.get("status") == "error"]
    out = ["Error Breakdown"]
    if not errs:
        out.append("- (none)")
        return "\n".join(out)
    by_type = {}
    for e in errs:
        et = e.get("error_type") or "unknown"
        by_type.setdefault(et, []).append(e)
    for et, group in by_type.items():
        latest = max(group, key=lambda x: x.get("finished_at") or "")
        out.append(
            f"- {et}: count={len(group)}, latest_finished_at={latest.get('finished_at')}, "
            f"latest_script={latest.get('script_name')}"
        )
    return "\n".join(out)


def format_text_slowest(events, limit):
    out = ["Slowest Events"]
    ranked = sorted(events, key=lambda e: e.get("duration_ms") or 0, reverse=True)[:limit]
    for e in ranked:
        out.append(
            f"- {e.get('finished_at')} | {e.get('script_name')} | "
            f"{e.get('duration_ms')}ms | {e.get('status')} | rid={e.get('review_id') or '-'} | "
            f"out={e.get('output_path') or '-'}"
        )
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Query engine ops events")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--script-name")
    parser.add_argument("--status", choices=["success", "error"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--recent", action="store_true")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--errors", action="store_true")
    parser.add_argument("--slowest", action="store_true")
    args = parser.parse_args()

    events, invalid = load_events(args.input, args.days, args.script_name, args.status)

    if args.format == "json":
        s = build_summary(events, invalid)
        print(json.dumps(s, indent=2, ensure_ascii=False))
        return

    if args.recent:
        print(format_text_recent(events, args.limit))
    elif args.errors:
        print(format_text_errors(events))
    elif args.slowest:
        print(format_text_slowest(events, args.limit))
    else:
        s = build_summary(events, invalid)
        print(format_text_summary(s))


if __name__ == "__main__":
    main()
