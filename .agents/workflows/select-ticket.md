---
name: select-ticket
description: Discover and select the next ticket to work on via tkt.
trigger: manual
---

# Select Ticket

Find the right ticket to work next. All ticketing access goes through `tkt`.

## Steps

1. Verify `tkt doctor` passes and `tkt` is on PATH.
2. Identify current user:
   ```shell
   // turbo
   tkt whoami
   ```
3. Run tiers 1–5 from `.sdlc/config.toml [queries]` until one returns selectable tickets:
   ```shell
   // turbo
   tkt list --tier N --json
   ```
4. Filter out blocked tickets with `tkt blockers KEY --json`.
5. **Tier 1/2** (assigned): auto-select, emit `SELECTED: <KEY>`, proceed to `triage-ticket`.
6. **Tier 3/4/5** (unassigned/backlog): print up to 5 ranked recommendations and wait for human pick.

## After-hours deferral (optional)

Configured via the `[schedule]` table and evaluated by `tkt schedule --json`, which reports `label` and `in_window` (exit 2 = misconfigured: fail closed and defer). Ask it in the same step that partitions the candidates, since that step may run in a fresh shell. During business hours, candidates carrying the label are deferred: Tier 1/2 auto-select picks the first non-deferred candidate, falling back to the first deferred one only when no other exists (note it — deploy holds until after hours); Tier 3/4/5 never hard-filter — sort deferred last and mark the `After-hours?` column.

## Recommendation format

| Key | Type | type_class | Priority | Summary | Effort | Est. tokens | Est. wall time | Blockers? | After-hours? | Why this one |

Estimate guidance: S ≤ 100 LOC / ~50k tokens / ~30 min; M 100–400 LOC / ~150k tokens / ~2 h; L > 400 LOC / ~400k tokens / half-day.

## Rules

- Never auto-select unassigned work.
- All ticketing access goes through `tkt`.
