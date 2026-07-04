"""
E8 Controlled Auto-refresh Tests.

Verifies:
  1. Auto-refresh toggle exists and persists via session_state
  2. Interval selectbox appears only when auto-refresh is enabled
  3. Fetch trigger fires on both manual click and auto-fetch due
  4. last_fetch_ts advances on success, not on failure
  5. is_auto_rerun detection logic is correct
  6. E8 exclusions: no /price intrabar, no signal overlay
  7. Quota exhausted does not crash — error message shown gracefully

Does NOT:
  - Require a running Streamlit app (structural/code inspection tests)
  - Test actual timing (impossible in unit tests)
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys as _sys


# ── Load dashboard.py source for inspection ────────────────────────────────


def _get_dashboard_src() -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / "daily_xauusd_brief"
        / "dashboard.py"
    ).read_text(encoding="utf-8")


# ── E8 scope tests ────────────────────────────────────────────────────────


def test_auto_refresh_toggle_exists_in_e8_section():
    """E8 section has a st.checkbox for auto_chart_refresh."""
    src = _get_dashboard_src()
    e8_start = src.find("E8: Auto-refresh")
    assert e8_start > 0, "E8 section not found"
    e8 = src[e8_start:e8_start + 2000]
    assert 'st.checkbox' in e8, "Auto-refresh checkbox not found in E8 section"
    assert 'auto_chart_refresh' in e8, "'auto_chart_refresh' not in E8 section"


def test_interval_selectbox_in_e8_section():
    """Interval selectbox (5min / 10min / 30min) exists in E8."""
    src = _get_dashboard_src()
    e8 = src[src.find("E8: Auto-refresh"):src.find("E8: Auto-refresh") + 2000]
    assert '[300, 600, 1800]' in e8 or 'chart_interval' in e8, \
        "Interval options [300, 600, 1800]s (5/10/30min) not found in E8"


def test_fetch_trigger_conditions_include_auto_fetch_due():
    """Fetch logic evaluates both manual click AND auto_fetch_due."""
    src = _get_dashboard_src()
    assert 'should_fetch = fetch_clicked or auto_fetch_due' in src, \
        "should_fetch must consider both click and auto_fetch_due"


def test_last_fetch_ts_advances_on_success():
    """On successful fetch, session_state.last_fetch_ts is set to current time."""
    src = _get_dashboard_src()
    # After adapter.refresh(), last_fetch_ts should be set
    assert 'st.session_state.last_fetch_ts = now_ts' in src, \
        "last_fetch_ts not updated on successful fetch"


def test_last_fetch_ts_unchanged_on_failure():
    """On fetch exception, last_fetch_ts is NOT advanced (keeps old value)."""
    src = _get_dashboard_src()
    # In the except block: st.session_state.last_fetch_ts = last_fetch_ts (old value)
    assert 'st.session_state.last_fetch_ts = last_fetch_ts' in src, \
        "On failure, last_fetch_ts must be restored to previous value"


def test_no_price_intrabar_update_in_e8():
    """E8 does not call get_price() or /price endpoint."""
    src = _get_dashboard_src()
    e8_start = src.find("E8: Auto-refresh")
    e8 = src[e8_start:]
    # Only scan the E8-E11 range (Phase 2A E12 code is in E11 section after E8)
    e11_markers = ["E9: Signal", "E10: Price", "E11:", "E12:"]
    e11_pos = len(e8)
    for marker in e11_markers:
        pos = e8.find(marker)
        if pos != -1 and pos < e11_pos:
            e11_pos = pos
    e8_only = e8[:e11_pos]

    assert "get_price_info" not in e8_only, "E8 section must not call get_price_info (Phase 2A is E12, not E8)"
    assert "/price" not in e8_only, "E8 must not call /price (E8 exclusion)"


def test_no_signal_overlay_in_e8():
    """E8 chart section has no signal overlay."""
    src = _get_dashboard_src()
    e8_start = src.find("E8: Auto-refresh")
    e8 = src[e8_start:]
    assert 'addLineSeries' not in e8, "No signal overlay (E8 exclusion)"
    assert 'signal_overlay' not in e8.lower(), "No signal overlay (E8 exclusion)"


def test_auto_fetch_due_requires_auto_chart_refresh_flag():
    """auto_fetch_due requires auto_chart_refresh=True to prevent rogue fetches."""
    src = _get_dashboard_src()
    # auto_fetch_due must start with the auto_chart_refresh check
    # Find the auto_fetch_due definition
    idx = src.find('auto_fetch_due = (')
    assert idx >= 0
    chunk = src[idx:idx + 300]
    assert 'auto_chart_refresh' in chunk, \
        "auto_fetch_due must check auto_chart_refresh flag first"


def test_last_fetch_ts_defined_before_fetch_trigger():
    """last_fetch_ts is initialized before auto_fetch_due is evaluated."""
    src = _get_dashboard_src()
    last_fetch_ts_idx = src.find('last_fetch_ts = st.session_state.get("last_fetch_ts"')
    auto_fetch_due_idx = src.find('auto_fetch_due = (')
    assert last_fetch_ts_idx > 0 and auto_fetch_due_idx > 0, "Both must exist"
    assert last_fetch_ts_idx < auto_fetch_due_idx, \
        "last_fetch_ts must be defined before auto_fetch_due is evaluated"


def test_auto_rerun_detection_logic_present():
    """is_auto_rerun detection uses prev_render_ts to identify auto-refresh reruns."""
    src = _get_dashboard_src()
    assert 'prev_render_ts' in src, "prev_render_ts tracking must be present"
    assert 'time_since_last_render' in src, "time_since_last_render must be computed"
    assert 'is_auto_rerun' in src, "is_auto_rerun variable must exist"


def test_quota_exhausted_shows_error_not_crash():
    """When adapter.refresh() raises QuotaExceeded, error_msg is set and page continues."""
    src = _get_dashboard_src()
    # The fetch logic uses bare except Exception — quota errors are caught
    # and displayed via st.error, not crash
    assert 'st.error(f"Fetch failed' in src, \
        "Quota errors must be shown via st.error (graceful degradation)"


def test_session_state_persists_auto_chart_refresh():
    """auto_chart_refresh is stored in session_state for persistence across reruns."""
    src = _get_dashboard_src()
    # The toggle must write to session_state
    assert 'st.session_state.auto_chart_refresh = auto_chart_refresh' in src, \
        "auto_chart_refresh must be persisted in session_state"


def test_no_autorefresh_call_in_e8_section():
    """E8 section does NOT call st.autorefresh directly — controlled via logic instead."""
    src = _get_dashboard_src()
    e8_start = src.find("E8: Auto-refresh")
    e8 = src[e8_start:]
    assert 'st.autorefresh' not in e8, \
        "E8 must not call st.autorefresh — uses session_state + external rerun"


def test_interval_presets_are_conservative():
    """Auto-refresh intervals are at least 5 minutes (no high-frequency polling)."""
    src = _get_dashboard_src()
    # Find the E8 interval selectbox options list
    idx = src.find('[300, 600, 1800]')
    assert idx > 0, "Interval list [300, 600, 1800]s not found"
    # Confirm this is inside the selectbox (min is 5 min)
    interval_section = src[idx - 100:idx + 50]
    assert '300' in interval_section  # 5 min minimum
    # Should not contain sub-5-minute options in the interval list
    assert 'st.selectbox' in interval_section


def test_only_active_timeframe_fetched():
    """Auto-refresh always calls adapter.refresh([current_tf]) — never all timeframes."""
    src = _get_dashboard_src()
    # In fetch logic: adapter.refresh(timeframes=[current_tf])
    assert 'adapter.refresh(timeframes=[current_tf])' in src, \
        "Must refresh only active timeframe, not all timeframes"