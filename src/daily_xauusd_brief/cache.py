"""DailyReport cache save/load — 重複使用生成好的 report，不重抓資料。

目標：讓 `--send-only` (重發) ไม่งั่งลง重複 fetch + compute。"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

from .models import DailyReport

logger = logging.getLogger(__name__)

CACHE_FILENAME = "latest_report.pkl"


def cache_path(base_dir: Path | None = None) -> Path:
    """Cache 檔位置。"""
    if base_dir is None:
        # default: repo_root/data/cache
        base_dir = Path(__file__).resolve().parent.parent.parent / "data" / "cache"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / CACHE_FILENAME


def save_latest(report: DailyReport, base_dir: Path | None = None) -> Path:
    """Pickle 寫出最新 report。"""
    p = cache_path(base_dir)
    p.write_bytes(pickle.dumps(report))
    logger.info("cache saved to %s", p)
    return p


def load_latest(base_dir: Path | None = None) -> DailyReport | None:
    """載入最新 report，無 cache 回傳 None。"""
    p = cache_path(base_dir)
    if not p.exists():
        logger.warning("no cache at %s", p)
        return None
    try:
        report = pickle.loads(p.read_bytes())
        logger.info("cache loaded from %s", p)
        return report
    except Exception as exc:  # noqa: BLE001
        logger.error("cache load failed: %s", exc)
        return None
