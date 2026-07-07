"""log_engine_review.py — append a Candlestick Direction Engine analysis to
data/engine_reviews.csv.

Usage
-----
    python scripts/log_engine_review.py --input path/to/snapshot.json
    python scripts/log_engine_review.py --input path/to/snapshot.json --dry-run
    cat path/to/snapshot.json | python scripts/log_engine_review.py

Input schema (compatible with direction_engine.build_engine_snapshot dict):
    {
        "now_hkt":          "2026-07-07T10:32:57+08:00",
        "symbol":           "GC=F",
        "period_used":      "5y",
        "primary_ok":       true,
        "provenance":       "primary",
        "bars":             1257,
        "d1_close":         4150.5,
        "d1_ma20":          4168.82,
        "d1_ma50":          4418.02,
        "d1_ma200":         4458.59,
        "sufficient_for_ma50":  true,
        "sufficient_for_ma200": true,
        "insufficient_context": false,
        // optional / extended fields from the operator's analysis
        "session":          "NY_open",       # Asia | London | NY_preopen | NY_open | Overlap | Other
        "engine_version":   "v1",
        "input_source":     "manual",         # manual | daily_inputs | yfinance_live | journal_replay | other
        "higher_tf_bias":   "bearish",        # bullish | bearish | neutral
        "direction_classification": "bearish_continuation",
        "bias":             "bearish",
        "confidence":       67,
        "support_levels":   ["4156.4","4140.1","4112.7","4062.0"],
        "resistance_levels": ["4169.46","4187.3","4215.5"],
        "invalidation_level": "4187.3",
        "candle_semantics": "seller_pressure",
        "evidence_count":   4,
        "conflict_count":   1,
        "ma200_available":  true,
        "pre_event_flag":   false,
        "initial_price":    4163.2,
        "validation_window":"4h",
        // these fields are optional at initial logging (filled later during review):
        "validation_price": null,
        "max_favorable_move": null,
        "max_adverse_move":   null,
        "invalidation_hit":   null,
        "outcome_label":      null,
        "review_score":       null,
        "failure_reason":     null,
        "notes":              null,
        "lesson":             null,
        "next_adjustment":    null,
    }

Missing optional fields will be left empty in the CSV.  Required fields are
enforced; schema validation errors prevent append and print a clear message.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from engine_ops_logger import track, log_event

_HKT = timezone(timedelta(hours=8))

# ── CSV column definition ──────────────────────────────────────────────────

HEADERS = [
    "review_id", "date", "hkt_time", "session", "symbol", "engine_version",
    "input_source", "higher_tf_bias", "direction_classification", "bias",
    "confidence", "support_levels", "resistance_levels", "invalidation_level",
    "candle_semantics", "evidence_count", "conflict_count",
    "insufficient_context", "ma200_available", "pre_event_flag",
    "initial_price", "validation_window", "validation_price",
    "max_favorable_move", "max_adverse_move", "invalidation_hit",
    "outcome_label", "confidence_bucket", "review_score", "failure_reason",
    "notes", "lesson", "next_adjustment", "created_at", "updated_at",
]

# Fields that must be present (not necessarily non-null) in the input JSON.
REQUIRED_INPUT_FIELDS = [
    "now_hkt", "symbol", "confidence",
]

# Strict enum fields and their allowed values.
ENUM_FIELD_VALUES: dict[str, list[str]] = {
    "session":                 ["Asia", "London", "NY_preopen", "NY_open", "Overlap", "Other"],
    "input_source":            ["manual", "daily_inputs", "yfinance_live", "journal_replay", "other"],
    "higher_tf_bias":          ["bullish", "bearish", "neutral"],
    "bias":                    ["bullish", "bearish", "neutral"],
    "candle_semantics":        ["buyer_pressure", "seller_pressure", "mixed", "compression"],
    "validation_window":       ["30m", "1h", "4h", "session_close", "next_day", "NY_open", "custom"],
    "outcome_label":           ["correct", "partially_correct", "wrong", "invalidated", "no_follow_through"],
    "failure_reason":          ["none", "low_liquidity_noise", "wrong_htf_bias",
                               "wrong_level", "late_entry_context", "news_distortion",
                               "overconfident_score", "insufficient_context", "other"],
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def confidence_bucket(score: int | None) -> str:
    if score is None:
        return "N/A"
    if score < 40:
        return "0-39"
    if score < 55:
        return "40-54"
    if score < 70:
        return "55-69"
    if score < 85:
        return "70-84"
    return "85-100"


def _parse_hkt_now(raw: str | None) -> tuple[str, str]:
    """Return (date_hkt, hkt_time) from an ISO timestamp string."""
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            # Convert to HKT
            dt_hkt = dt.astimezone(_HKT)
            return dt_hkt.strftime("%Y-%m-%d"), dt_hkt.strftime("%H:%M:%S")
        except ValueError:
            pass
    now = datetime.now(_HKT)
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")


def _fmt(v: Any) -> str:
    """Convert value to safe CSV string."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ";".join(str(x) for x in v)
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def _check_enum(key: str, value: str | None) -> None:
    if value is None:
        return
    allowed = ENUM_FIELD_VALUES.get(key)
    if allowed and value not in allowed:
        raise ValueError(
            f"Field '{key}' has illegal value '{value}'. "
            f"Allowed: {allowed}"
        )


def _build_row(data: dict, dry_run: bool = False) -> dict:
    """Map input JSON to a CSV row dict, validating required + enum fields."""
    missing = [f for f in REQUIRED_INPUT_FIELDS if f not in data]
    if missing:
        raise ValueError(f"Missing required input fields: {missing}")

    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 100):
        raise ValueError(
            f"'confidence' must be numeric 0-100; got {conf!r}"
        )

    for enum_key in ENUM_FIELD_VALUES:
        raw = data.get(enum_key)
        if raw is not None:
            _check_enum(enum_key, str(raw))

    date_hkt, hkt_time = _parse_hkt_now(data.get("now_hkt"))
    now_iso = datetime.now(_HKT).isoformat(timespec="seconds")

    # Generate stable review_id from symbol+date+hkt_time+uuid4 suffix
    uid = uuid.uuid4().hex[:6]
    review_id = f"{data.get('symbol', 'UNK').replace('=', '-')}_{date_hkt}_{hkt_time.replace(':', '')}_{uid}"

    # List fields that are serialised as semicolon-joined strings
    list_fields = {"support_levels", "resistance_levels"}

    row: dict[str, str] = {}
    for col in HEADERS:
        raw = data.get(col)
        if col in list_fields and isinstance(raw, list):
            row[col] = ";".join(str(x) for x in raw)
        elif raw is None:
            row[col] = ""
        elif col in ("insufficient_context", "ma200_available", "pre_event_flag",
                     "primary_ok"):
            row[col] = str(bool(raw)).lower()
        elif col in ("evidence_count", "confidence", "review_score",
                     "initial_price", "d1_close", "d1_ma20", "d1_ma50",
                     "d1_ma200", "bars",
                     "max_favorable_move", "max_adverse_move", "validation_price"):
            row[col] = str(float(raw)) if raw is not None else ""
        else:
            row[col] = str(raw) if raw is not None else ""

    # Override auto-computed fields
    row["review_id"]           = review_id
    row["date"]                = date_hkt
    row["hkt_time"]            = hkt_time
    row["confidence_bucket"]   = confidence_bucket(
        int(conf) if conf is not None else None
    )
    row["created_at"]          = now_iso
    row["updated_at"]          = now_iso

    # Map extended fields that the input JSON may use different names for
    row["engine_version"]      = str(data.get("engine_version", "v1"))
    row["input_source"]        = str(data.get("input_source", "manual"))
    row["session"]             = str(data.get("session", "Other"))
    row["higher_tf_bias"]      = str(data.get("higher_tf_bias", "neutral"))
    row["direction_classification"] = str(data.get("direction_classification", ""))
    row["bias"]                = str(data.get("bias", "neutral"))
    row["invalidation_level"]  = _fmt(data.get("invalidation_level"))
    row["candle_semantics"]    = str(data.get("candle_semantics", "mixed"))
    row["evidence_count"]      = str(data.get("evidence_count", ""))
    row["conflict_count"]      = str(data.get("conflict_count", ""))
    row["pre_event_flag"]      = str(bool(data.get("pre_event_flag"))).lower()
    row["validation_window"]   = str(data.get("validation_window", ""))

    # Validation / outcome fields — leave empty on initial log
    for col in ["validation_price", "max_favorable_move", "max_adverse_move",
                "invalidation_hit", "outcome_label", "review_score",
                "failure_reason", "notes", "lesson", "next_adjustment"]:
        if col not in data:
            row[col] = ""

    return row


# ── CSV I/O ────────────────────────────────────────────────────────────────

def _get_csv_path() -> Path:
    base = Path.home() / "projects" / "daily-xauusd-bot"
    return base / "data" / "engine_reviews.csv"


def ensure_csv(path: Path) -> None:
    """Create the CSV with headers if it does not exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()


def append_review(row: dict, dry_run: bool = False) -> dict:
    """Append `row` dict to the CSV, or raise on error. Returns result info."""
    path = _get_csv_path()
    ensure_csv(path)

    # Duplicate detection: check if a row with same (symbol, date, hkt_time, session)
    # already exists.  If so, reject to avoid double-logging.
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing = {(r["symbol"], r["date"], r["hkt_time"], r.get("session", ""))
                    for r in reader if r.get("date")}

    key = (row.get("symbol", ""), row.get("date", ""),
           row.get("hkt_time", ""), row.get("session", ""))
    if key in existing:
        raise ValueError(
            f"Duplicate entry rejected: symbol={key[0]} date={key[1]} "
            f"hkt_time={key[2]} session={key[3]} already exists in CSV. "
            f"Use a unique timestamp or remove the existing row first."
        )

    if dry_run:
        return {"success": True, "dry_run": True,
                "review_id": row["review_id"], "path": str(path), "row": row}

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writerow(row)

    return {"success": True, "review_id": row["review_id"], "path": str(path)}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _load_input(src: str | None) -> dict:
    if src and src != "-":
        with open(src, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Append a Candlestick Direction Engine analysis to engine_reviews.csv."
    )
    parser.add_argument(
        "--input", "-i", dest="input_path",
        help="Path to JSON snapshot file. Use '-' to read from stdin.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and print the row without writing to CSV.",
    )
    args = parser.parse_args()

    ev_metadata = {"dry_run": args.dry_run}
    result = None
    errored = False

    try:
        with track(script_name="log_engine_review.py", metadata=ev_metadata) as ev:
            try:
                data = _load_input(args.input_path)
            except json.JSONDecodeError as e:
                error_type = "JSONDecodeError"
                error_msg = f"Invalid JSON input: {e}"
                sys.stderr.write(f"ERROR: {error_msg}\n")
                errored = True
                # track __exit__ will re-raise — log it as error there
                raise RuntimeError(error_msg) from e
            except FileNotFoundError as e:
                error_type = "FileNotFoundError"
                error_msg = f"File not found: {e}"
                sys.stderr.write(f"ERROR: {error_msg}\n")
                errored = True
                raise RuntimeError(error_msg) from e

            try:
                row = _build_row(data, dry_run=args.dry_run)
                result = append_review(row, dry_run=args.dry_run)
            except ValueError as e:
                sys.stderr.write(f"VALIDATION ERROR: {e}\n")
                errored = True
                raise RuntimeError(str(e)) from e

            ev.update(
                review_id=result["review_id"],
                output_path=str(result["path"]),
                rows_affected=0 if args.dry_run else 1,
                status="success",
            )
    except RuntimeError:
        # Error already handled / stderr written above; re-raise so we exit 1
        pass

    if errored:
        sys.exit(1)

    # Success path
    if args.dry_run:
        print(f"[DRY RUN] review_id: {result['review_id']}")
        print(f"[DRY RUN] CSV path:  {result['path']}")
        print(f"[DRY RUN] Row preview:")
        for k, v in result["row"].items():
            print(f"  {k}: {v!r}")
    else:
        print(f"OK|{result['review_id']}|{result['path']}")


if __name__ == "__main__":
    main()