"""
Unified Engine Output Contract
V3 M2 — Integration Layer

所有 engine（Briefing / Candlestick / 未来 Fusion）的輸出都映射到此 contract。
用意：統一接口，方便 V4 Fusion Engine 批次讀取所有 engine 輸出。

DataFrame convention for candlestick: oldest-first (iloc[0]=oldest, iloc[-1]=newest).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class EngineOutput:
    """
    統一 engine 輸出 contract。

    Fields:
        engine_name   : engine 標識 ("candlestick" | "briefing")
        run_id        : 此次執行的唯一 ID（UUID4）
        symbol        : 合約代碼 "XAUUSD"
        timestamp     : 報告產生時間（ISO8601 UTC）
        timeframe     : 時間框架 "1D"
        bias          : "bullish" | "bearish" | "neutral"
        bias_strength : 0.0–1.0，M1 已有，candlestick 計算
                        （信號強度，唔等於 confidence）
        confidence    : None 或 0.0–1.0，V4/V5 計算
                        （跨 engine confidence，現階段 Candlestick=None）
        explanation_zh: 一句中文摘要
        data_quality_flag: "ok" | "degraded" | "no_data"
        analysis_window: bar count 字串，如 "14 bars"，日線 = "1D"
        source_payload: engine-specific 詳細輸出（M1 的完整 CandleAnalysis.to_dict()）
    """

    engine_name:    str
    symbol:         str
    timestamp:      str
    timeframe:      str

    bias:           str
    bias_strength:  float                          # M1 既有 strength
    confidence:     Optional[float] = None          # V4+ cross-engine confidence
    explanation_zh: str = ""

    data_quality_flag: str = "ok"
    analysis_window:   str = "1D"
    source_payload:    dict = field(default_factory=dict)
    run_id:            str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # V3 M5 — execution-ready schema (additive, optional, backward-compatible).
    schema_version:   str = "3.5"
    signal_id:        str = ""
    decision_ready:   bool = False
    trade_eligible:   bool = False
    execution_status: str = "not_sent"
    execution_mode:   str = "none"
    execution_intent: dict = field(default_factory=dict)

    # ── Computed properties ─────────────────────────────────────────────────

    @property
    def is_bullish(self) -> bool:
        return self.bias == "bullish"

    @property
    def is_bearish(self) -> bool:
        return self.bias == "bearish"

    @property
    def is_neutral(self) -> bool:
        return self.bias == "neutral"

    @property
    def is_confident(self) -> bool:
        """True if confidence >= 0.65 (when available)."""
        return self.confidence is not None and self.confidence >= 0.65

    @property
    def run_id_short(self) -> str:
        """Short run ID for display."""
        return self.run_id[:8]

    @property
    def data_quality_ok(self) -> bool:
        return self.data_quality_flag == "ok"

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        Convert to JSON-serializable dict.

        Includes all fields. run_id is always included.
        Candlestick Engine uses this as source_payload, so we keep it flat
        at top level for easy Fusion Engine access.
        """
        return {
            "engine_name":     self.engine_name,
            "run_id":         self.run_id,
            "symbol":         self.symbol,
            "timestamp":      self.timestamp,
            "timeframe":      self.timeframe,
            "bias":           self.bias,
            "bias_strength":  round(self.bias_strength, 4),
            "confidence":     (round(self.confidence, 4) if self.confidence is not None else None),
            "explanation_zh": self.explanation_zh,
            "data_quality_flag": self.data_quality_flag,
            "analysis_window":  self.analysis_window,
            "source_payload":  self.source_payload,
            # V3 M5 — execution-ready fields
            "schema_version":   self.schema_version,
            "signal_id":        self.signal_id,
            "decision_ready":   self.decision_ready,
            "trade_eligible":   self.trade_eligible,
            "execution_status": self.execution_status,
            "execution_mode":   self.execution_mode,
            "execution_intent": self.execution_intent,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EngineOutput":
        """Reconstruct from dict (e.g., when reading from history)."""
        return cls(
            engine_name    = data["engine_name"],
            run_id         = data.get("run_id", uuid.uuid4().hex[:12]),
            symbol         = data["symbol"],
            timestamp      = data["timestamp"],
            timeframe      = data["timeframe"],
            bias           = data["bias"],
            bias_strength  = data["bias_strength"],
            confidence     = data.get("confidence"),
            explanation_zh = data["explanation_zh"],
            data_quality_flag = data.get("data_quality_flag", "ok"),
            analysis_window   = data.get("analysis_window", "1D"),
            source_payload = data.get("source_payload", {}),
            # V3 M5 — backward-readable (old payloads omit these).
            schema_version   = data.get("schema_version", "3.5"),
            signal_id        = data.get("signal_id", ""),
            decision_ready   = data.get("decision_ready", False),
            trade_eligible   = data.get("trade_eligible", False),
            execution_status = data.get("execution_status", "not_sent"),
            execution_mode   = data.get("execution_mode", "none"),
            execution_intent = data.get("execution_intent", {}),
        )

    # ── Factories ───────────────────────────────────────────────────────────

    @classmethod
    def now_utc(cls) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    # ── V3 M5 Execution-ready helpers ──────────────────────────────────────

    # Constants used by execution_intent builders and ExecutionService contracts.
    EXECUTION_STATUSES = (
        "not_sent", "paper", "queued", "sent", "skipped", "expired",
    )
    EXECUTION_MODES = ("none", "paper", "live")
    STRATEGY_IDS = ("candlestick_v3", "fusion_v1")

    # ── Formatters (V3 M3) ───────────────────────────────────────────────

    _BIAS_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
    _STRUCTURE_EMOJI = {
        "uptrend": "📈",
        "downtrend": "📉",
        "range": "↔️",
        "transition": "🔄",
    }

    def to_telegram_zh(self) -> str:
        """Format EngineOutput as concise Telegram-friendly zh text.

        Layout:
          • Signal emoji + bias + strength
          • Structure line
          • RSI / ATR line
          • Patterns (top 3, zh desc)
          • Breakout state
          • Support / Resistance levels (top 3 each)
          • Footer: explanation + run_id
        """
        p = self.source_payload or {}
        emoji = self._BIAS_EMOJI.get(self.bias, "⚪")
        struct_emoji = self._STRUCTURE_EMOJI.get(
            p.get("structure_state", ""), "•"
        )

        lines: list[str] = []
        lines.append(f"{emoji} *XAUUSD Candlestick* — {self.bias.upper()}")
        lines.append(
            f"strength: *{self.bias_strength:.0%}* | "
            f"confidence: {'—' if self.confidence is None else f'{self.confidence:.0%}'}"
        )

        structure = p.get("structure_state", "—")
        rsi = p.get("rsi_14")
        atr = p.get("atr_14")
        struct_line = f"{struct_emoji} 結構：{structure}"
        if isinstance(rsi, (int, float)) and isinstance(atr, (int, float)):
            struct_line += f" | RSI: `{rsi:.1f}` | ATR: `{atr:.1f}`"
        lines.append(struct_line)

        patterns = p.get("detected_patterns", []) or []
        if patterns:
            pstr = " ".join(
                f"• {pat.get('description_zh', pat.get('name', '?'))}"
                for pat in patterns[:3]
            )
            lines.append(f"📊 型態：{pstr}")

        bs = p.get("breakout_state", {}) or {}
        if bs.get("breakout_confirmed"):
            direction = "向上" if bs.get("breakout_type", {}).get("value") == "break_up" else "向下"
            lines.append(f"🚀 突破：已確認 {direction}")
        elif bs.get("breakout_watch"):
            level = bs.get("breakout_watch_level")
            lvl_str = f" @ `{level:.1f}`" if isinstance(level, (int, float)) else ""
            lines.append(f"👁️ 觀察：突破警戒中{lvl_str}")

        supports = (p.get("support_levels") or [])[:3]
        resists = (p.get("resistance_levels") or [])[:3]
        if supports or resists:
            sr_parts: list[str] = []
            if supports:
                sr_parts.append(
                    "🟢 S: " + ", ".join(f"`{s:.1f}`" for s in supports)
                )
            if resists:
                sr_parts.append(
                    "🔴 R: " + ", ".join(f"`{r:.1f}`" for r in resists)
                )
            lines.append("  ".join(sr_parts))

        if self.explanation_zh:
            lines.append("")
            lines.append(self.explanation_zh)

        # V3 M4: validation status line (if present in payload)
        validation = p.get("validation") or {}
        if validation:
            v_status = validation.get("status", "—")
            v_emoji = {
                "ok": "🟢",
                "qualified_with_caution": "🟡",
                "degraded": "🟡",
                "invalid": "🔴",
            }.get(v_status, "⚪")
            conf_pct = (
                f"{self.confidence:.0%}" if self.confidence is not None else "—"
            )
            lines.append(
                f"\n{v_emoji} M4 Validation: `{v_status}` · confidence *{conf_pct}*"
            )

        lines.append(f"\n_run_id: `{self.run_id_short}` · {self.analysis_window}_")
        return "\n".join(lines)

    @classmethod
    def candlestick_now(cls) -> "EngineOutput":
        """Create a minimal empty EngineOutput for candlestick engine."""
        return cls(
            engine_name="candlestick",
            symbol="XAUUSD",
            timestamp=cls.now_utc(),
            timeframe="1D",
            bias="neutral",
            bias_strength=0.0,
            confidence=None,
            explanation_zh="",
        )

    # ── V3 M5 execution-ready helpers ─────────────────────────────────────

    # Threshold constants — tuneable via future config, exposed for tests.
    M5_CONFIDENCE_MIN: float = 0.6
    M5_DATA_QUALITY_OK: tuple[str, ...] = ("ok", "degraded")

    def build_execution_intent(
        self,
        *,
        strategy_id: str | None = None,
        risk_reward: tuple[float, float] | None = None,
        reason_codes: list[str] | None = None,
    ) -> dict:
        """Compose a JSON-serializable execution_intent dict.

        Pure mapper: bias → decision (`bullish` → long, `bearish` → short,
        otherwise `flat`). Stops short of placing any order — only builds
        the *contract* a future ExecutionService will read.

        Args:
            strategy_id:   one of `STRATEGY_IDS`; defaults to engine-based.
            risk_reward:   optional `(stop_distance, target_distance)` in price
                           units. If supplied, SL/TP fields are populated.
            reason_codes:  list of human-readable reason codes for audit trail.

        Returns:
            JSON-serializable dict; never None.
        """
        decision = {
            "bullish": "long",
            "bearish": "short",
            "neutral": "flat",
        }.get(self.bias, "none")

        if strategy_id is None:
            strategy_id = (
                "fusion_v1" if self.engine_name == "fusion"
                else "candlestick_v3"
            )

        intent: dict = {
            "symbol":         self.symbol,
            "decision":       decision,
            "confidence":    (
                round(self.confidence, 4) if self.confidence is not None else 0.0
            ),
            "strategy_id":    strategy_id,
            "timeframe":      self.timeframe,
            "entry_type":     None,    # market | limit | null — left null until V6+
            "stop_loss":      None,
            "take_profit":    None,
            "max_risk_pct":   None,
            "reason_codes":   reason_codes or [],
        }

        if risk_reward is not None and len(risk_reward) == 2:
            sl, tp = risk_reward
            intent["stop_loss"]   = round(float(sl), 4)
            intent["take_profit"] = round(float(tp), 4)

        return intent

    def populate_execution_fields(
        self,
        *,
        min_confidence: float | None = None,
        allow_degraded_data: bool = False,
    ) -> "EngineOutput":
        """Populate V3 M5 top-level execution fields in-place.

        Returns self for chaining; never raises (this is metadata population
        only — no side effects, no network).

        Decision rules:
          * decision_ready = True when (a) data_quality_flag in ok/degraded
            AND (b) confidence ≥ min_confidence AND (c) bias ∈ {bullish,bearish}.
          * trade_eligible  = decision_ready AND validation status ∉ {invalid,
                            hard_fail}  AND execution_status == "not_sent".
          * execution_status stays at "not_sent" (no service has acted yet).
          * execution_mode stays at "none" (still research-only).
          * signal_id      = derived as `sig-{run_id[:8]}` if blank.
        """
        min_conf = (
            min_confidence if min_confidence is not None
            else self.M5_CONFIDENCE_MIN
        )
        ok_quals = self.M5_DATA_QUALITY_OK if allow_degraded_data else ("ok",)

        # decision_ready: structural minimum
        confidence_ok = (
            self.confidence is not None and self.confidence >= min_conf
        )
        data_ok = self.data_quality_flag in ok_quals
        bias_directional = self.bias in ("bullish", "bearish")

        self.decision_ready = confidence_ok and data_ok and bias_directional

        # trade_eligible: cross-engine safety gate
        validation = (self.source_payload or {}).get("validation") or {}
        v_status = validation.get("status", "—")
        not_hard_failed = v_status not in {"invalid"}

        self.trade_eligible = (
            self.decision_ready
            and not_hard_failed
            and self.execution_status == "not_sent"
        )

        # signal_id (stable, derived once)
        if not self.signal_id:
            self.signal_id = f"sig-{self.run_id[:8]}"

        # Schema version: pin to 3.5 (this contract).
        self.schema_version = "3.5"

        # Build a default intent (skip risk_reward until V6+ risk gate).
        if not self.execution_intent:
            self.execution_intent = self.build_execution_intent(
                reason_codes=[
                    f"validation:{v_status}" if v_status != "—" else "validation:none",
                    f"confidence:{self.confidence:.2f}" if self.confidence is not None else "confidence:none",
                    f"data_quality:{self.data_quality_flag}",
                ],
            )

        return self