# mad_xauusd_brief

Personal trading / macro brief toolkit for **XAUUSD (gold)** plus a daily
**Hong Kong morning briefing**. Experimental, single-user, local-first.

The repo produces:
- A daily XAUUSD brief (Trad. Chinese) delivered to Telegram at 08:30 HKT.
- A daily Hong Kong briefing delivered to WhatsApp at 09:00 HKT.
- A Streamlit dashboard for near-real-time XAUUSD bars, signal markers,
  price lines, and intraday price freshness.

## Features

- **Real-time XAUUSD chart** -- Polling market-data adapter + intrabar
  price freshness (30 s TTL).
- **Signal markers & price lines overlay** on the dashboard chart.
- **Intraday freshness indicator** -- HKT timestamp shown in chart caption;
  stale cache / delayed state labelled explicitly.
- **Crons** for XAUUSD and HK briefings, with graced fallbacks when
  upstream APIs fail.
- **Delivery** via Telegram (XAUUSD) and WhatsApp (HK).

## Quick Start

Requires Python 3.11 and [uv](https://github.com/astral-sh/uv).

```bash
uv venv --python 3.11
uv sync
uv run streamlit run src/daily_xauusd_brief/dashboard.py
```

Dashboard runs at <http://localhost:8501>.

To generate one daily brief locally (no delivery):

```bash
uv run python -m daily_xauusd_brief.main --dry-run
```

## Architecture / Milestones

- **Phase 1 -- phase1-dashboard-mvp** (2d389cd)
  Real bars via PollingMarketDataAdapter, signal markers overlay,
  price-lines overlay, dashboard wiring (render_streamlit_chart).
- **Phase 2A -- phase2a-intrabar** (9705342)
  get_price_info(ttl_seconds=30) intrabar price freshness,
  HKT freshness indicator in chart caption, quota/failure fallback
  to stale cache + delayed label.

## Crons / Briefings

- **XAUUSD cron** -- runs the pipeline in --dry-run and delivers the
  generated MD to Telegram.
- **HK Briefing cron** -- uses the m2.7 model and delivers to WhatsApp.
- Delivery channel IDs and API tokens are intentionally not stored in
  the repo (see below).

## Secrets / Safety

- Never commit API tokens (Twelve Data, GitHub PAT), Telegram bot
  tokens, or WhatsApp secrets.
- Use .env (loaded via python-dotenv),
  .streamlit/secrets.toml, or your shell environment.
- .gitignore excludes .env* and .streamlit/secrets.toml by design.

## Disclaimer

All content is research-only. Nothing here is investment advice and
no order-routing code is included.
