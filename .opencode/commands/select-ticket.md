---
description: Discover and select the next ticket to work on via tkt
---

Discover and select the next ticket to work on: $ARGUMENTS

1. Verify `tkt doctor` passes and `tkt` is on PATH.
2. Identify current user: `tkt whoami`.
3. Run tiers 1–5 from `.sdlc/config.toml [queries]` until one returns selectable tickets:
   - `tkt list --tier N --json`
   - Filter out blocked tickets with `tkt blockers KEY --json`.
4. If Tier 1 or 2 (assigned work): auto-select the first candidate, emit `SELECTED: <KEY>`, and proceed to `triage-ticket`.
5. If Tier 3/4/5: print up to 5 ranked recommendations and wait for a human pick.

Recommendation table: Key | Type | type_class | Priority | Summary | Effort (S/M/L) | Est. tokens | Est. wall time | Blockers? | Why this one

Estimate guidance: S ≤ 100 LOC / ~50k tokens / ~30 min; M 100–400 LOC / ~150k tokens / ~2 h; L > 400 LOC / ~400k tokens / half-day.

Never auto-select unassigned work. All ticketing access goes through `tkt`.
