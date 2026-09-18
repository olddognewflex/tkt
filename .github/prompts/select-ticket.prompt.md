---
mode: agent
description: 'Discover and select the next ticket to work on via tkt.'
tools: ['terminal']
---

# Select Ticket

Find the right ticket to work next. All ticketing access goes through `tkt`.

## Prerequisites

- `tkt doctor` passes.
- `tkt` on PATH.

## Steps

1. Identify current user:
   ```shell
   tkt whoami
   ```
2. Run tiers 1–5 from `.sdlc/config.toml [queries]` until one returns selectable tickets:
   ```shell
   tkt list --tier N --json
   ```
3. Filter out blocked tickets with `tkt blockers KEY --json`.
4. **Tier 1 or 2** (assigned work): auto-select the first candidate, emit `SELECTED: <KEY>`, and hand off to `triage-ticket`.
5. **Tier 3–5** (unassigned/backlog): print up to 5 ranked recommendations and wait for a human pick.

## After-hours deferral (optional)

Configured via the `[schedule]` table: `tkt cfg schedule.after_hours_label` (exit 4 = no table = feature off), `schedule.business_hours` (HH:MM-HH:MM; start > end = overnight window), `schedule.timezone` (IANA), `schedule.days`. Compute in/out of window once before the tier loop. During business hours, candidates carrying the label are deferred: Tier 1/2 auto-select picks the first non-deferred candidate, falling back to the first deferred one only when no other exists (note it — deploy holds until after hours); Tier 3–5 never hard-filter — sort deferred last and mark the `After-hours?` column.

## Recommendation output

| Key | Type | type_class | Priority | Summary | Effort | Est. tokens | Est. wall time | Blockers? | After-hours? | Why this one |

- Effort: S ≤ 100 LOC, M 100–400, L > 400.
- Tokens: S ≈ 50k, M ≈ 150k, L ≈ 400k.
- Wall time: S ≈ 30 min, M ≈ 2 h, L ≈ half-day.

## Rules

- Never auto-select unassigned work.
- `type_class` tells you if the ticket is `full_sdlc` or `deliverable`.
