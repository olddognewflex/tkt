# Behavior specs (BDD) — the portable practice

This is the **process** half of behavior-driven development, owned by the SDLC
pack so it is reusable across repos and languages. The concrete test **harness**
(godog for Go, Cucumber.js/vitest-cucumber for TypeScript, pytest-bdd for Python,
…) stays repo-owned and is bound through one config key — `build.bdd`. The pack
never hardcodes a tool, exactly as it never hardcodes a ticketing backend.

## Why this lives in the pack

The pack's whole design is "one practice, many backends": skills speak semantic
verbs and resolve the concrete command from `.sdlc/config.toml`. Behavior specs
fit the same shape — the *methodology* (Gherkin + how CI treats it + how work is
enabled per ticket) is identical everywhere; only the *runner* differs. Putting
the methodology here lets a TypeScript app, a CLI tool, and a Go service all get
the same executable-spec workflow without the pack ever depending on a harness.

## The practice

### 1. One feature file per capability

Describe behavior in Gherkin (`Given` / `When` / `Then`), one `.feature` file per
functional capability — not one per ticket. A single ticket may touch several
features, and a feature may span several tickets. Write the **full** expected
behavior up front so the spec is a living contract, even before any of it runs.

### 2. Non-strict until wired, then promote to strict

Run the suite **non-strict** by default: undefined / pending steps are reported
but do **not** fail CI. This lets the complete feature set be authored early and
wired incrementally as implementation lands, without turning CI red on scenarios
nobody has implemented yet. Once every step for a capability is wired, promote
that suite (or the whole repo) to **strict** so undefined steps fail — locking in
the behavior against regression.

### 3. Enable per ticket

As each ticket is implemented:

1. Identify which existing `.feature` scenarios cover the behavior.
2. Write the step definitions against the real code (or in-memory fakes at
   boundaries — see below).
3. Wire them in (register the steps), so those scenarios start enforcing.
4. Run the suite: the new scenarios pass; everything else stays pending (green).
5. Push — CI stays green because undefined steps don't fail.

### 4. Boundaries are interfaces with in-memory fakes

Behavior specs pair naturally with keeping every external system (DB, queue,
HTTP) behind an interface with an in-memory fake for tests. Steps exercise the
real internal logic against fakes; concrete backends land behind the existing
interface without touching call sites. This keeps the suite fast and hermetic and
complements — does not replace — real-datastore integration tests.

## The `build.bdd` hook

Each repo binds its runner under `[build]` in `.sdlc/config.toml`:

```toml
[build]
build     = "..."
test      = "..."
typecheck = "..."
lint      = "..."
bdd       = "go test ./features/..."   # optional; the repo's behavior-spec runner
```

Skills resolve it the same way they resolve every other build command:

```sh
tkt cfg build.bdd --pkg "<pkg>"
```

- **Optional.** `build.bdd` is absent by default. When the key is unset,
  `tkt cfg build.bdd` prints `tkt: config key not found: build.bdd` to stderr and
  exits `4` (`NotFoundError`). Skills treat **exit 4 specifically** as the clean
  no-op — a repo with no behavior specs is unaffected. Any other non-zero exit
  (e.g. `2` = a broken/missing `.sdlc/config.toml`) is a real error and is
  surfaced rather than swallowed, so a misconfiguration never hides as a silent skip.
- **Non-blocking.** Where a skill runs `build.bdd` (e.g. `self-review`), it runs
  it as a **reported, non-blocking** signal, mirroring the non-strict CI posture:
  pending scenarios inform the author but never block the flow. A repo makes BDD
  blocking by pointing `build.bdd` at a strict runner once its steps are wired.

## Adopting in a repo

1. Add a `bdd` command under `[build]` in `.sdlc/config.toml` (see the
   `examples/config.*.toml` files).
2. Stand up the harness for your stack (the actual `.feature` files + step
   runner). That harness — and its lint/build wiring — is repo-owned; record the
   binding decision in a repo ADR.
3. Author feature files per capability; enable them per ticket as above.

Typical bindings, for reference:

| Stack | Runner | `build.bdd` |
|-------|--------|-------------|
| Go | godog, `features/` + non-strict `features_test.go` | `go test ./features/...` |
| TypeScript | Cucumber.js or vitest-cucumber | `npm run bdd` |
| Python | pytest-bdd | `pytest tests/features` |

The pack has no opinion beyond the key: whatever the command is, it must exit
non-zero only when a *wired* scenario fails.
