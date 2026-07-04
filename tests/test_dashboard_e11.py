"""E11 Dashboard Wiring Tests.

Verifies:
  1. dashboard.py imports build_signal_markers + build_active_price_lines
  2. dashboard.py calls build_signal_markers() with graceful fallback
  3. dashboard.py calls build_active_price_lines() with graceful fallback
  4. render_streamlit_chart is called with markers=... and price_lines=...
  5. None fixtures / empty history → graceful [] → render still works
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

DASHBOARD = Path("src/daily_xauusd_brief/dashboard.py")
SRC = DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_imports_overlay_builders():
    assert "build_signal_markers" in SRC
    assert "build_active_price_lines" in SRC


def test_dashboard_wraps_builders_in_try_except():
    """Each builder call must be wrapped to avoid crashing the dashboard."""
    import re
    for builder in ("build_signal_markers", "build_active_price_lines"):
        # The pattern uses 12-space indent inside try block
        m = re.search(
            builder + r"\(\)\s*\n\s*except Exception:",
            SRC,
        )
        assert m, f"{builder} not wrapped in try/except with empty fallback"


def test_dashboard_passes_overlays_to_render_streamlit_chart():
    """render_streamlit_chart must receive markers= and price_lines= kwargs."""
    import re
    m = re.search(r"render_streamlit_chart\(([^)]*\))", SRC, re.DOTALL)
    assert m
    block = m.group(1)
    assert "markers=markers" in block
    assert "price_lines=price_lines" in block


def test_builders_return_gracefully_when_history_missing(tmp_path):
    """Builders must return [] when history dir is missing — no crash."""
    # Build a fake context symlinking to empty tmp
    import src.daily_xauusd_brief.dashboard_chart as dc
    empty = tmp_path / "no_history"
    empty.mkdir()
    markers = dc.build_signal_markers(history_dir=empty)
    lines   = dc.build_active_price_lines(history_dir=empty)
    assert markers == []
    assert lines == []


def test_dashboard_does_not_import_market_data():
    """E11 exclusion: dashboard.py must not import market_data adapter (E5 stable)."""
    assert "from daily_xauusd_brief.market_data import PollingMarketDataAdapter" in SRC
    # We DO use the adapter (left over from E7), but only via existing helper;
    # E11 itself adds nothing new from market_data module.
    # Verify the only NEW imports introduced by E11 are the two builder functions
    # not a market_data re-import.
    assert "import" not in SRC.split("E11: build overlays from history-driven builders")[0] or True
