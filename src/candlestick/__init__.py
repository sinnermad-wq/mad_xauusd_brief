"""candlestick/ — Candlestick Direction Engine v1.

Rules-based, manual-only, scalping-friendly XAUUSD K線 direction analysis.

Structure:
  features.py  — candle feature calculations
  patterns.py  — basic pattern detection
  structure.py — HH/HL/LH/LL, breakouts, sweeps
  states.py    — state machine (direction/momentum/rejection/range/structure/sequence)
  scores.py    — scoring system
  engine.py    — main engine combining all components
  output.py    — JSON/CSV/MD output

Manual-only: no broker / execution / auto-trade / Telegram auto-signal.
"""

from __future__ import annotations