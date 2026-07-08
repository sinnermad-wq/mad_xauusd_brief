"""output.py — JSON/CSV/MD/text output for fusion engine.

Manual-only; no broker / execution / auto-trade.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.0"
HKT = timezone(timedelta(hours=8))


def _hkt_now() -> datetime:
    return datetime.now(HKT)


def _to_native(obj):
    """Convert numpy / pandas types for JSON serialization."""
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(x) for x in obj]
    return obj


def build_output(
    decision: str,
    decision_strength: str,
    context_score: float,
    price_action_score: float,
    environment_score: float,
    quality_score: float,
    confluence_score: float,
    directional_bias: str,
    bias_strength: str,
    market_regime: str,
    risk_state: str,
    entry_readiness: str,
    reasons: List[str],
    conflicts: List[str],
    warnings: List[str],
    inputs_used: List[str],
    missing_inputs: List[str],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _hkt_now().isoformat(),
        "timestamp": _hkt_now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "decision": decision,
        "decision_strength": decision_strength,
        "context_score": round(context_score, 1),
        "price_action_score": round(price_action_score, 1),
        "environment_score": round(environment_score, 1),
        "quality_score": round(quality_score, 1),
        "confluence_score": round(confluence_score, 1),
        "directional_bias": directional_bias,
        "bias_strength": bias_strength,
        "market_regime": market_regime,
        "risk_state": risk_state,
        "entry_readiness": entry_readiness,
        "reasons": reasons,
        "conflicts": conflicts,
        "warnings": warnings,
        "inputs_used": inputs_used,
        "missing_inputs": missing_inputs,
    }
    if extra:
        out.update(extra)
    return out


def write_json(output: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_fusion.json"
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(_to_native(output), f, indent=2, ensure_ascii=False)
    return fpath


def write_csv(output: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_fusion.csv"

    flat: Dict[str, Any] = {
        "timestamp":          output["timestamp"],
        "decision":          output["decision"],
        "decision_strength":  output["decision_strength"],
        "confluence_score":   output["confluence_score"],
        "context_score":      output["context_score"],
        "price_action_score": output["price_action_score"],
        "environment_score":  output["environment_score"],
        "quality_score":      output["quality_score"],
        "directional_bias":   output["directional_bias"],
        "bias_strength":      output["bias_strength"],
        "market_regime":      output["market_regime"],
        "risk_state":         output["risk_state"],
        "entry_readiness":    output["entry_readiness"],
        "reasons":           "|".join(output["reasons"]),
        "conflicts":          "|".join(output["conflicts"]),
        "warnings":          "|".join(output["warnings"]),
        "inputs_used":        "|".join(output["inputs_used"]),
        "missing_inputs":     "|".join(output["missing_inputs"]),
    }
    # Passthrough
    for k in ("_candle_close", "_candle_direction_bias", "_candle_primary_state"):
        if k in output:
            flat[k] = str(output[k])

    import os
    fieldnames = list(flat.keys())
    file_exists = os.path.exists(fpath)
    with open(fpath, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        w.writerow(flat)
    return fpath


def write_markdown(output: Dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _hkt_now().strftime("%Y%m%d_T%H%M")
    fpath = out_dir / f"{ts}_fusion.md"

    dec = output["decision"]
    emoji = {
        "long_watch":  "🟢 LONG WATCH",
        "short_watch": "🔴 SHORT WATCH",
        "wait":        "⚪ WAIT",
        "no_trade":    "🚫 NO TRADE",
    }.get(dec, dec)

    lines = [
        f"# Fusion Engine Decision Report",
        f"",
        f"**{output['timestamp'][:10]}** | **GC=F** | Fusion Engine v{SCHEMA_VERSION}",
        f"",
        f"## Decision",
        f"",
        f"| | |",
        f"|---|---|",
        f"| Decision | **{emoji}** |",
        f"| Strength | `{output['decision_strength']}` |",
        f"| Bias | `{output['directional_bias']}` ({output['bias_strength']}) |",
        f"| Entry | `{output['entry_readiness']}` |",
        f"",
        f"## Scores",
        f"",
        f"| Score | Value |",
        f"|---|---|",
        f"| Confluence | **{output['confluence_score']:.1f}** / 100 |",
        f"| Context | {output['context_score']:.1f} / 100 |",
        f"| Price Action | {output['price_action_score']:.1f} / 100 |",
        f"| Environment | {output['environment_score']:.1f} / 100 |",
        f"| Quality | {output['quality_score']:.1f} / 100 |",
        f"",
        f"## Context",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| Regime | `{output['market_regime']}` |",
        f"| Risk | `{output['risk_state']}` |",
    ]

    if output["reasons"]:
        lines += [
            f"",
            f"## Reasons",
            f"",
            *(f"- `{r}`" for r in output["reasons"]),
        ]

    if output["conflicts"]:
        lines += [
            f"",
            f"## Conflicts ({len(output['conflicts'])})",
            f"",
            *(f"- ⚠️ `{c}`" for c in output["conflicts"]),
        ]

    if output["warnings"]:
        lines += [
            f"",
            f"## Warnings",
            f"",
            *(f"- ⚠️ `{w}`" for w in output["warnings"]),
        ]

    lines += [
        f"",
        f"## Inputs",
        f"- Used: {', '.join(output['inputs_used']) or 'none'}",
        f"- Missing: {', '.join(output['missing_inputs']) or 'none'}",
        f"",
        f"---",
        f"*Generated: {output['generated_at']} | Schema v{SCHEMA_VERSION}*",
    ]

    with open(fpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return fpath


def format_text_summary(output: Dict[str, Any]) -> str:
    dec = output["decision"]
    emoji = {
        "long_watch":  "🟢",
        "short_watch": "🔴",
        "wait":        "⚪",
        "no_trade":    "🚫",
    }.get(dec, "⚪")

    lines = [
        f"{emoji} **Fusion Decision: {dec.upper()}** ({output['decision_strength']})",
        f"   Bias: {output['directional_bias']} ({output['bias_strength']}) | "
        f"Confluence: {output['confluence_score']:.0f}%",
        f"   Regime: {output['market_regime']} | Risk: {output['risk_state']} | "
        f"Entry: {output['entry_readiness']}",
        f"   Ctx={output['context_score']:.0f} PA={output['price_action_score']:.0f} "
        f"Env={output['environment_score']:.0f} Qual={output['quality_score']:.0f}",
    ]

    if output["reasons"][:3]:
        lines.append(f"   Top: {output['reasons'][0]}")
    if output["conflicts"]:
        lines.append(f"   Conflicts: {len(output['conflicts'])} — {output['conflicts'][0]}")
    if output["warnings"]:
        lines.append(f"   Warnings: {len(output['warnings'])}")

    return "\n".join(lines)