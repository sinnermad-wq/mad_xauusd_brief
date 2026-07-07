"""update_engine_review.py — update an existing engine review's outcome fields.

Usage
-----
    # Full outcome update
    python scripts/update_engine_review.py \
        --review-id GC-F_2026-07-07_103257_14afec \
        --validation-price 4168.3 \
        --max-favorable-move 22.1 \
        --max-adverse-move 8.4 \
        --invalidation-hit false \
        --outcome-label correct \
        --review-score 4 \
        --failure-reason none \
        --lesson "Invalidation at 4187.3 held; bearish_continuation confirmed" \
        --next-adjustment "Monitor NY_close candle for potential reversal signal" \
        --notes "Confidence 67 held; M15 wick pattern was reliable"

    # Partial update (only set what you know)
    python scripts/update_engine_review.py \
        --review-id GC-F_2026-07-07_103257_14afec \
        --outcome-label correct \
        --review-score 4

    # Force overwrite even if outcome_label already set
    python scripts/update_engine_review.py --review-id ... --outcome-label wrong --force

This is step 2 of the review workflow (step 1 = log_engine_review.py).
The script does NOT auto-schedule; it runs on demand after the validation
window expires.
"""

from __future__ import annotations

import argparse
import csv
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_HKT = timezone(timedelta(hours=8))

# ── Enums ────────────────────────────────────────────────────────────────────

OUTCOME_LABELS = {"correct", "partially_correct", "wrong", "invalidated", "no_follow_through"}
FAILURE_REASONS = {
    "none", "low_liquidity_noise", "wrong_htf_bias", "wrong_level",
    "late_entry_context", "news_distortion", "overconfident_score",
    "insufficient_context", "other",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _hkt_now() -> str:
    return datetime.now(_HKT).isoformat(timespec="seconds")


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def _write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    # Write to temp file then rename for atomic replace
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _find_row(rows: list[dict], review_id: str) -> int:
    for i, r in enumerate(rows):
        if r.get("review_id") == review_id:
            return i
    raise ValueError(f"review_id not found: {review_id!r}")


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


# ── Validation ────────────────────────────────────────────────────────────────

class ValidationError(Exception):
    pass


def _check_enum(label: str, value: str | None, allowed: set[str], field: str) -> None:
    if value is None:
        return
    if value not in allowed:
        raise ValidationError(
            f"--{field}={value!r} not in allowed values: {sorted(allowed)}"
        )


def _parse_bool(raw: str, field: str) -> bool:
    low = raw.lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    raise ValidationError(
        f"--{field} must be true/false/1/0/yes/no; got {raw!r}"
    )


def _parse_float(raw: str | None, field: str) -> float | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValidationError(f"--{field} must be a number; got {raw!r}")


def _parse_int(raw: str | None, field: str) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    try:
        v = float(raw)
        if v != int(v):
            raise ValidationError(f"--{field} must be an integer; got {raw!r}")
        return int(v)
    except ValueError:
        raise ValidationError(f"--{field} must be an integer; got {raw!r}")


# ── Main update logic ────────────────────────────────────────────────────────

def update_review(
    review_id: str,
    validation_price: str | None,
    max_favorable_move: str | None,
    max_adverse_move: str | None,
    invalidation_hit: str | None,
    outcome_label: str | None,
    review_score: str | None,
    failure_reason: str | None,
    lesson: str | None,
    next_adjustment: str | None,
    notes: str | None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update one row in engine_reviews.csv. Returns result dict."""
    csv_path = Path.home() / "projects" / "daily-xauusd-bot" / "data" / "engine_reviews.csv"

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    headers, rows = _read_csv(csv_path)

    # Find target row
    idx = _find_row(rows, review_id)
    row = rows[idx]

    # Check existing outcome_label
    existing_label = row.get("outcome_label", "").strip()
    if existing_label and not force:
        raise ValueError(
            f"review_id {review_id!r} already has outcome_label={existing_label!r}. "
            "Add --force to overwrite."
        )

    # Validate enums before touching anything
    _check_enum("outcome_label", outcome_label, OUTCOME_LABELS, "outcome-label")
    _check_enum("failure_reason", failure_reason, FAILURE_REASONS, "failure-reason")

    if outcome_label is not None and outcome_label not in OUTCOME_LABELS:
        raise ValidationError(f"--outcome-label={outcome_label!r} not in {sorted(OUTCOME_LABELS)}")
    if failure_reason is not None and failure_reason not in FAILURE_REASONS:
        raise ValidationError(f"--failure-reason={failure_reason!r} not in {sorted(FAILURE_REASONS)}")

    # Build updates dict (only set fields that were explicitly provided)
    updates: dict[str, str] = {}
    if validation_price is not None:
        updates["validation_price"] = _fmt(_parse_float(validation_price, "validation-price"))
    if max_favorable_move is not None:
        updates["max_favorable_move"] = _fmt(_parse_float(max_favorable_move, "max-favorable-move"))
    if max_adverse_move is not None:
        updates["max_adverse_move"] = _fmt(_parse_float(max_adverse_move, "max-adverse-move"))
    if invalidation_hit is not None:
        updates["invalidation_hit"] = _fmt(_parse_bool(invalidation_hit, "invalidation-hit"))
    if outcome_label is not None:
        updates["outcome_label"] = outcome_label
    if review_score is not None:
        v = _parse_int(review_score, "review-score")
        if v is not None and not (1 <= v <= 5):
            raise ValidationError(f"--review-score must be 1-5; got {v}")
        updates["review_score"] = _fmt(v)
    if failure_reason is not None:
        updates["failure_reason"] = failure_reason
    if lesson is not None:
        updates["lesson"] = lesson
    if next_adjustment is not None:
        updates["next_adjustment"] = next_adjustment
    if notes is not None:
        updates["notes"] = notes

    now_iso = _hkt_now()
    updates["updated_at"] = now_iso

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "review_id": review_id,
            "path": str(csv_path),
            "updates": updates,
            "row_preview": {k: row.get(k, "") for k in list(updates.keys())[:6]},
        }

    # Apply updates to the target row
    rows[idx].update(updates)
    _write_csv(csv_path, headers, rows)

    return {
        "success": True,
        "review_id": review_id,
        "path": str(csv_path),
        "updated_at": now_iso,
        "fields_updated": sorted(updates.keys()),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update outcome fields in data/engine_reviews.csv by review_id.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--review-id", required=True, help="Target review_id to update")
    parser.add_argument("--validation-price", help="Float: price at validation window close")
    parser.add_argument("--max-favorable-move", help="Float: max price move in favourable direction")
    parser.add_argument("--max-adverse-move", help="Float: max price move against position")
    parser.add_argument("--invalidation-hit", choices=["true","false","1","0","yes","no"],
                        help="Boolean: whether invalidation level was hit")
    parser.add_argument("--outcome-label",
                        choices=sorted(OUTCOME_LABELS),
                        help=f"Outcome: {sorted(OUTCOME_LABELS)}")
    parser.add_argument("--review-score", type=int, choices=[1,2,3,4,5],
                        help="Integer 1-5: quality of this prediction")
    parser.add_argument("--failure-reason",
                        choices=sorted(FAILURE_REASONS),
                        help=f"Failure category: {sorted(FAILURE_REASONS)}")
    parser.add_argument("--lesson", help="Free text: what was learned")
    parser.add_argument("--next-adjustment", help="Free text: recommended adjustment for next run")
    parser.add_argument("--notes", help="Free text: any additional notes")
    parser.add_argument("--force", action="store_true",
                        help="Allow overwrite even if outcome_label already set")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and print changes without writing")

    args = parser.parse_args()

    try:
        result = update_review(
            review_id=args.review_id,
            validation_price=args.validation_price,
            max_favorable_move=args.max_favorable_move,
            max_adverse_move=args.max_adverse_move,
            invalidation_hit=args.invalidation_hit,
            outcome_label=args.outcome_label,
            review_score=str(args.review_score) if args.review_score is not None else None,
            failure_reason=args.failure_reason,
            lesson=args.lesson,
            next_adjustment=args.next_adjustment,
            notes=args.notes,
            force=args.force,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.exit(1)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        sys.exit(1)
    except ValidationError as e:
        sys.stderr.write(f"VALIDATION ERROR: {e}\n")
        sys.exit(1)

    if args.dry_run:
        print(f"[DRY RUN] review_id: {result['review_id']}")
        print(f"[DRY RUN] CSV path:   {result['path']}")
        print(f"[DRY RUN] Fields to update ({len(result['updates'])}):")
        for k, v in result["updates"].items():
            print(f"  {k}: {v!r}")
        print(f"[DRY RUN] Row preview:")
        for k, v in result["row_preview"].items():
            print(f"  {k}: {v!r}")
    else:
        print(f"OK|{result['review_id']}|{result['updated_at']}|{','.join(result['fields_updated'])}")


if __name__ == "__main__":
    main()