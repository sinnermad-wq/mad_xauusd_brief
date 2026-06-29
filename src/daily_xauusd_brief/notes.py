"""使用者手動心得：讀 reports/notes/{YYYY-MM-DD}.md 或最近的 notes.md。"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_notes(base_dir: Path, report_date: str) -> str:
    """載入指定日期嘅使用者筆記。

    Lookup order:
    1. `reports/notes/{report_date}.md`
    2. `reports/notes/latest.md`

    Returns: file content (stripped) or "".
    """
    notes_dir = base_dir / "reports" / "notes"
    if not notes_dir.exists():
        return ""

    candidates = [
        notes_dir / f"{report_date}.md",
        notes_dir / "latest.md",
    ]
    for p in candidates:
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    logger.info("loaded notes from %s", p)
                    return content
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to read %s: %s", p, exc)

    return ""
