"""
XAUUSD Mission Control Dashboard
v7.2 — Fixed: key mismatches, Path.home(), auto-refresh, top_news format

Reads from:
  ~/projects/daily-xauusd-bot/data/history/  (JSON reports)
  ~/projects/daily-xauusd-bot/logs/            (log file)
"""

import os
import streamlit as st
import pandas as pd
import json
from pathlib import Path
from datetime import datetime

# ── Robust path resolution ─────────────────────────────────────────────────
# Use Path.home() for reliable cross-platform home dir (works in Git Bash,
# CMD, PowerShell, and Windows Python).
BASE_DIR = Path.home() / "projects" / "daily-xauusd-bot"
JSON_DIR = BASE_DIR / "data" / "history"
LOG_FILE = BASE_DIR / "logs" / "daily-xauusd-brief.log"

# Ensure data directory exists (shows friendly error if not)
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
    """Parse log file for last execution result."""
    if not LOG_FILE.exists():
        return "Unknown (log not found)"

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in reversed(lines):
            if "history saved" in line.lower() or "cache saved" in line.lower():
                return "✅ Success"
            if "ERROR" in line or "Exception" in line:
                return "❌ Failed"
    except (OSError, UnicodeDecodeError):
        pass
    return "⚠️ Unknown"


def load_latest_candle() -> dict | None:
    """
    Load the most recent candlestick EngineOutput from
    data/history/candlestick/.

    Returns the EngineOutput dict or None.
    """
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


# ── UI ─────────────────────────────────────────────────────────────────────

st.title("🥇 XAUUSD Mission Control")
st.caption(f"Data: `{JSON_DIR}`")

# Auto-refresh / manual refresh
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
    # Fix: correct key is daily_change_pct (not daily_change)
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
    ts_display = ts[11:16] if len(ts) > 16 else ts  # HH:MM
    col4.metric("Last Run", ts_display)
else:
    col4.metric("Last Run", "N/A")

st.divider()

# ── Main: 2-column layout ──────────────────────────────────────────────────
left_col, right_col = st.columns([2, 1])

with left_col:
    # Latest Briefing Summary
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
        # Theme keywords
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
        st.write("**Pipeline Health**")
        status = get_cron_status()
        if "✅" in status:
            st.success(status)
        elif "❌" in status:
            st.error(status)
        else:
            st.info(status)

        st.markdown("---")
        st.write(f"**Data files:** {len(list(JSON_DIR.glob('*.json')))}")
        st.caption(f"Log: {LOG_FILE.name}")
    else:
        st.info("Run pipeline to see insights.")

# ── Candlestick Full Analysis (V3 M3) ──────────────────────────────────
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

    # Signal banner
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

    # Metrics grid
    mc1, mc2, mc3, mc4 = st.columns(4)
    rsi = p.get("rsi_14")
    atr = p.get("atr_14")
    mc1.metric("結構", struct)
    mc2.metric("RSI(14)", f"{rsi:.1f}" if isinstance(rsi, (int, float)) else "—")
    mc3.metric("ATR(14)", f"{atr:.1f}" if isinstance(atr, (int, float)) else "—")
    patterns_count = len(p.get("detected_patterns") or [])
    mc4.metric("型態", patterns_count)

    # Two columns: Patterns + Breakout / SR
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

        # Telegram preview
        explanation = candle.get("explanation_zh", "")
        if explanation:
            st.markdown("**💬 解讀**")
            st.info(explanation)

        # Link to HTML report
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

        # ── V3 M4 Validation Layer panel ────────────────────────────────
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


# ── V4 Fusion Engine Summary ───────────────────────────────────────────────
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

    # Sub-scores from source_payload (V4 spec: candlestick_score / briefing /
    # agreement / quality / final)
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


# ── Footer ───────────────────────────────────────────────────────────────


st.caption(
    "XAUUSD Mission Control v8.2 (V4 Fusion Engine) | "
    f"Data: `{JSON_DIR}` | "
    "Run `python -m daily_xauusd_brief.main --dry-run` to generate today's report"
)