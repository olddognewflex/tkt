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
   - Include: Key, Type, type_class, Priority, Summary, Effort (S/M/L), Est. tokens, Est. wall time, Blockers?, Why this one
   - Wait for human pick

## Rules

- Never auto-select unassigned work
- All ticketing access goes through `tkt`
- `type_class` (`full_sdlc` or `deliverable`) determines downstream routing
