# Strategy Health Guardrails v1

> Hard limits enforced by design. No execution logic. No auto-parameter change.

## Absolute prohibitions

1. **No auto-trade** — `strategy_health/` module never submits, cancels, or
   modifies orders. It has zero access to broker or exchange APIs.

2. **No auto-parameter change** — Suggestions are analytical output only.
   The engine computes and emits; it never writes to config files,
   fusion rules, or backtest parameters.

3. **No credential storage** — The module does not store or transmit
   brokerage credentials, API keys, or wallet addresses.

4. **No recursive cron** — A strategy-health cron job must never schedule
   another strategy-health job.

## Design guarantees

| What it does | What it does NOT do |
|---|---|
| Reads backtest JSON files | Modifies backtest config |
| Reads fusion_history JSON files | Sends orders to any broker |
| Computes diagnostics + suggestions | Writes to fusion rules |
| Returns frozen `StrategyHealthSnapshot` | Auto-adjusts strategy parameters |
| Reads/writes `approvals.json` (manual-edit file) | Auto-approves suggestions |
| Checks file mtimes for freshness | Triggers trading webhooks |
| Returns health verdict (green/yellow/red) | Auto-disables sessions |

## Approval state machine

```
pending → approved  → human reviews suggestion, explicitly approves
pending → rejected  → human reviews, explicitly rejects
pending → superseded → same suggestion re-appeared; previous resolution stands
* → pending (never auto-reset)
```

**Approved / rejected / superseded states are permanent for that
`suggestion_id`.** The engine will not re-open a resolved suggestion.

## When to restart from scratch

If the strategy has been fundamentally changed (new indicators, new
session filters, new symbol), discard the old approvals file:

```bash
rm data/strategy_health/approvals.json
```

Old suggestion IDs will never match new suggestions, so the old file
acts as noise.

## Dashboard integration

The dashboard block in `dashboard.py` is **read-only**. It surfaces
`snapshot.health_status`, `snapshot.diagnostics`, and
`snapshot.pending_approvals`. The only write from the dashboard side is
manual editing of `approvals.json` by the user directly.

## Logging

Diagnostic runs log to `logs/strategy_health.log`. Each run records:
- snapshot_id
- health_status
- suggestion count and kinds
- computation time (ms)

No sensitive data is written to logs.

## Red lines (will not be added in future versions without AGENTS.md change)

- Broker API integration (MT4/MT5/Binance/IB)
- Auto position sizing based on health verdict
- Auto session disabling without human approval
- Auto parameter backfill from health metrics
- Push notifications that require auto-decision