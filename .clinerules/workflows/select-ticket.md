# /select-ticket

Discover and select the next ticket to work on via `tkt`.

## Steps

1. Verify `tkt doctor` passes and `tkt` is on PATH.
2. Identify current user:
   ```shell
   tkt whoami
   ```
3. Run tiers 1–5 from `.sdlc/config.toml [queries]` until one returns selectable tickets.
4. Filter out blocked tickets with `tkt blockers KEY --json`.
5. **Tier 1/2** (assigned): auto-select, emit `SELECTED: <KEY>`, proceed to `triage-ticket`.
6. **Tier 3/4/5** (unassigned/backlog): print up to 5 ranked recommendations and wait for human pick.

## Recommendation format

| Key | Type | type_class | Priority | Summary | Effort | Est. tokens | Est. wall time | Blockers? | Why this one |

Estimate guidance: S ≤ 100 LOC / ~50k tokens / ~30 min; M 100–400 LOC / ~150k tokens / ~2 h; L > 400 LOC / ~400k tokens / half-day.

## Rules

- Never auto-select unassigned work.
- All ticketing access goes through `tkt`.
