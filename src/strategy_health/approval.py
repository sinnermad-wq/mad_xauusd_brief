"""Human approval store — manual-resolution only.

The approvals file lives at ``data/strategy_health/approvals.json``.
It is append-only and human-edited.

Workflow:
  1. Engine computes suggestions, builds pending_approvals.
  2. Snapshot is returned to caller (dashboard / caller logs it).
  3. User reviews the approvals file manually.
  4. User edits status to: approved / rejected / superseded.
  5. Engine on next run reads updated status — never auto-applies.

NO auto-apply. NO broker execution. NO parameter changes from here.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Tuple

from .models import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    APPROVAL_SUPERSEDED,
    VALID_APPROVAL_STATUSES,
    StrategyApprovalState,
)


APPROVAL_FILE = "data/strategy_health/approvals.json"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d)


def load_approvals(path: str = APPROVAL_FILE) -> Dict[str, Mapping[str, Any]]:
    """Load existing approval states by suggestion_id. Returns empty dict if missing."""
    if not os.path.exists(path):
        return {}
    try:
        return json.loads(open(path).read())
    except Exception:
        return {}


def write_approvals(
    approvals: Dict[str, Mapping[str, Any]],
    path: str = APPROVAL_FILE,
) -> None:
    """Write the full approvals dict to disk (atomic-ish via temp file)."""
    _ensure_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(approvals, fh, indent=2)
    os.replace(tmp, path)


def upsert_approval(
    state: StrategyApprovalState,
    path: str = APPROVAL_FILE,
) -> None:
    """Insert or update one approval record in the approvals file."""
    approvals = load_approvals(path)
    approvals[state.suggestion_id] = state.to_dict()
    write_approvals(approvals, path)


def get_pending(
    path: str = APPROVAL_FILE,
) -> List[StrategyApprovalState]:
    """Return all currently pending approval records."""
    approvals = load_approvals(path)
    pending: List[StrategyApprovalState] = []
    for key, val in approvals.items():
        if val.get("status") == APPROVAL_PENDING:
            pending.append(StrategyApprovalState(
                suggestion_id=key,
                kind=val.get("kind", ""),
                status=APPROVAL_PENDING,
                created_at=val.get("created_at", ""),
                updated_at=val.get("updated_at", ""),
                note=val.get("note", ""),
                resolved_by=val.get("resolved_by", ""),
            ))
    return pending


def resolve_approval(
    suggestion_id: str,
    new_status: str,
    note: str = "",
    resolved_by: str = "user",
    path: str = APPROVAL_FILE,
) -> None:
    """Mark an approval record as resolved. Caller responsible for validation."""
    if new_status not in VALID_APPROVAL_STATUSES:
        raise ValueError("Invalid status: " + new_status)
    approvals = load_approvals(path)
    now = _now()
    if suggestion_id in approvals:
        approvals[suggestion_id]["status"] = new_status
        approvals[suggestion_id]["updated_at"] = now
        approvals[suggestion_id]["note"] = note
        approvals[suggestion_id]["resolved_by"] = resolved_by
    else:
        approvals[suggestion_id] = {
            "suggestion_id": suggestion_id,
            "kind": "",
            "status": new_status,
            "created_at": now,
            "updated_at": now,
            "note": note,
            "resolved_by": resolved_by,
        }
    write_approvals(approvals, path)


def build_approval_state_from_suggestion(
    sug,
    existing_status: str = APPROVAL_PENDING,
    created_at: str = "",
    updated_at: str = "",
) -> StrategyApprovalState:
    """Construct an approval state from a StrategySuggestion."""
    now = _now()
    return StrategyApprovalState(
        suggestion_id=sug.suggestion_id,
        kind=sug.kind,
        status=existing_status,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


def diff_approvals(
    suggestions: Tuple[StrategySuggestion, ...],
    approvals: Dict[str, Mapping[str, Any]],
) -> Tuple[List[StrategySuggestion], List[StrategyApprovalState]]:
    """Separate suggestions into new (no prior approval) vs superseded (already resolved)."""
    new_suggestions: List[StrategySuggestion] = []
    still_pending: List[StrategyApprovalState] = []

    for sug in suggestions:
        rec = approvals.get(sug.suggestion_id)
        if rec is None:
            new_suggestions.append(sug)
        elif rec.get("status") == APPROVAL_PENDING:
            still_pending.append(StrategyApprovalState(
                suggestion_id=sug.suggestion_id,
                kind=sug.kind,
                status=APPROVAL_PENDING,
                created_at=rec.get("created_at", ""),
                updated_at=rec.get("updated_at", ""),
                note=rec.get("note", ""),
                resolved_by=rec.get("resolved_by", ""),
            ))
        # resolved entries are simply dropped — superseded automatically

    return tuple(new_suggestions), tuple(still_pending)