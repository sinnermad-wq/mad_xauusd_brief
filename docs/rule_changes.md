# Engine Rule Changelog

> Append-only log of analytical rule and calibration changes.
> Each entry must be complete enough to reconstruct the state before the change.
> Delete nothing; mark superseded entries as `superseded` with a date and link to replacement.

---

## How to add an entry

Copy the template below, fill it out, and append it to this file.
Do not edit or delete existing entries.

```
## [YYYY-MM-DD] — [short title]
**Status:** Proposed | Approved | Rejected | Rolled Back
**Rule:** [which parameter or rule changed — be specific]
**Before:** [value or description before the change]
**After:**  [value or description after the change; omit if rejected]
**Requester:** [name]
**Approved by:** [name]
**Rationale:** [brief reason]
**Evidence:** [links, backtest notes, or observation that motivated the change]
**Rollback plan:** [how to undo this — omit if not applicable]

### Validation checkpoints
| Checkpoint | Date | Result | Recorded by |
|---|---|---|---|
| 1-week | YYYY-MM-DD | pass / fail — [brief note] | |
| 4-week | YYYY-MM-DD | pass / fail — [brief note] | |
| Monthly | YYYY-MM-DD | pass / fail — [brief note] | |
```

---

## Entries

<!-- Add new entries below this line — do not edit entries above this line -->