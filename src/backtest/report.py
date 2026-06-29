"""Report generation — write outcomes + calibration + breakdown as JSON + Markdown.

Outputs (per spec, Part 5):
    1. outcomes.json  — machine-readable row-level data
    2. calibration.json — bucket + ECE + Brier
    3. summary.md — human-readable Markdown

Pure functions: no global state. I/O happens only at the top-level `write_report` call.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .models import (
    BacktestRunSummary,
    CalibrationReport,
    ReplaySpec,
    VERDICT_OK,
    VERDICT_INSUFFICIENT,
)
from .breakdown import compute_breakdown


# ─── Entry point ────────────────────────────────────────────────────────
def write_report(
    summary: BacktestRunSummary,
    outcomes: List,
    output_dir: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> Dict[str, Path]:
    """Write all backtest outputs.

    Args:
        summary: BacktestRunSummary from the full pipeline
        outcomes: list of Outcome dicts (from [o.to_dict() for o in ...])
        output_dir: directory for output files. If None, files are returned
                    as Path objects without writing (for dry-run preview).
        dry_run: if True, write summary to stdout instead of files and
                 return paths pointing to temp text (no file I/O).

    Returns:
        Dict of {"outcomes": Path, "calibration": Path, "summary": Path}
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    output_dir = Path(output_dir) if output_dir else None

    calibration_path = _ensure(output_dir, f"calibration_{ts}.json")
    outcomes_path    = _ensure(output_dir, f"outcomes_{ts}.json")
    summary_path     = _ensure(output_dir, f"summary_{ts}.md")

    outcomes_data = [o.to_dict() if hasattr(o, "to_dict") else o for o in outcomes]
    calibration_data = summary.calibration.to_dict()
    summary_text = _render_summary_md(summary, calibration_data, outcomes_data)

    # Write files only when not in dry-run mode
    if output_dir is not None and not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        calibration_path.write_text(json.dumps(calibration_data, indent=2), encoding="utf-8")
        outcomes_path.write_text(json.dumps(outcomes_data, indent=2), encoding="utf-8")
        summary_path.write_text(summary_text, encoding="utf-8")
    else:
        # dry-run: write summaries to stdout only
        calibration_path = Path(f"<dry-run:calibration_{ts}.json>")
        outcomes_path    = Path(f"<dry-run:outcomes_{ts}.json>")
        summary_path     = Path(f"<dry-run:summary_{ts}.md>")

    return {
        "outcomes":     outcomes_path,
        "calibration":  calibration_path,
        "summary":      summary_path,
    }


def _ensure(dir: Path | None, name: str) -> Path:
    """Return dir / name if dir exists else a no-write Path for dry-run."""
    if dir:
        return dir / name
    return Path(f"<dry-run:{name}>")


# ─── Markdown renderer ──────────────────────────────────────────────────
def _render_summary_md(
    summary: BacktestRunSummary,
    calibration_data: Dict,
    outcomes_data: List,
) -> str:
    lines = [
        "# XAUUSD Backtest Summary",
        "",
        f"**Generated**: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
        f"**Spec**: horizons={summary.spec.horizons}, "
                  f"sources={summary.spec.sources}, "
                  f"from={summary.spec.from_date or 'all'}, "
                  f"to={summary.spec.to_date or 'all'}, "
                  f"limit={summary.spec.limit or 'all'}",
        f"**Verdict**: `{summary.verdict}`",
        "",
        "---",
        "",
        "## Signals & Coverage",
        "",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Signals loaded | {summary.n_signals_loaded} |",
        f"| Outcomes generated | {summary.n_outcomes} |",
    ]

    for reason, count in summary.skipped:
        lines.append(f"| Skipped ({reason}) | {count} |")

    # ── Horizon stats ──
    if summary.horizon_stats:
        lines += ["", "## Horizon Stats", "", "| Horizon | n | Hit Rate | Avg Signed Return |"]
        lines += ["|---|---|---|---|"]
        for h in sorted(summary.horizon_stats):
            s = summary.horizon_stats[h]
            lines.append(f"| {h}-bar | {int(s['n'])} | {s['hit_rate']:.1%} | {s['avg_signed_return']:+.4f} |")

    # ── Confidence calibration ──
    cal = calibration_data
    verdict = summary.verdict
    if verdict == VERDICT_INSUFFICIENT:
        lines += ["", "## Calibration — INSUFFICIENT_DATA", ""]
        lines.append(f"_n_total={cal['n_total']} (minimum 10 needed for calibration)_")
    else:
        lines += ["", "## Calibration", "", f"**ECE**: {cal['ece']:.4f}  **Brier**: {cal['brier']:.4f}", ""]
        lines += [
            "| Bucket | n | Hit Rate | Avg Signed Return | Avg Raw Return |",
            "|---|---|---|---|---|",
        ]
        for b in cal.get("buckets", []):
            if b["n"] > 0:
                lines.append(
                    f"| [{b['lo']:.1f},{b['hi']:.1f}) "
                    f"| {b['n']} | {b['hit_rate']:.1%} "
                    f"| {b['avg_signed_return']:+.4f} "
                    f"| {b['avg_raw_return']:+.4f} |"
                )

    # ── Filter utility ──
    lines += ["", "## Filter Utility", "",
              "### trade_candidate", "",
              "| Value | Hit Rate |", "|---|---|",]
    tc = cal.get("by_trade_candidate_hit_rate", {})
    for val_key in sorted(tc.keys(), key=lambda b: str(b)):
        lines.append(f"| {val_key} | {tc[val_key]:.1%} |")

    if cal.get("by_consensus_hit_rate"):
        lines += ["", "### consensus_label", "", "| Label | Hit Rate |", "|---|---|",]
        for label, hr in sorted(cal["by_consensus_hit_rate"].items()):
            lines.append(f"| {label} | {hr:.1%} |")

    if cal.get("by_conflict_hit_rate"):
        lines += ["", "### conflict_label", "", "| Label | Hit Rate |", "|---|---|",]
        for label, hr in sorted(cal["by_conflict_hit_rate"].items()):
            lines.append(f"| {label} | {hr:.1%} |")

    # ── Final verdict ──
    lines += ["", "---", "", "## Final Verdict", ""]
    if summary.n_outcomes == 0:
        lines.append("⚠️ **INSUFFICIENT_DATA** — no outcomes generated.")
        lines.append("Check: signals loaded? horizon within price window?")
    elif summary.verdict == VERDICT_INSUFFICIENT:
        lines.append(f"⚠️ **INSUFFICIENT_DATA** — n={summary.n_outcomes}, minimum 10 required.")
    else:
        ece = cal["ece"]
        brier = cal["brier"]
        lines.append(f"✅ **{summary.verdict}** — {summary.n_outcomes} outcomes, ECE={ece:.4f}, Brier={brier:.4f}.")
        if ece < 0.05:
            lines.append("📊 Confidence is well-calibrated (ECE < 0.05).")
        elif ece < 0.10:
            lines.append("📊 Confidence is moderately calibrated (ECE < 0.10).")
        else:
            lines.append("⚠️ Confidence may be mis-calibrated (ECE ≥ 0.10).")
        lines.append("")
        lines.append("*This is research/validation output — not investment advice.*")

    return "\n".join(lines)