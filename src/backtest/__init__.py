"""backtest/ — Backtest & Scalping Research Module v1.

Structure:
  engine.py     — signal processing, trade execution, cost modeling
  strategies.py — strategy registry + signal generators
  metrics.py    — performance & risk metrics
  reporting.py  — CSV + Markdown report generation

Manual-only: no broker / execution / cron / dashboard wiring.
"""

from __future__ import annotations