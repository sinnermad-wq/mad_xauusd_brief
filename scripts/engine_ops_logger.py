"""engine_ops_logger.py — append workflow events to data/engine_ops_events.jsonl.

All engine-review scripts log to this file for observability.
Events are JSONL, one JSON object per line, newest first.

Usage (inside scripts)
----------------------
    from engine_ops_logger import log_event, track

    # Simple call
    log_event(script_name="log_engine_review.py", status="success",
              review_id="GC-F_2026-07-07_103257_14afec", rows_affected=1,
              output_path=str(CSV_PATH))

    # With duration tracking
    started = time.time()
    try:
        result = do_something()
        duration_ms = int((time.time() - started) * 1000)
        log_event(script_name=..., status="success",
                  duration_ms=duration_ms, review_id=result["id"])
    except SomeError as e:
        duration_ms = int((time.time() - started) * 1000)
        log_event(script_name=..., status="error",
                  error_type=type(e).__name__, error_message=str(e),
                  duration_ms=duration_ms)
        raise  # re-raise, do NOT swallow

    # Context-manager shortcut (captures duration automatically)
    with track(script_name="log_engine_review.py", review_id="X") as ev:
        ev.update(output_path=str(path))   # add fields before final flush
        # ... your code ...
        ev["status"] = "success"

    # Dry-run event
    with track(script_name="generate_engine_weekly_report.py",
               metadata={"dry_run": True}) as ev:
        ev["status"] = "success"
        ev["output_path"] = "stdout"

Rules
-----
- ALWAYS logs success AND error — no silent drops.
- errors MUST re-raise after logging (do NOT swallow exceptions).
- log_event() never raises; any write failure is warned but not propagated.
- Log file: {REPO_ROOT}/data/engine_ops_events.jsonl
- If directory missing, created automatically.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

_HKT = timezone(timedelta(hours=8))
_LOG_PATH = Path.home() / "projects" / "daily-xauusd-bot" / "data" / "engine_ops_events.jsonl"

# Reusable event field defaults
_BASE_FIELDS = (
    "event_type",
    "script_name",
    "started_at",
    "finished_at",
    "duration_ms",
    "status",
    "error_type",
    "error_message",
    "review_id",
    "output_path",
    "rows_affected",
    "metadata",
)


def _now_iso() -> str:
    return datetime.now(_HKT).isoformat(timespec="milliseconds")


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def log_event(
    script_name: str = "",
    status: str = "success",
    error_type: str | None = None,
    error_message: str | None = None,
    review_id: str | None = None,
    output_path: str | None = None,
    rows_affected: int | None = None,
    duration_ms: int | None = None,
    metadata: dict | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    """Append a JSONL event to the ops log.

    Never raises. Write failures are warned to stderr but do not propagate.
    """
    event: dict[str, Any] = {
        "event_type": "script_run",
        "script_name": script_name,
        "started_at": started_at or "",
        "finished_at": finished_at or _now_iso(),
        "duration_ms": duration_ms,
        "status": status,
        "error_type": error_type,
        "error_message": error_message,
        "review_id": review_id,
        "output_path": output_path,
        "rows_affected": rows_affected,
        "metadata": metadata or {},
    }

    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    try:
        _ensure_dir(_LOG_PATH)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        import sys
        print(f"WARNING: engine_ops_logger: failed to write event: {exc}",
              file=sys.stderr)


@contextmanager
def track(
    script_name: str,
    review_id: str | None = None,
    metadata: dict | None = None,
):
    """Context manager that auto-captures started_at / duration_ms.

    Usage:
        with track(script_name="log_engine_review.py",
                   review_id="GC-F_2026-07-07_103257",
                   metadata={"dry_run": True}) as ev:
            # ev is a mutable dict that gets written on __exit__
            ev["output_path"] = "/path/to/file"
            # ... do work ...
            ev["status"] = "success"

    IMPORTANT: If an exception escapes the context, status is set to "error"
    and the exception is re-raised after logging.
    """
    started_at = _now_iso()
    ev: dict[str, Any] = {
        "event_type": "script_run",
        "script_name": script_name,
        "started_at": started_at,
        "finished_at": "",
        "duration_ms": None,
        "status": "error",          # default; override before exit if success
        "error_type": None,
        "error_message": None,
        "review_id": review_id,
        "output_path": None,
        "rows_affected": None,
        "metadata": metadata or {},
    }
    start_ts = time.time()
    try:
        yield ev
    except Exception as exc:
        ev["finished_at"] = _now_iso()
        ev["duration_ms"] = int((time.time() - start_ts) * 1000)
        ev["status"] = "error"
        ev["error_type"] = type(exc).__name__
        ev["error_message"] = str(exc)
        _write_event(ev)
        raise                           # MUST re-raise
    else:
        ev["finished_at"] = _now_iso()
        ev["duration_ms"] = int((time.time() - start_ts) * 1000)
        _write_event(ev)


def _write_event(ev: dict[str, Any]) -> None:
    try:
        _ensure_dir(_LOG_PATH)
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        import sys
        print(f"WARNING: engine_ops_logger: failed to write event: {exc}",
              file=sys.stderr)