# /select-ticket

Discover and select the next ticket to work on via `tkt`.

## Steps

1. Verify prerequisites:
   - `tkt doctor` passes
   - `tkt` on PATH
2. Identify current user:
   ```shell
   tkt whoami
   ```
3. Run tiers 1–5 in order until one returns selectable tickets:
   ```shell
   tkt list --tier N --json
   ```
   Filter out blocked tickets with `tkt blockers KEY --json`.
4. **Tier 1 or 2** (assigned work):
   - Emit `SELECTED: <KEY>`
   - Hand off to `triage-ticket`
5. **Tier 3–5** (unassigned/backlog):
   - Print up to 5 ranked candidates
   - Include: Key, Type, type_class, Priority, Summary, Effort (S/M/L), Est. tokens, Est. wall time, Blockers?, After-hours?, Why this one
   - Wait for human pick

## After-hours deferral (optional)

Configured via the `[schedule]` table and evaluated by `tkt schedule --json`, which reports `label` and `in_window` (exit 2 = misconfigured: fail closed and defer). Ask it in the same step that partitions the candidates, since that step may run in a fresh shell. During business hours, candidates carrying the label are deferred: Tier 1/2 auto-select picks the first non-deferred candidate, falling back to the first deferred one only when no other exists (note it — deploy holds until after hours); Tier 3–5 never hard-filter — sort deferred last and mark the After-hours? column.

## Rules

- Never auto-select unassigned work
- All ticketing access goes through `tkt`
- `type_class` (`full_sdlc` or `deliverable`) determines downstream routing
