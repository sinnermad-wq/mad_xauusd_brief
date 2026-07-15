"""
XAUUSD Mission Control Dashboard
v9 — Ops Health block: read-only workflow observability panel

E9 responsibilities:
  1. Ops Health section: reuses query_engine_ops.py via subprocess (JSON mode)
  2. Shows: total/success/error runs, success rate, avg/max duration,
     recent runs list, per-script breakdown, error-type breakdown
  3. Error rate warning when > 20% in last 7 days
  4. Read-only: no writes, no cron, no webhook
  5. 60-second cache TTL on ops summary

Does NOT:
  - Modify trading logic
  - Change engine_reviews.csv schema
  - Add cron/webhook/daemon
  - Write any data back
  - Does NOT touch existing auto-refresh control

Reads from:
  ~/projects/daily-xauusd-bot/data/history/  (JSON reports)
  ~/projects/daily-xauusd-bot/logs/            (log file)
  Twelve Data REST API                         (real-time chart data)
"""

import os
import sys
from pathlib import Path

# ── Bootstrap: add src/ to Python path so 'from daily_xauusd_brief ...' resolves ──
# Allows `streamlit run dashboard.py` from the project root without needing a virtualenv activation.
_dashboard_py = Path(__file__).resolve()              # …/src/daily_xauusd_brief/dashboard.py
_src = _dashboard_py.parent.parent                    # …/src  (package root)
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import streamlit as st
import pandas as pd
import json
from pathlib import Path
from datetime import datetime

# ── Robust path resolution ─────────────────────────────────────────────────
BASE_DIR = Path.home() / "projects" / "daily-xauusd-bot"
JSON_DIR = BASE_DIR / "data" / "history"
LOG_FILE = BASE_DIR / "logs" / "daily-xauusd-brief.log"

if not BASE_DIR.exists():
    st.error(f"Project directory not found: {BASE_DIR}")
    st.info("Run `python -m daily_xauusd_brief.main --dry-run` first to generate data.")
    st.stop()

st.set_page_config(
    page_title="XAUUSD Mission Control",
    page_icon="🥇",
    layout="wide",
)


# ── Data loading helpers ────────────────────────────────────────────────────

def load_latest_report(mode: str = "daily") -> dict | None:
    """Return the most recent JSON report for the given mode, or None."""
    if not JSON_DIR.exists():
        return None
    files = sorted(JSON_DIR.glob(f"*{mode}*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_recent_reports(limit: int = 14) -> list[dict]:
    """Load up to `limit` recent JSON reports across all modes."""
    if not JSON_DIR.exists():
        return []
    files = sorted(JSON_DIR.glob("*.json"), reverse=True)
    reports = []
    for f in files[:limit]:
        try:
            with open(f, "r", encoding="utf-8") as jf:
                reports.append(json.load(jf))
        except (json.JSONDecodeError, OSError):
            continue
    return reports


def get_cron_status() -> str:
    """Parse log file for last execution result + staleness check.

    Returns:
        ✅ Success     — last entry contains "history saved" or "cache saved" TODAY
        ⚠️ Stale        — last entry is success but from yesterday or older
        ❌ Failed       — last entry contains "ERROR" or "Exception"
        ⚠️ Unknown      — log empty / not found / no recognizable entry
    """
    if not LOG_FILE.exists():
        return "⚠️ Unknown (log not found)"
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if not lines:
            return "⚠️ Unknown (log empty)"

        today = datetime.now().strftime("%Y-%m-%d")
        last_success_date: str | None = None

        # Scan all lines from bottom (most recent first) to find last success
        for line in reversed(lines):
            lower = line.lower()
            if "history saved" in lower or "cache saved" in lower:
                # Extract date from log line, e.g. "2026-07-14 08:30:56,455 [INFO] ..."
                date_str = line.strip().split(" ")[0]  # "2026-07-14"
                if len(date_str) == 10 and date_str[4] == "-":
                    last_success_date = date_str
                break

        if last_success_date:
            if last_success_date == today:
                return "✅ Success"
            else:
                return f"⚠️ Stale (last success: {last_success_date})"

        # No success found — check for recent errors
        for line in reversed(lines[:20]):  # only check last 20 lines
            if "ERROR" in line or "Exception" in line:
                return "❌ Failed"
        return "⚠️ Unknown"

    except (OSError, UnicodeDecodeError):
        return "⚠️ Unknown (read error)"


def load_latest_candle() -> dict | None:
    """Load the most recent candlestick EngineOutput from data/history/candlestick/."""
    candle_dir = JSON_DIR / "candlestick"
    if not candle_dir.exists():
        return None
    files = sorted(candle_dir.glob("*_candlestick.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def load_latest_fusion() -> dict | None:
    """Load most recent FusionOutput from data/history/fusion/."""
    fusion_dir = JSON_DIR / "fusion"
    if not fusion_dir.exists():
        return None
    files = sorted(fusion_dir.glob("*.json"), reverse=True)
    if not files:
        return None
    try:
        with open(files[0], encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def format_news_item(n) -> str:
    """Handle top_news items — can be str or dict with 'title' key."""
    if isinstance(n, str):
        return n
    if isinstance(n, dict):
        return n.get("title", str(n))
    return str(n)


# ══════════════════════════════════════════════════════════════════════════════
#  E7: Real-Time Chart Section (PollingMarketDataAdapter)
# ══════════════════════════════════════════════════════════════════════════════

def _init_market_data():
    """Initialize PollingMarketDataAdapter in session_state (once per session)."""
    if "market_data_adapter" not in st.session_state:
        try:
            from daily_xauusd_brief.market_data import PollingMarketDataAdapter
            st.session_state.market_data_adapter = PollingMarketDataAdapter()
        except Exception as exc:
            st.session_state.market_data_adapter = None
            st.session_state.market_data_error = str(exc)


def _quota_summary(adapter) -> str:
    """Return a human-readable quota status string."""
    if adapter is None:
        return "adapter unavailable"
    try:
        quota = adapter._quota
        used = quota._daily_used
        budget = quota.daily_budget
        pct = used / budget * 100 if budget else 0
        resets_in = max(0, 86400 - quota._window_start_time)
        resets_h = resets_in // 3600
        resets_m = (resets_in % 3600) // 60
        return (
            f"quota: {used}/{budget} cr used ({pct:.0f}%) · "
            f"per-min: {quota._minute_count}/8 · "
            f"daily reset in ~{resets_h}h {resets_m}m"
        )
    except Exception:
        return "quota status unknown"


# ── UI ─────────────────────────────────────────────────────────────────────

st.title("🥇 XAUUSD Mission Control")
st.caption(f"Data: `{JSON_DIR}`")

# E7: Initialize market data adapter once
_init_market_data()

# Auto-refresh / manual refresh (existing — NOT modified by E7)
col_refresh = st.columns([1, 1, 4])
with col_refresh[0]:
    if st.button("🔄 Refresh"):
        st.rerun()
with col_refresh[1]:
    interval = st.selectbox("Auto-refresh", [0, 30, 60, 300], index=0,
                            format_func=lambda x: "Off" if x == 0 else f"{x}s")
if interval > 0:
    st.autorefresh(interval * 1000, key="auto")

# ── Top Row: 4 metric cards ─────────────────────────────────────────────────
latest = load_latest_report("daily")
reports = load_recent_reports(14)

col1, col2, col3, col4 = st.columns(4)

# 1. Price + change
if latest:
    price = latest.get("xauusd_price", "N/A")
    change_pct = latest.get("daily_change_pct", latest.get("daily_change_abs", "N/A"))
    if isinstance(change_pct, float):
        delta = f"{change_pct:+.2f}%"
    else:
        delta = str(change_pct)
    col1.metric("XAUUSD Price", f"${price}" if price != "N/A" else "N/A", delta)
else:
    col1.metric("XAUUSD Price", "N/A", "Run --dry-run first")

# 2. Cron status
col2.metric("Cron Status", get_cron_status())

# 3. Active mode
col3.metric("Active Mode", "Daily")

# 4. Last run time
if latest:
    ts = latest.get("timestamp", "")
    ts_display = ts[11:16] if len(ts) > 16 else ts
    col4.metric("Last Run", ts_display)
else:
    col4.metric("Last Run", "N/A")

st.divider()

# ── Main: 2-column layout ──────────────────────────────────────────────────
left_col, right_col = st.columns([2, 1])

with left_col:
    st.subheader("📰 Latest Briefing Summary")
    if latest:
        with st.expander("View Full Summary", expanded=True):
            ts_full = latest.get("timestamp", "N/A")
            if "T" in ts_full:
                ts_full = ts_full.replace("T", " ")[:19]
            st.caption(f"⏱ {ts_full}  |  Mode: {latest.get('mode', 'daily')}")

            tech = latest.get("technical_summary", "N/A")
            trend = latest.get("trend", "")
            ma20 = latest.get("ma20", "")
            st.markdown(f"**📊 Technical:** {tech}")
            if ma20:
                st.caption(f"   MA20: ${ma20}  |  Trend: {trend}")

            final = latest.get("final_summary", "N/A")
            st.markdown(f"**🎯 Key Takeaways:** {final}")

            st.markdown("**📋 Top News:**")
            for i, n in enumerate(latest.get("top_news", []), 1):
                st.write(f"{i}. {format_news_item(n)}")

            risk = latest.get("risk_notes", "")
            if risk:
                st.warning(f"⚠️ **Risk:** {risk}")
    else:
        st.info("No daily report found. Run `python -m daily_xauusd_brief.main --dry-run` first.")

    st.subheader("📅 Recent History (Last 14 runs)")
    if reports:
        rows = []
        for r in reports:
            ts = r.get("timestamp", "")
            price_val = r.get("xauusd_price", "—")
            mode_val = r.get("mode", "?")
            tech = (r.get("technical_summary", "") or "")[:40]
            rows.append({
                "Date": ts[:10] if ts else "—",
                "Time": ts[11:16] if len(ts) > 16 else "",
                "Mode": mode_val,
                "Price": f"${price_val}" if price_val != "—" else "—",
                "Technical": tech,
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, width="content", hide_index=True)
    else:
        st.write("No history yet.")

with right_col:
    st.subheader("🔍 Insights")
    if reports:
        themes = ["Fed", "CPI", "NFP", "Yields", "Dollar", "Geopolitics",
                  "Support", "Resistance", "MA20", "Bullish", "Bearish"]
        all_text = " ".join([
            r.get("technical_summary", "") + " " + r.get("final_summary", "")
            for r in reports
        ]).lower()
        found = [k for k in themes if k.lower() in all_text]
        st.write("**Common Themes:**")
        st.write(", ".join(found) if found else "Collecting data...")

        st.markdown("---")
        # Pipeline health
        st.write("**Pipeline Health**")
        status = get_cron_status()
        if "✅" in status:
            st.success(status)
        elif "❌" in status:
            st.error(status)
        elif "⚠️" in status:
            st.warning(status)
        else:
            st.info(status)

        st.markdown("---")
        st.write(f"**Data files:** {len(list(JSON_DIR.glob('*.json')))}")
        st.caption(f"Log: {LOG_FILE.name}")
    else:
        st.info("Run pipeline to see insights.")

# ══════════════════════════════════════════════════════════════════════════════
#  E7: Real-Time XAUUSD Chart (Twelve Data)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  E7/E8: Real-Time XAUUSD Chart (Twelve Data)
#  E7: Manual fetch button + real bars via PollingMarketDataAdapter
#  E8: Controlled auto-refresh with session_state toggle + conservative interval
# ══════════════════════════════════════════════════════════════════════════════

st.divider()
st.subheader("📊 Real-Time XAUUSD Chart (Twelve Data)")

adapter = st.session_state.get("market_data_adapter")

# ── E8: Auto-refresh state initialization ─────────────────────────────
# Tracks previous render timestamp to detect auto-refresh reruns.
now_ts = datetime.now().timestamp()
st.session_state["prev_render_ts"] = st.session_state.get("last_render_ts", now_ts)
st.session_state["last_render_ts"] = now_ts

# ── Controls row ──────────────────────────────────────────────────────────
ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4, ctrl_col5 = st.columns([2, 2, 1, 1, 2])

TIMEFRAME_OPTIONS = ["1m", "5m", "15m", "1h", "4h", "1D"]
DEFAULT_TF = "1h"

with ctrl_col1:
    current_tf = st.selectbox(
        "時間框架",
        TIMEFRAME_OPTIONS,
        index=TIMEFRAME_OPTIONS.index(
            st.session_state.get("current_tf", DEFAULT_TF)
        ) if st.session_state.get("current_tf") in TIMEFRAME_OPTIONS else 1,
        key="tf_selector",
    )
    # Persist selection
    st.session_state.current_tf = current_tf

with ctrl_col2:
    st.caption(f"📡 " + _quota_summary(adapter))

with ctrl_col3:
    # E8: Auto-refresh toggle — default OFF for safety
    auto_chart_refresh = st.checkbox(
        "Auto-refresh",
        value=st.session_state.get("auto_chart_refresh", False),
        key="auto_chart_refresh_toggle",
    )
    st.session_state.auto_chart_refresh = auto_chart_refresh

with ctrl_col4:
    if auto_chart_refresh:
        # E8: Conservative default 5 min; options in seconds
        chart_interval = st.selectbox(
            "Interval",
            [300, 600, 1800],
            index=[300, 600, 1800].index(st.session_state.get("chart_refresh_interval_s", 300)),
            format_func=lambda x: f"{x//60}min",
            key="chart_interval_select",
        )
        st.session_state.chart_refresh_interval_s = chart_interval
    else:
        chart_interval = st.session_state.get("chart_refresh_interval_s", 300)

with ctrl_col5:
    # E8: Status line — shows when last fetched and next auto-refresh ETA
    last_fetch_ts = st.session_state.get("last_fetch_ts", 0)
    if last_fetch_ts:
        ago_sec = max(0, now_ts - last_fetch_ts)
        if auto_chart_refresh and chart_interval:
            next_in = max(0, chart_interval - ago_sec)
            st.caption(f"⏱ Last: {int(ago_sec)}s ago | ⏭ Next: {int(next_in)}s")
        else:
            st.caption(f"⏱ Last fetch: {int(ago_sec)}s ago")
    else:
        st.caption("ℹ️ Click Fetch or enable Auto-refresh")

# ── Manual fetch button ───────────────────────────────────────────────────
with ctrl_col1:
    fetch_label = "📡 Fetch Real Bars"
    if adapter is not None:
        try:
            bars = adapter.get_bars(current_tf, limit=60)
            if bars:
                fetch_label = "📡 Refresh Bars"
        except Exception:
            fetch_label = "📡 Fetch Real Bars"
    fetch_clicked = st.button(fetch_label, type="primary")

# ── Quota progress (rightmost) ──────────────────────────────────────────
with ctrl_col5:
    if adapter is not None:
        try:
            quota = adapter._quota
            daily_pct = quota._daily_used / quota.daily_budget * 100 if quota.daily_budget else 0
            min_pct = quota._minute_count / 8 * 100
            st.progress(min(daily_pct / 100, 1.0), text=f"Daily {daily_pct:.0f}%")
            st.caption(f"Per-min: {quota._minute_count}/8 ({min_pct:.0f}%)")
        except Exception:
            pass

# ── E8: Fetch trigger conditions ────────────────────────────────────────
# Condition 1: Manual button click (existing E7 behaviour)
# Condition 2: Auto-refresh enabled + sufficient elapsed since last fetch
prev_render_ts = st.session_state.get("prev_render_ts", now_ts)
time_since_last_render = now_ts - prev_render_ts
is_auto_rerun = 1.0 <= time_since_last_render <= 3.0  # rerun within 1-3s window

auto_fetch_due = (
    auto_chart_refresh
    and chart_interval
    and (now_ts - last_fetch_ts) >= chart_interval
)
should_fetch = fetch_clicked or auto_fetch_due

# ── Fetch logic ──────────────────────────────────────────────────────────
error_msg = None

if should_fetch and adapter is None:
    error_msg = st.session_state.get("market_data_error", "Adapter not initialized.")
elif should_fetch:
    try:
        with st.spinner(
            "📡 Fetching real bars..."
            if auto_fetch_due and not fetch_clicked
            else f"Fetching {current_tf} bars..."
        ):
            adapter.refresh(timeframes=[current_tf])
        st.session_state.last_fetch_ts = now_ts
        if fetch_clicked:
            st.success(f"Loaded {current_tf} bars from Twelve Data ✓")
        elif auto_fetch_due:
            st.success(f"[Auto] {current_tf} bars refreshed ✓")
    except Exception as exc:
        error_msg = str(exc)
        st.session_state.last_fetch_ts = last_fetch_ts  # don't advance on failure

if error_msg:
    st.error(f"Fetch failed: {error_msg}")

# ── Render chart ──────────────────────────────────────────────────────────
if adapter is not None:
    try:
        from daily_xauusd_brief.dashboard_chart import (
            render_streamlit_chart,
            build_chart_bar_json_from_adapter,
            build_signal_markers,
            build_active_price_lines,
            build_fusion_decision_markers,
        )

        # E11: build overlays from history-driven builders (graceful fallback if no data)
        try:
            markers = build_signal_markers()
        except Exception:
            markers = []
        # Fusion decision markers — overlay on top of history markers
        try:
            fusion_markers = build_fusion_decision_markers()
        except Exception:
            fusion_markers = []
        markers = markers + fusion_markers
        try:
            price_lines = build_active_price_lines()
        except Exception:
            price_lines = []
        # Check if we have bars (from cache or just-fetched)
        bars, bar_time = build_chart_bar_json_from_adapter(adapter, current_tf, limit=60)

        # Phase 2A: intrabar freshness — lightweight 1-credit call with 30s TTL cache
        price_info = None
        try:
            price_info = adapter.get_price_info(ttl_seconds=30)
        except Exception:
            pass  # gracefully skip if adapter doesn't support it yet

        if bars:
            # E11: pass markers + price_lines into render_streamlit_chart
            # Phase 2A: also pass price_freshness for intrabar indicator
            render_streamlit_chart(
                None,
                timeframe=current_tf,
                adapter=adapter,
                markers=markers,
                price_lines=price_lines,
                price_freshness=price_info,
            )
        else:
            st.info(
                f"No {current_tf} bars available. Click **Fetch Real Bars** to load data from Twelve Data. "
                "(Uses ~3-5 credits per fetch)"
            )
            # Show mock chart so the page isn't blank
            from daily_xauusd_brief.dashboard_chart import load_latest_candle, build_chart_bar_json, render_chart
            fallback_candle = load_latest_candle()
            if fallback_candle:
                st.caption("⚠️ Showing simulated chart — connect real data above")
                from daily_xauusd_brief.dashboard_chart import generate_historical_bars
                from datetime import datetime, timezone
                p = fallback_candle.get("source_payload", {})
                mock_bars, _ = build_chart_bar_json(fallback_candle)
                if mock_bars:
                    html = render_chart(mock_bars)
                    from streamlit.components.v1 import html as st_html
                    st_html(html, height=540)
                    ts_str = fallback_candle.get("timestamp", "") or ""
                    bar_ts = ts_str.replace("T", " ")[:19] + " UTC" if ts_str else "—"
                    st.caption(f"🕐 Latest bar: **{bar_ts}** (simulated) | ⚠️ Not real-time")
    except Exception as exc:
        st.error(f"Chart error: {exc}")
else:
    st.info(
        "Market data adapter not available. "
        "Run `python -m daily_xauusd_brief.main --dry-run` to initialize the environment, "
        "or check Twelve Data API key in .env"
    )


# ── Candlestick Full Analysis (existing — NOT modified by E7) ──────────────
st.divider()
st.subheader("🕯️ Candlestick Analysis")
candle = load_latest_candle()
if candle:
    p = candle.get("source_payload", {}) or {}
    bias = candle.get("bias", "neutral")
    bias_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(bias, "⚪")
    bias_color = {"bullish": "#3fb950", "bearish": "#f85149", "neutral": "#8b949e"}.get(bias, "#8b949e")
    strength = candle.get("bias_strength", 0)
    confidence = candle.get("confidence")
    struct = p.get("structure_state", "—")
    run_id = candle.get("run_id", "")
    ts = candle.get("timestamp", "")

    st.markdown(
        f"""
        <div style="border-left:4px solid {bias_color};padding:14px 18px;
                    background:#1c2128;border-radius:6px;margin-bottom:14px;">
          <div style="font-size:22px;font-weight:700;color:{bias_color};">
            {bias_emoji} {bias.upper()}
          </div>
          <div style="color:#8b949e;font-size:13px;margin-top:4px;">
            strength {strength:.0%} ·
            confidence {'—' if confidence is None else f'{confidence:.0%}'} ·
            run_id <code>{run_id[:8]}</code>
          </div>
          <div style="color:#8b949e;font-size:12px;margin-top:2px;">
            {ts} · window: {candle.get('analysis_window', '—')}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    mc1, mc2, mc3, mc4 = st.columns(4)
    rsi = p.get("rsi_14")
    atr = p.get("atr_14")
    mc1.metric("結構", struct)
    mc2.metric("RSI(14)", f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—")
    mc3.metric("ATR(14)", f"{atr:.1f}" if isinstance(atr, (int, float)) else "—")
    patterns_count = len(p.get("detected_patterns") or [])
    mc4.metric("型態", patterns_count)

    c_left, c_right = st.columns(2)
    with c_left:
        st.markdown("**📊 偵測型態**")
        patterns = p.get("detected_patterns") or []
        if patterns:
            for pat in patterns[:5]:
                name = pat.get("name", "?")
                desc = pat.get("description_zh") or name
                st.write(f"- {desc} *({name})*")
        else:
            st.caption("未偵測到顯著型態")

        st.markdown("**🚀 突破狀態**")
        bs = p.get("breakout_state", {}) or {}
        if bs.get("breakout_confirmed"):
            direction = "向上" if bs.get("breakout_type", {}).get("value") == "break_up" else "向下"
            st.success(f"已確認突破 {direction}")
        elif bs.get("breakout_watch"):
            lvl = bs.get("breakout_watch_level")
            lvl_str = f" @ {lvl:.1f}" if isinstance(lvl, (int, float)) else ""
            st.warning(f"觀察中{lvl_str}")
        else:
            st.caption("未偵測突破訊號")

    with c_right:
        st.markdown("**支撐 / 阻力位**")
        supports = p.get("support_levels") or []
        resists = p.get("resistance_levels") or []
        if supports or resists:
            max_len = max(len(supports), len(resists))
            for i in range(max_len):
                s = f"${supports[i]:,.2f}" if i < len(supports) else "—"
                r = f"${resists[i]:,.2f}" if i < len(resists) else "—"
                sc, rc = st.columns([1, 1])
                sc.markdown(f"🟢 L{i + 1}: **{s}**")
                rc.markdown(f"🔴 L{i + 1}: **{r}**")
        else:
            st.caption("未偵測到支撐 / 阻力位")

        explanation = candle.get("explanation_zh", "")
        if explanation:
            st.markdown("**💬 解讀**")
            st.info(explanation)

        report_date = ts[:10] if ts else None
        if report_date:
            report_path = Path.home() / "projects" / "daily-xauusd-bot" / "reports" / "candlestick" / f"{report_date}.html"
            if report_path.exists():
                with open(report_path, "r", encoding="utf-8") as rf:
                    html_content = rf.read()
                st.download_button(
                    f"📄 Download HTML Report ({report_date})",
                    data=html_content,
                    file_name=f"XAUUSD_candlestick_{report_date}.html",
                    mime="text/html",
                )
            else:
                st.caption(f"💡 HTML report 未生成 (路徑: {report_path})")

        validation = p.get("validation")
        if validation:
            st.markdown("**🛡️ Validation (V3 M4)**")
            v_status = validation.get("status", "—")
            status_color = {
                "ok": "#3fb950",
                "qualified_with_caution": "#f0b90b",
                "degraded": "#f0b90b",
                "invalid": "#f85149",
            }.get(v_status, "#8b949e")
            st.markdown(
                f'<span style="color:{status_color};font-weight:700;">●</span> '
                f'status: <code>{v_status}</code>',
                unsafe_allow_html=True,
            )

            conf = validation.get("confidence") or {}
            if conf:
                vc1, vc2, vc3 = st.columns(3)
                vc1.metric("TF align", f"{conf.get('timeframe_alignment_score', 0):.0%}")
                vc2.metric("Cross-engine", f"{conf.get('cross_engine_score', 0):.0%}")
                vc3.metric("Data quality", f"{conf.get('data_quality_score', 0):.0%}")
                final = conf.get("final_confidence", 0)
                st.progress(min(max(final, 0.0), 1.0), text=f"Final confidence: {final:.0%}")

            ce = validation.get("cross_engine") or {}
            if ce.get("label"):
                st.caption(f"Cross-engine: `{ce['label']}`")
            ta = validation.get("timeframe_alignment") or {}
            if ta.get("label"):
                st.caption(f"TF alignment: `{ta['label']}` ({ta.get('alignment_score', 0):.0%})")
            ds = validation.get("data_sanity") or {}
            soft = ds.get("soft_flags") or []
            if soft:
                st.warning(f"Data soft flags: {', '.join(soft)}")

else:
    st.info("No candlestick data. Run `--mode candlestick --dry-run` first.")


# ── Fusion Engine v1 Cockpit (read-only) ─────────────────────────────────────
# Read-only; no execution / auto-trade / broker
# Chart markers: long_watch=green below / short_watch=red above / wait=yellow inBar / no_trade=banner

st.divider()
st.subheader("🎯 Fusion Decision Cockpit")

FUSION_DIR_V1 = BASE_DIR / "data" / "fusion"

def load_latest_fusion_v1():
    if not FUSION_DIR_V1.exists():
        return None
    import json as _json
    files = sorted(FUSION_DIR_V1.glob("*_fusion.json"))
    if not files:
        return None
    try:
        return _json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None

fusion_v1 = load_latest_fusion_v1()

if not fusion_v1:
    st.info("No fusion v1 data. Run: python scripts/run_fusion_engine.py --output text")
else:
    decision   = fusion_v1.get("decision", "unknown")
    strength   = fusion_v1.get("decision_strength", "unknown")
    conf       = float(fusion_v1.get("confluence_score", 0))
    bias       = fusion_v1.get("directional_bias", "unknown")
    b_str      = fusion_v1.get("bias_strength", "unknown")
    regime     = fusion_v1.get("market_regime", "unknown")
    risk_state = fusion_v1.get("risk_state", "unknown")
    readiness  = fusion_v1.get("entry_readiness", "unknown")
    warnings   = fusion_v1.get("warnings") or []
    conflicts  = fusion_v1.get("conflicts") or []
    reasons    = fusion_v1.get("reasons") or []
    inputs_u   = fusion_v1.get("inputs_used") or []
    missing    = fusion_v1.get("missing_inputs") or []
    ctx_score  = float(fusion_v1.get("context_score", 0))
    pa_score   = float(fusion_v1.get("price_action_score", 0))
    env_score  = float(fusion_v1.get("environment_score", 0))
    qual_score = float(fusion_v1.get("quality_score", 0))
    f_ts       = (fusion_v1.get("generated_at") or "")[:19].replace("T", " ")

    dec_colors = {
        "long_watch":  ("#1a7f37", "#3fb950"),
        "short_watch": ("#8b1a1a", "#f85149"),
        "wait":        ("#5e4200", "#e3b23c"),
        "no_trade":    ("#2d1b00", "#9b8eff"),
    }
    _, ac_col = dec_colors.get(decision, ("#1c2128", "#8b949e"))

    dec_emoji = {
        "long_watch":  "🟢 LONG WATCH",
        "short_watch": "🔴 SHORT WATCH",
        "wait":        "⚪ WAIT",
        "no_trade":    "🚫 NO TRADE",
    }.get(decision, "❔ UNKNOWN")

    ac = ac_col
    # Single concatenation — no implicit string concat across lines
    html = (
        '<div style="border-left:4px solid ' + ac + ';padding:16px 20px;'
        + 'background:#0d1117;border-radius:8px;margin-bottom:10px;">'
        + '<div style="font-size:24px;font-weight:700;color:' + ac + ';margin-bottom:4px;">'
        + dec_emoji
        + '</div>'
        + '<div style="color:#8b949e;font-size:13px;">'
        + '<b>Strength:</b> ' + strength + ' &nbsp;|&nbsp;'
        + '<b>Confluence:</b> ' + ('%.1f' % conf) + '% &nbsp;|&nbsp;'
        + '<b>Bias:</b> ' + bias + ' (' + b_str + ')'
        + '</div>'
        + '<div style="color:#8b949e;font-size:13px;margin-top:2px;">'
        + '<b>Regime:</b> ' + regime + ' &nbsp;|&nbsp;'
        + '<b>Risk:</b> ' + risk_state + ' &nbsp;|&nbsp;'
        + '<b>Entry:</b> ' + readiness + ' &nbsp;|&nbsp;'
        + '<b>Generated:</b> ' + f_ts
        + '</div>'
        + '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

    if decision == "no_trade":
        st.warning("🚫 NO TRADE — Market closed / high risk / degraded inputs. See panel below.")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Context Score",  "%.1f" % ctx_score, help="Briefing vs candle bias alignment; event risk penalty")
    c2.metric("Price Action",   "%.1f" % pa_score,  help="Candlestick conviction, structure, rejection, momentum")
    c3.metric("Environment",    "%.1f" % env_score,  help="Regime fit, volatility, sequence, compression")
    c4.metric("Quality Score",  "%.1f" % qual_score,  help="Staleness, missing inputs, conflicts, event risk")

    tab_r, tab_c, tab_w, tab_f = st.tabs([
        "📋 Reasons",
        "⚠️ Conflicts (" + str(len(conflicts)) + ")",
        "🔔 Warnings (" + str(len(warnings)) + ")",
        "📥 Inputs",
    ])

    with tab_r:
        if reasons:
            for r in reasons[:8]:
                st.markdown("- `" + r + "`")
        else:
            st.caption("No reasons recorded.")

    with tab_c:
        if conflicts:
            for c in conflicts:
                st.markdown("⚠️ `" + c + "`")
        else:
            st.caption("No conflicts detected.")

    with tab_w:
        if warnings:
            for w in warnings:
                st.markdown("- `" + w + "`")
        else:
            st.caption("No warnings.")

    with tab_f:
        used_str = ", ".join(inputs_u) if inputs_u else "none"
        miss_str = ", ".join(missing) if missing else "none"
        st.markdown("**Inputs used:** " + used_str)
        st.markdown("**Missing:** " + miss_str)
        c_close = fusion_v1.get("_candle_close")
        if c_close is not None:
            c_dir = fusion_v1.get("_candle_direction_bias", 0)
            c_state = fusion_v1.get("_candle_primary_state", "unknown")
            st.markdown(
                            "**Candle:** close=$" + str(c_close) + " | "
                            "bias=%.2f" % float(c_dir) + " | state=" + str(c_state)
                        )

            st.subheader("🛰️ Engine Reviews")

REVIEWS_CSV = BASE_DIR / "data" / "engine_reviews.csv"

def _load_reviews() -> pd.DataFrame | None:
    if not REVIEWS_CSV.exists():
        return None
    try:
        df = pd.read_csv(REVIEWS_CSV, dtype=str, keep_default_na=False)
        # Parse confidence as numeric for aggregation
        df["_conf_num"] = pd.to_numeric(df["confidence"], errors="coerce")
        # Parse created_at for sorting
        df["_created"] = pd.to_datetime(df.get("created_at", ""), errors="coerce")
        return df
    except Exception:
        return None

reviews_df = _load_reviews()

if reviews_df is None or reviews_df.empty:
    st.info(f"No engine reviews yet. Run `python scripts/log_engine_review.py --input <snapshot.json>` to log an analysis.")
else:
    col1, col2, col3, col4 = st.columns(4)

    total = len(reviews_df)
    avg_conf = reviews_df["_conf_num"].mean()
    # outcome_label ratio
    has_outcome = reviews_df[reviews_df["outcome_label"] != ""]
    correct_pct = (
        (has_outcome["outcome_label"] == "correct").sum() / len(has_outcome) * 100
        if len(has_outcome) > 0 else None
    )
    invalidation_pct = (
        (reviews_df["invalidation_hit"].str.lower() == "true").sum() / total * 100
        if "invalidation_hit" in reviews_df.columns else None
    )
    insufficient_count = (reviews_df["insufficient_context"].str.lower() == "true").sum()
    ma200_ratio = (
        (reviews_df["ma200_available"].str.lower() == "true").sum() / total * 100
        if "ma200_available" in reviews_df.columns else None
    )

    col1.metric("Total Reviews", total)
    col2.metric("Avg Confidence", f"{avg_conf:.1f}" if pd.notna(avg_conf) else "—")
    col3.metric("Correct %", f"{correct_pct:.0f}%" if correct_pct is not None else "—")
    col4.metric("Invalidation Hit %", f"{invalidation_pct:.0f}%" if invalidation_pct is not None else "—")

    st.markdown("**📋 Latest 20 Reviews**")
    disp_cols = [
        "date", "hkt_time", "session", "symbol", "direction_classification",
        "bias", "confidence", "confidence_bucket", "invalidation_level",
        "outcome_label", "insufficient_context",
    ]
    disp = reviews_df.sort_values("_created", ascending=False).head(20)[disp_cols]
    st.dataframe(disp, use_container_width=True, hide_index=True)

    st.markdown("**📊 Breakdowns**")
    b1, b2, b3 = st.columns(3)
    with b1:
        st.caption("By Session")
        sess = reviews_df["session"].value_counts().reset_index()
        sess.columns = ["session", "count"]
        st.dataframe(sess, use_container_width=True, hide_index=True)
    with b2:
        st.caption("By Direction Classification")
        dcc = reviews_df["direction_classification"].value_counts().reset_index()
        dcc.columns = ["direction_classification", "count"]
        st.dataframe(dcc, use_container_width=True, hide_index=True)
    with b3:
        st.caption("By Confidence Bucket")
        cb = reviews_df["confidence_bucket"].value_counts().reset_index()
        cb.columns = ["confidence_bucket", "count"]
        st.dataframe(cb, use_container_width=True, hide_index=True)

    st.markdown("**🔍 Data Quality**")
    dq1, dq2 = st.columns(2)
    dq1.metric("`insufficient_context=true` count", int(insufficient_count))
    dq2.metric("MA200 available ratio", f"{ma200_ratio:.0f}%" if ma200_ratio is not None else "—")


# ── V4 Fusion Engine Summary (existing — NOT modified by E7) ───────────────

st.divider()
st.subheader("🧠 Fusion Decision (V4)")
fusion = load_latest_fusion()
if fusion:
    f_bias = fusion.get("fusion_bias", "neutral")
    bias_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(f_bias, "⚪")
    consensus = fusion.get("consensus_label", "—")
    conflict = fusion.get("conflict_label", "none")
    f_conf = float(fusion.get("fusion_confidence", 0))
    trade_c = fusion.get("trade_candidate", False)
    run_id = fusion.get("run_id", "")

    consensus_color = {
        "aligned": "#3fb950",
        "partially_aligned": "#f0b90b",
        "mixed": "#f0b90b",
        "insufficient_context": "#9b8eff",
    }.get(consensus, "#8b949e")

    sig_id = fusion.get("signal_id", "")

    st.markdown(
        f"""
        <div style="border-left:4px solid {consensus_color};padding:14px 18px;
                    background:#1c2128;border-radius:6px;margin-bottom:14px;">
          <div style="font-size:22px;font-weight:700;color:{consensus_color};">
            {bias_emoji} {f_bias.upper()} · consensus `{consensus}`
          </div>
          <div style="color:#8b949e;font-size:13px;margin-top:4px;">
            fusion_conf {f_conf:.0%} · conflict `{conflict}` ·
            trade_candidate {'✅' if trade_c else '❌'}
          </div>
          <div style="color:#8b949e;font-size:12px;margin-top:2px;">
            run_id <code>{run_id[:8]}</code> ·
            signal_id <code>{sig_id}</code> · schema <code>{fusion.get('schema_version','—')}</code>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    src = fusion.get("source_payload", {}) or {}
    scores = src.get("scores", {}) or {}
    if scores:
        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
        fc1.metric("Candlestick", f"{scores.get('candlestick_score', 0):.0%}")
        fc2.metric("Briefing",    f"{scores.get('briefing_score', 0):.0%}")
        fc3.metric("Agreement",   f"{scores.get('agreement_score', 0):.0%}")
        fc4.metric("Quality",     f"{scores.get('quality_score', 0):.0%}")
        fc5.metric("Final",       f"{fusion.get('fusion_confidence', 0):.0%}")

    explain = fusion.get("explanation_zh", "")
    if explain:
        st.info(f"💬 {explain}")

else:
    st.info("No fusion data. Run `--mode fusion --dry-run` first.")


# ── Ops Health (read-only) ──────────────────────────────────────────────────
# Reuses query_engine_ops.py — no duplicate aggregation logic.
# Manual-only: no writes, no cron, no webhooks.

st.divider()
st.subheader("🛰️ Ops Health")

_OPS_JSONL = BASE_DIR / "data" / "engine_ops_events.jsonl"
_OPS_SCRIPT = BASE_DIR / "scripts" / "query_engine_ops.py"

@st.cache_data(ttl=60)
def _load_ops_summary(days=7) -> dict | None:
    """Call query_engine_ops.py --format json and parse. Returns None if no data."""
    import subprocess, json as _json
    python_bin = sys.executable
    try:
        result = subprocess.run(
            [python_bin, str(_OPS_SCRIPT), "--format", "json",
             "--days", str(days), "--limit", "20"],
            capture_output=True, text=True, timeout=15,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            return None
        return _json.loads(result.stdout)
    except Exception:
        return None

ops = _load_ops_summary()

if ops is None or ops.get("time_window", {}).get("total_events", 0) == 0:
    st.info("No ops events found. Run a review/report script to generate events.")
else:
    total = ops["time_window"]["total_events"]
    success = ops["status_counts"]["success"]
    error = ops["status_counts"]["error"]
    success_rate = ops["status_counts"]["success_rate"]
    error_rate = ops["status_counts"]["error_rate"]
    avg_ms = ops["duration"]["avg_ms"]
    max_ms = ops["duration"]["max_ms"]

    # High error rate warning (>20%)
    if error_rate > 20:
        st.warning(f"⚠️ High error rate: {error_rate}% ({error} errors / {total} runs in 7 days)")

    # Top-row metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total Runs", total)
    m2.metric("Success", success)
    m3.metric("Errors", error)
    m4.metric("Success Rate", f"{success_rate:.0f}%")
    m5.metric("Avg / Max ms", f"{avg_ms:.1f} / {max_ms:.0f}")

    # Two-column lower section: recent events + breakdown
    r_col, b_col = st.columns([1, 1])

    with r_col:
        st.markdown("**📋 Recent Runs**")
        # Reuse --recent from the query script
        import subprocess as _sub
        python_bin = sys.executable
        try:
            recent_result = _sub.run(
                [python_bin, str(_OPS_SCRIPT), "--recent", "--limit", "15", "--days", "7"],
                capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR),
            )
            if recent_result.returncode == 0:
                # Print first 15 lines of --recent output
                lines = recent_result.stdout.strip().split("\n")
                for line in lines[1:]:  # skip header "Recent Events"
                    if line.strip():
                        st.caption(line)
        except Exception:
            st.caption("(failed to load recent events)")

    with b_col:
        # Script breakdown
        st.markdown("**📊 By Script**")
        for sname, sdata in ops.get("script_counts", {}).items():
            s_rate = (sdata["success"] / sdata["count"] * 100) if sdata["count"] else 0
            st.caption(
                f"- {sname}: {sdata['count']} runs, "
                f"✅{sdata['success']} ❌{sdata['error']}, "
                f"avg={sdata['avg_ms']}ms, max={sdata['max_ms']}ms"
            )

        # Error breakdown
        etypes = ops.get("error_types", {})
        if etypes:
            st.markdown("**⚠️ Error Types**")
            for et, ec in etypes.items():
                lt = ops.get("latest_error_per_type", {}).get(et, {})
                lt_time = (lt.get("finished_at") or "—")[:19]
                lt_script = lt.get("script_name") or "—"
                st.caption(f"- {et}: {ec}x  (latest: {lt_time} @ {lt_script})")


# ── Strategy Health ──────────────────────────────────────────────────────────
import sys as _sys
_sh_dir = BASE_DIR / "src" / "strategy_health"
if (_sh_dir / "__init__.py").exists():
    try:
        _sys.path.insert(0, str(BASE_DIR / "src"))
        from strategy_health import (
            load_latest_snapshot,
            compute_all_trend_windows,
            generate_trend_report_markdown,
            build_review_report,
            build_review_report_all_windows,
            load_latest_weekly_digest,
            digest_age_hours,
            get_fusion_summary,
            get_price_summary,
            HEALTH_GREEN, HEALTH_YELLOW, HEALTH_RED, HEALTH_UNKNOWN,
            SEVERITY_OK, SEVERITY_WARN, SEVERITY_CRITICAL, SEVERITY_UNKNOWN,
            APPROVAL_PENDING,
        )

        st.divider()
        st.subheader("🏥 Strategy Health")

        # Load latest snapshot (auto-discovers data files)
        try:
            snap = load_latest_snapshot()
        except Exception:
            snap = None

        if snap is None:
            st.info("Strategy health: no data available (run backtest + fusion first).")

        else:
            # Health status banner
            if snap.health_status == HEALTH_GREEN:
                st.success("✅ Health: GREEN — All diagnostics within normal range")
            elif snap.health_status == HEALTH_YELLOW:
                st.warning("⚠️ Health: YELLOW — At least one diagnostic at warning level")
            elif snap.health_status == HEALTH_RED:
                st.error("🔴 Health: RED — Critical issues detected or data sources missing")
            else:
                st.info("❓ Health: UNKNOWN — Insufficient data to determine status")

            # ── Trend Report ────────────────────────────────────────────────
            try:
                trends = compute_all_trend_windows()
                if trends and any(t.entries_used > 0 for t in trends.values()):
                    with st.expander("📈 Trend Report (7d / 14d / 30d)", expanded=False):
                        report_md = generate_trend_report_markdown(trends)
                        st.markdown(report_md)
                else:
                    st.caption("📈 Trend: need 2+ history entries to compute trends.")
            except Exception as _te:
                st.caption(f"Trend unavailable: {_te}")

            # ── Diagnostic summary cards ──────────────────────────────────
            sev_col1, sev_col2, sev_col3 = st.columns(3)
            diag_names = [d.name for d in snap.diagnostics]
            sev_map = {d.name: d.severity for d in snap.diagnostics}

            with sev_col1:
                st.markdown("**Diagnostics**")
                for d in snap.diagnostics[:2]:
                    icon = "🟢" if d.severity == SEVERITY_OK else \
                           "🟡" if d.severity == SEVERITY_WARN else \
                           "🔴" if d.severity == SEVERITY_CRITICAL else "⚪"
                    st.caption(f"{icon} {d.name}: {d.severity}")

            with sev_col2:
                st.markdown("** &nbsp;**")  # spacer
                for d in snap.diagnostics[2:4]:
                    icon = "🟢" if d.severity == SEVERITY_OK else \
                           "🟡" if d.severity == SEVERITY_WARN else \
                           "🔴" if d.severity == SEVERITY_CRITICAL else "⚪"
                    st.caption(f"{icon} {d.name}: {d.severity}")

            with sev_col3:
                st.markdown("** &nbsp;**")
                for d in snap.diagnostics[4:]:
                    icon = "🟢" if d.severity == SEVERITY_OK else \
                           "🟡" if d.severity == SEVERITY_WARN else \
                           "🔴" if d.severity == SEVERITY_CRITICAL else "⚪"
                    st.caption(f"{icon} {d.name}: {d.severity}")

            # ── Suggestions ───────────────────────────────────────────────
            if snap.suggestions:
                st.markdown(f"**💡 Suggestions ({len(snap.suggestions)})**")
                for sug in snap.suggestions:
                    priority_icon = {
                        1: "🛑", 2: "🔴", 3: "🟠", 4: "🟡",
                        5: "🟡", 6: "⚠️", 7: "👀", 8: "✅"
                    }.get(sug.priority, "•")
                    st.markdown(
                        f"{priority_icon} **[{sug.kind}]** *{sug.title}*  "
                        f"| Priority {sug.priority}"
                    )
                    with st.expander("Rationale + Actions", expanded=False):
                        st.markdown(f"_{sug.rationale}_")
                        if sug.actions:
                            for a in sug.actions:
                                st.caption(f"• {a}")

            # ── Pending approvals ─────────────────────────────────────────
            pending = [pa for pa in snap.pending_approvals if pa.status == APPROVAL_PENDING]
            if pending:
                st.warning(
                    f"📋 **{len(pending)} pending approval(s)** — "
                    "edit `data/strategy_health/approvals.json` manually to resolve."
                )
            else:
                st.success("All suggestions have been reviewed.")

            # ── Snapshot metadata ──────────────────────────────────────────
            with st.expander("🔧 Snapshot details"):
                col_a, col_b = st.columns(2)
                with col_a:
                    st.write(f"**ID:** `{snap.snapshot_id}`")
                    st.write(f"**Generated:** {snap.generated_at}")
                with col_b:
                    st.write(f"**Config:** `{snap.config_snapshot.get('performance_window', '?')}t` perf / "
                             f"`{snap.config_snapshot.get('regime_window', '?')}d` regime")
                if snap.warnings:
                    st.write(f"**Warnings:** `{', '.join(snap.warnings)}`")

            # ── Review Report (read-only) ────────────────────────────────────
            st.markdown("#### 📋 Review Report")
            st.caption(
                "Read-only summary aggregating current snapshot + history trends. "
                "Window selector shows 7d / 14d / 30d. No auto-apply, no execution."
            )
            rev_window = st.selectbox(
                "Review window",
                options=[7, 14, 30],
                index=0,
                format_func=lambda d: f"{d} day(s)",
                key="review_window_days",
            )

            try:
                review = build_review_report(snap, window_days=rev_window)

                # Window comparison: build all 3 once and let users switch via selectbox
                _all_reviews = build_review_report_all_windows(snap)
                if (
                    _all_reviews.get(f"{rev_window}d") is not None
                    and _all_reviews[f"{rev_window}d"].sections
                ):
                    review = _all_reviews[f"{rev_window}d"]
                else:
                    # Fall back to the function result
                    review = build_review_report(snap, window_days=rev_window)

                # Compact header
                _hdr_cols = st.columns([2, 2, 2])
                _hdr_cols[0].metric("Window", f"{review.window_days}d")
                _hdr_cols[1].metric("Health Status", review.health_status.upper())
                _hdr_cols[2].metric(
                    "Health Score",
                    f"{review.health_score:.1f}" if review.health_score >= 0 else "—",
                )

                if not review.has_sufficient_history:
                    st.warning(
                        "⚠️ Insufficient history (< 2 entries in selected window). "
                        "Showing current-snapshot summary only."
                    )

                # Render each section
                for sec in review.sections:
                    if not sec.has_content():
                        continue
                    with st.expander(sec.heading, expanded=(sec.heading.startswith("1."))):
                        if sec.findings:
                            for f in sec.findings:
                                st.markdown(f"- {f.display()[:300]}")
                        if sec.table:
                            header = sec.table[0]
                            rows = sec.table[1:]
                            try:
                                import pandas as _pd
                                _df = _pd.DataFrame(rows, columns=header)
                                st.table(_df)
                            except Exception:
                                # Fallback: simple markdown table
                                st.markdown("| " + " | ".join(str(c) for c in header) + " |")
                                st.markdown("| " + " | ".join("---" for _ in header) + " |")
                                for r in rows:
                                    st.markdown("| " + " | ".join(str(c) for c in r) + " |")
            except Exception as _re:
                st.caption(f"Review report unavailable: {_re}")

            # ── Review Actions Queue (manual-only) ─────────────────────────────
            st.markdown("#### 🎯 Review Actions Queue")
            st.caption(
                "Prioritized action list for manual review. "
                "No auto-apply, no execution."
            )
            q_window = st.selectbox(
                "Queue window",
                options=[7, 14, 30],
                index=0,
                format_func=lambda d: f"{d} day(s)",
                key="queue_window_days",
            )

            try:
                q = build_review_actions_queue(snap, window_days=q_window)

                # Header stats
                c1, c2, c3 = st.columns([2, 2, 2])
                c1.metric("Queue window", f"{q.window_days}d")
                c2.metric("Total actions", q.total_count)
                c3.metric(
                    "Top priority",
                    q.top_action().title[:40] if q.total_count > 0 else "—",
                )

                if not q.has_sufficient_history:
                    st.warning(
                        "⚠️ Insufficient history. Showing current-snapshot queue only."
                    )

                if q.is_empty():
                    st.info(
                        "✅ No actions queued. All items reviewed or no issues detected."
                    )
                else:
                    # Export: markdown (left) + CSV (right)
                    col_md, col_csv = st.columns([1, 1])
                    with col_md:
                        st.markdown(q.to_markdown())
                    with col_csv:
                        st.code(q.to_csv(), language=None)

                    # Priority-sorted table
                    st.markdown(
                        f"**{q.total_count} action(s) — sorted by priority**"
                    )
                    header = ["PRIORITY", "CATEGORY", "TITLE", "SEVERITY", "AGE"]
                    rows = []
                    for a in q.actions:
                        rows.append(
                            [
                                f"{a.priority_score:.1f}",
                                a.category,
                                a.title[:55],
                                a.severity.upper(),
                                a.age_label,
                            ]
                        )
                    _df = pd.DataFrame(rows, columns=header)
                    st.table(_df)

            except Exception as _qre:
                st.caption(f"Review Actions Queue unavailable: {_qre}")

    except Exception as _e:
        st.warning(f"Strategy Health unavailable: {_e}")
else:
    pass  # strategy_health module not yet installed


# ── Weekly Digest (read-only, auto-loaded) ─────────────────────────────────
try:
    from strategy_health.digest_loader import (
        load_latest_weekly_digest,
        weekly_digest_fallback,
        digest_age_hours,
    )
    _digest = load_latest_weekly_digest()
except Exception:
    _digest = weekly_digest_fallback("dashboard import failed")

if _digest.get("_fallback"):
    st.divider()
    st.subheader("📊 Weekly Digest")
    st.info(
        "⚠️ No weekly digest available. "
        "Run `scripts\generate_weekly_digest.py` to generate the first digest, "
        "then schedule it with Windows Task Scheduler (see `docs/scheduler.md`)."
    )
else:
    st.divider()
    st.subheader("📊 Weekly Digest")

    _age = digest_age_hours(_digest)
    _age_str = f"{_age:.1f}h ago" if _age is not None else "unknown"

    _price = _digest.get("summary", {}).get("price", {})
    _fusion = _digest.get("summary", {}).get("fusion", {})
    _reviews = _digest.get("summary", {}).get("engine_reviews", {})
    _news = _digest.get("summary", {}).get("news", {})

    _cols = st.columns([1, 1, 1, 1])
    _cols[0].metric(
        "Window",
        f"{_digest.get('window_days', '?')}d",
        help=f"Generated {_digest.get('generated_at', '?')[:16]} · {_age_str}"
    )
    _cols[1].metric(
        "Dominant Bias",
        _digest.get("dominant_bias", "N/A") or "mixed",
        help=f"Fusion runs: {_fusion.get('total_runs', 0)}"
    )
    _cols[2].metric(
        "Avg Fusion Conf",
        f"{(_digest.get('avg_fusion_confidence') or 0) * 100:.0f}%"
        if _digest.get("avg_fusion_confidence") is not None else "N/A",
        help=f"Conflicts (any): {_fusion.get('conflict_any_count', 0)}"
    )
    _cols[3].metric(
        "Price 7d Δ",
        f"{_price.get('change_pct', 0):+.2f}%"
        if _price.get("change_pct") is not None else "N/A",
        help=f"High ${_price.get('high', '?')} · Low ${_price.get('low', '?')}"
    )

    with st.expander("📋 Digest Details", expanded=False):
        _tab1, _tab2 = st.columns(2)
        with _tab1:
            st.markdown("**🧠 Fusion Engine**")
            _fc = [
                ("Total runs", _fusion.get("total_runs", 0)),
                ("Bullish", _fusion.get("bullish_count", 0)),
                ("Bearish", _fusion.get("bearish_count", 0)),
                ("Neutral", _fusion.get("neutral_count", 0)),
                ("Trade candidates", _fusion.get("trade_candidate_count", 0)),
                ("Conflicts (any)", _fusion.get("conflict_any_count", 0)),
            ]
            for _lbl, _val in _fc:
                st.caption(f"  · {_lbl}: **{_val}**")
        with _tab2:
            if _reviews.get("total_reviews", 0) > 0:
                st.markdown("**🛰️ Engine Reviews**")
                _rc = [
                    ("Total reviews", _reviews.get("total_reviews", 0)),
                    ("Avg confidence", f"{_reviews.get('avg_confidence', 'N/A')}%"),
                    ("Outcomes filled", _reviews.get("outcomes_filled", 0)),
                    ("  · correct", _reviews.get("outcomes_correct", 0)),
                    ("  · incorrect", _reviews.get("outcomes_incorrect", 0)),
                    ("Insufficient MA200", _reviews.get("insufficient_context_count", 0)),
                ]
                for _lbl, _val in _rc:
                    st.caption(f"  · {_lbl}: **{_val}**")
            else:
                st.caption("No engine reviews in this window.")
        _art_count = _news.get("total_articles", 0)
        if _art_count > 0:
            st.markdown(f"**📰 News ({_art_count} articles)**")
            for _t in _news.get("top_tags", [])[:5]:
                st.caption(f"  · `{_t['tag']}` ({_t['count']})")

    st.caption(
        f"Generated: {_digest.get('generated_at', '?')[:16]} · "
        f"{_age_str} · Schema v{_digest.get('schema_version', '?')} · "
        "read-only / manual-only"
    )

# ── Footer ─────────────────────────────────────────────────────────────────

st.caption(
    "XAUUSD Mission Control v9 (E9 + Strategy Health) | "
    f"Data: `{JSON_DIR}` | "
    "Run `python -m daily_xauusd_brief.main --dry-run` to generate today's report"
)
