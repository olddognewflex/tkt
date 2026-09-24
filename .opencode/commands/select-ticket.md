---
description: Discover and select the next ticket to work on via tkt
---

Discover and select the next ticket to work on: $ARGUMENTS

1. Verify `tkt doctor` passes and `tkt` is on PATH.
2. Identify current user: `tkt whoami`.
3. Run tiers 1–5 from `.sdlc/config.toml [queries]` until one returns selectable tickets:
   - `tkt list --tier N --json`
   - Filter out blocked tickets with `tkt blockers KEY --json`. Skip a tier only when it is undefined (`tkt cfg queries.tierN` exits 4); any other `tkt` failure stops selection with its error — never skip a failed tier or report nothing to work on. A candidate whose blocker check fails is excluded with a warning, never treated as unblocked.
4. If Tier 1 or 2 (assigned work): auto-select the first candidate, emit `SELECTED: <KEY>`, and proceed to `triage-ticket`.
5. If Tier 3/4/5: print up to 5 ranked recommendations and wait for a human pick.

After-hours deferral (optional): `tkt schedule --json` reports `label` and `in_window` for the `[schedule]` table (exit 2 = misconfigured: fail closed and defer); ask it in the same step that partitions the candidates. During business hours, candidates carrying the label are deferred — Tier 1/2 auto-select prefers non-deferred candidates, falling back to a deferred one only when no other exists (note it — deploy holds until after hours); Tier 3/4/5 never hard-filter — sort deferred last and mark the After-hours? column.

Recommendation table: Key | Type | type_class | Priority | Summary | Effort (S/M/L) | Est. tokens | Est. wall time | Blockers? | After-hours? | Why this one

Estimate guidance: S ≤ 100 LOC / ~50k tokens / ~30 min; M 100–400 LOC / ~150k tokens / ~2 h; L > 400 LOC / ~400k tokens / half-day.

Never auto-select unassigned work. All ticketing access goes through `tkt`.
