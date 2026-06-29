"""
Fusion Engine I/O — write/read FusionOutput JSON to history.

Pure functions. No logging side effects. Caller responsibility for directory.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import FusionOutput


def write_fusion_record(
    output: FusionOutput,
    out_dir: str | Path,
    ts: str | None = None,
) -> Path:
    """Write a single FusionOutput JSON file under out_dir.

    Args:
        output:  FusionOutput instance.
        out_dir: Target directory (created if missing).
        ts:      Optional timestamp string; defaults to output.timestamp.

    Filename: YYYY-MM-DD.json if ts parseable, else `<run_id>.json`.
    """
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    ts_str = ts or output.timestamp
    if not ts_str:
        target = out_path / f"{output.run_id}.json"
    else:
        # extract YYYY-MM-DD
        filename_ts = ts_str[:10] if len(ts_str) >= 10 else "unknown"
        target = out_path / f"{filename_ts}.json"

    target.write_text(
        json.dumps(output.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def read_fusion_record(path: str | Path) -> FusionOutput:
    """Load a FusionOutput from a history JSON file."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return FusionOutput.from_dict(data)
