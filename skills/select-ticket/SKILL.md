---
name: select-ticket
description: 'Discover and select the next ticket to work on, respecting priority, assignee, and blockers. Provider-agnostic via tkt. Auto-selects when the candidate is unambiguous; otherwise returns recommendations for human pick.'
allowed-tools: [Bash, Read]
model_tier: cheap
---

# Select Ticket

Entry point for the automated SDLC. Finds the right ticket to work next.
**All ticketing access goes through `tkt`** (the provider-agnostic CLI) — this
skill never calls Jira/GitHub/Linear directly. Backend + board shape come from
`.sdlc/config.toml`.

**Auto-selects at Tier 1 or Tier 2** (top candidate after the query's own sort;
first hit wins, never blocks on ties). Tiers 3–5 only ever return a ranked
recommendation list and wait for a human pick — the agent never auto-selects
unassigned work.

## Prerequisites

- `tkt doctor` passes (auth + board model reachable).
- `tkt` on PATH (or invoke via its path, e.g. `~/Development/tkt/tkt`).

## Selection tiers (stop at first non-empty)

Tiers map to named queries in `.sdlc/config.toml` `[queries]` (`tier1`…`tier5`).
The default mapping mirrors the original flow:

```
Tier 1: To Do + Highest priority + assigned to me   → auto-select
Tier 2: To Do + any priority     + assigned to me   → auto-select (query sorts by priority)
Tier 3: To Do + Highest priority + unassigned       → recommend (human picks)
Tier 4: To Do + any priority     + unassigned       → recommend (human picks)
Tier 5: Backlog                                       → recommend for promotion
```

A project that doesn't define a given tier query simply skips it (see Steps).

**After-hours deferral (optional):** when the config defines a `[schedule]` table,
candidates carrying its after-hours label are deferred during business hours, as
reported by `tkt schedule --json`. Auto-select picks one only when no other
candidate exists in the same tier, and the recommendation table sorts them last.
No `[schedule]` table → behavior unchanged.

## Steps

### 1. Identify current user

```shell
tkt whoami
```

`currentUser()` inside the tier queries is resolved by the provider, so no email
variable is needed.

### 2. Run tiers in order until one returns selectable tickets

```shell
TKT=tkt   # or the repo path, e.g. ~/Development/tkt/tkt

# Clear the previous run's selection first: step 3 reads these files, and a
# stale pair would auto-select a ticket from an earlier run.
rm -f /tmp/tkt_tier /tmp/tkt_candidates.json

# is_blocked KEY -> prints "1" if the ticket has unresolved blockers.
is_blocked() { [ "$("$TKT" blockers "$1" --json | jq 'length')" -gt 0 ] && echo 1; }

for N in 1 2 3 4 5; do
  # A defined-but-empty tier exits 0 with []; an undefined tier exits non-zero.
  OUT=$("$TKT" list --tier "$N" --json 2>/dev/null) || continue
  [ "$(echo "$OUT" | jq 'length')" -gt 0 ] || continue

  # Filter blockers per-candidate via `tkt blockers` (authoritative on every
  # backend — some list paths don't carry link data via the ticketing backend).
  SELECTABLE='[]'
  for K in $(echo "$OUT" | jq -r '.[].key'); do
    [ -n "$(is_blocked "$K")" ] && continue
    SELECTABLE=$(echo "$OUT" | jq --arg k "$K" --argjson acc "$SELECTABLE" \
      '$acc + [.[] | select(.key == $k)]')
  done
  [ "$(echo "$SELECTABLE" | jq 'length')" -gt 0 ] || continue

  echo "TIER=$N"
  echo "$N" > /tmp/tkt_tier       # the next step may run in a fresh shell
  echo "$SELECTABLE" > /tmp/tkt_candidates.json
  break
done
```

`tkt blockers` returns only **unresolved** blockers (a blocker that's Done is
dropped), so a non-empty result means genuinely blocked.

### 3. Auto-select (Tier 1 / Tier 2) or recommend (Tier 3+)

During business hours, candidates carrying the after-hours label are
**deferred**: worked only when no other candidate exists in the tier. (Anything
ahead of a deferred ticket is ≥ its priority, because the tier query already
sorts by priority.) The block is self-contained, since a harness may run it in a
fresh shell: it reads the tier and candidates from Step 2's files and asks
`tkt schedule` itself.

- **Tier 1 or 2** → pick the first selectable candidate (assigned work is implicit
  consent), emit `SELECTED: <KEY>`, hand off to `triage-ticket`.

  ```shell
  TKT=${TKT:-tkt}
  TIER=$(cat /tmp/tkt_tier 2>/dev/null) || TIER=""   # empty = step 2 found nothing
  # Exit 2 = [schedule] is misconfigured: fail closed and defer, loudly.
  if SCHED=$("$TKT" schedule --json); then
    AH_LABEL=$(echo "$SCHED" | jq -r '.label // ""')
    IN_WINDOW=$(echo "$SCHED" | jq -r '.in_window')
  else
    echo "WARNING: [schedule] is misconfigured (see above); deferring after-hours tickets" >&2
    AH_LABEL=$("$TKT" cfg schedule.after_hours_label 2>/dev/null) || AH_LABEL=""
    IN_WINDOW=true
  fi
  if [ -z "$TIER" ]; then
    NORMAL='[]'; DEFERRED='[]'
  elif [ -n "$AH_LABEL" ] && [ "$IN_WINDOW" = "true" ]; then
    NORMAL=$(jq --arg l "$AH_LABEL" '[.[] | select((.labels // []) | index($l) | not)]' /tmp/tkt_candidates.json)
    DEFERRED=$(jq --arg l "$AH_LABEL" '[.[] | select((.labels // []) | index($l))]' /tmp/tkt_candidates.json)
  else
    NORMAL=$(cat /tmp/tkt_candidates.json); DEFERRED='[]'
  fi

  if [ "$TIER" = "1" ] || [ "$TIER" = "2" ]; then
    KEY=$(echo "$NORMAL" | jq -r '.[0].key // empty')
    if [ -z "$KEY" ]; then
      # every candidate is after-hours: work may start now; deploy-ready holds
      # the actual deploy until outside business hours.
      KEY=$(echo "$DEFERRED" | jq -r '.[0].key // empty')
      [ -n "$KEY" ] && echo "NOTE: $KEY is after-hours; deploy will hold during business hours"
    elif [ "$(echo "$DEFERRED" | jq 'length')" -gt 0 ]; then
      echo "DEFERRED: $(echo "$DEFERRED" | jq -r '[.[].key] | join(", ")') (after-hours, in business hours)"
    fi
    if [ -n "$KEY" ]; then echo "SELECTED: $KEY"; fi
  fi
  ```

- **Tier 3 / 4 / 5** → print up to 5 ranked recommendations and stop. Do not
  transition. Include **Type** (and `type_class`) so the reader knows whether the
  ticket runs the full SDLC (`full_sdlc`) or the deliverable short-circuit.
  Never hard-filter after-hours candidates here (a human is picking): during
  business hours (`tkt schedule --json` reports `"in_window": true`), sort
  deferred candidates to the bottom and mark them in the `After-hours?` column.

### Recommendation output format

| Key | Type | type_class | Priority | Summary | Effort (S/M/L) | Est. tokens | Est. wall time | Blockers? | After-hours? | Why this one |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

Estimate guidance:

- **Effort**: S ≤ 100 LOC, M 100–400, L > 400 (infer from acceptance criteria).
- **Tokens**: S ≈ 50k, M ≈ 150k, L ≈ 400k.
- **Wall time**: S ≈ 30 min, M ≈ 2 h, L ≈ half-day (excl. human review).
- **Why this one**: one line citing priority, dependency readiness, or alignment.

For Tier 5, label them "promotion candidates — in backlog, not To Do; promote one
to To Do before work starts."

## Output

- `SELECTED: <KEY>` (auto path) → proceed to `triage-ticket`
- OR a recommendation table with a clear "waiting for pick" message
