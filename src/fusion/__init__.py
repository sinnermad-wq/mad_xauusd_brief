"""fusion/ — Fusion Engine v1.

Combines briefing/refresh + candlestick engine into a single decision object.
Read-only confluence engine; no broker / execution / auto-trade.

Structure:
  engine.py   — fusion scoring + decision logic
  output.py   — JSON/CSV/MD/text reports
"""

from __future__ import annotations