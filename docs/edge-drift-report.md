# Edge Drift Report

Back-port audit of the 15 shared SDLC skills in this pack against their
`edge-dev-platform` originals (`.claude/skills/<name>/SKILL.md`).

**Baseline.** The pack's skill *content* was extracted on 2026-06-02
(`633f558`, `082de59`); harness mirrors on 2026-06-15 (`9500b11`); the
`--agent-status` schema lines landed later on 2026-06-23 (`14553a7`). Edge
commits at or before the 2026-06-02 15:21 content port are already reflected in
the baseline; the genuinely *post-port* edge skill changes are `6aa1f74`,
`4dd1882`, `3ed4e1d`, `d93b1fa`, and `5496d60`.

**Rule applied.** Back-port only *generic* pipeline/gate/wording improvements,
translated through the tkt abstraction (ticketing → `tkt` verbs; build → `tkt cfg
build.*`; lanes → roles). Reject anything Edge-specific: acli/Jira/Tempo,
DTB/board, FelipeFlow branch mechanics, Go/pnpm/turbo toolchain, Graphify,
grill-with-adrs, edge repo paths, Copilot-reviewer specifics, deploy-window
company policy.

---

## automated-sdlc

Edge commits since port: `c3f1ea7` (2026-06-02 10:25, 2-line policy alignment)
and `0cd02b1` (2026-06-02 12:39, "roll out grill-with-adrs across SDLC") — both
land *before* the 15:21 content port, so their state is in the baseline. No
post-port commits.

- **grill-with-adrs Phase 2.5** — *rejected (Edge-specific).* Adds a flagged
  `GRILL_ENABLE` phase that invokes the edge-only `grill-with-adrs` skill, files
  one DTB Task per NEW-ADR via `acli`, and branches on DTB issue types. Tightly
  coupled to ADRs + acli + DTB; the pack authors deliberately excluded it at port
  time. Not back-ported (would import edge tooling).

(Sanitization only: one illustrative "Jira/Tempo worklog" example reworded to
"your ticketing backend's worklog" — not a drift change.)

## select-ticket

Edge commit since port: `8b9ffc2` (2026-06-01, agent-config) — pre-port, in
baseline. No post-port drift. **No drift.** (Sanitization: one code comment "e.g.
Jira via acli" made generic.)

## triage-ticket

Edge commit since port: `5496d60` (2026-07-07, Go-first).

- **Go shared-lib note** — *rejected (Edge-specific).* Appends `libs/go/<name>`
  and `@edge/<name>-service` rows to the "map ticket content to monorepo
  packages" table. The pack keeps package mapping in the project's own conventions
  doc, not in the skill; the table itself is edge-specific. Not back-ported.

## plan-ticket

Edge commits since port: `6aa1f74` (2026-06-08, Graphify nav step), `4dd1882`
(2026-06-22, ADR renumber), `5496d60` (2026-07-07, Go paths).

- **Graphify navigation pre-step (1a)** — *rejected (Edge-specific).* Built
  entirely around Graphify, an edge-approved local knowledge-graph tool
  (ADR-0010, `graphify-out/graph.json`, `docs/approved-tools.md`, DTB-510). The
  generic kernel ("narrow the read set before reading files") is already covered
  by the pack's "keep this mapping in the project's own conventions doc" note.
- **ADR renumber `0005`→`0010`** — *rejected (Edge-specific).* Edge ADR
  bookkeeping; no pack analog.
- **Shared-lib path swaps** (`packages/shared/<lib>/src/`→`libs/<lib>/src/`,
  Go paths) — *rejected (Edge-specific).* Edge repo layout; the pack does not
  hardcode paths. Not back-ported. (Sanitization: one build-command example
  comment "e.g. pnpm turbo build" reworded generically.)

## open-pr

Edge commit since port: `c3f1ea7` (2026-06-02 10:25, "align repo with company
policies", 64 lines) — pre-port, in baseline. No post-port drift. The changes
are FelipeFlow branch/company-policy mechanics, which the pack abstracts via
`tkt cfg vcs.branch_fmt` / `tkt cfg vcs.reviewers` anyway. **No drift.**

## ci-fix

Edge commit since port: `5496d60` (2026-07-07, Go-first).

- **Go build/vet/golangci-lint rows + `pnpm turbo run go:build …` rebuild** —
  *rejected (Edge-specific, absorbed by abstraction).* The pack's ci-fix already
  rebuilds via `eval "$(tkt cfg build.build/test/typecheck)"` and lints via
  `tkt cfg build.lint`, which is language-agnostic and covers Go automatically
  through `.sdlc/config.toml`. The Go/pnpm specifics have no generic residue.
  Not back-ported.

## self-review

Edge commit since port: `5496d60` (2026-07-07, Go-first).

- **Go review criteria + Go rebuild commands** — *rejected (Edge-specific,
  absorbed by abstraction).* The added criteria (RFC 9457 via `libs/go/errors`,
  `%w` wrapping, `log/slog`, chi thin-handler, `go vet`/`golangci-lint`) are
  language-specific; the pack's self-review already defers all such rules to
  "the project's own conventions doc" and rebuilds via `tkt cfg build.*`. No
  generic residue. Not back-ported.

## respond-to-review

No edge commits since the port. **No drift.**

## deploy-preview

No edge commits since the port. **No drift.**

## deploy-ready

Edge commits since port: `8b9ffc2` (2026-06-01) and `e07b9d2` (2026-06-02 11:15,
"block prod deploys Mon/Tue/Wed") — both pre-port, in baseline.

- **Mon/Tue/Wed UTC prod-deploy freeze + `override_deploy_window`** — *rejected
  (Edge-specific).* An Auction Edge Deployment & Release Policy tied to
  `deploy-production.yml` and a specific weekday cadence. Not generic; the pack's
  deploy-ready deliberately omits it. Not back-ported.

## complete-deliverable

No edge commits since the port. **No drift.**

## resume-from-revise

Edge commit since port: `c3f1ea7` (2026-06-02 10:25, 2-line policy alignment) —
pre-port, in baseline. No post-port drift. **No drift.**

## check-blockers

Edge commit since port: `8b9ffc2` (2026-06-01, agent-config) — pre-port, in
baseline. **No drift.**

## hotfix-revert

Edge commit since port: `8b9ffc2` (2026-06-01, agent-config) — pre-port, in
baseline. **No drift.**

## sync-skills

Edge commits since port: `3ed4e1d` (2026-06-08, loader shell-token fix),
`d93b1fa` (2026-06-17, Codex parity).

- **Loader shell-token fix** (`3ed4e1d`) — *accepted; already present.* Edge
  reworded its OpenCode/Gemini bang-backtick examples so the skill loader would
  not eval `` !`<cmd>` `` as live shell injection. The pack's sync-skills already
  uses the safe form (`` `!<cmd>` `` / `` `!git status --short` `` inside code
  spans, no bang immediately preceding a backtick command), so no change was
  needed — the format-fix is effectively back-ported.
- **Codex parity** (`d93b1fa`) — *partially accepted.* Back-ported the generic
  bit: added **Codex** to the `.agents/skills/<name>/SKILL.md` "Used By" column
  and a note that "Codex consumes the `.agents/skills` copy directly — keep it
  byte-identical." Rejected the Edge-specific bits (`pnpm ai:sync` / `pnpm
  ai:check`, `.codex/rules/default.rules`, `.codex/hooks.json`,
  `.codex/agents/*.toml`) — the pack's Tier C/D already list Codex and
  `.codex/config.toml` generically.

sync-skills is meta → no harness mirrors.

---

## Summary

| Skill | Post-port drift? | Back-ported? |
|-------|------------------|--------------|
| automated-sdlc | grill-with-adrs (pre-port, in baseline) | no — Edge-specific |
| select-ticket | no | no |
| triage-ticket | Go shared-lib note | no — Edge-specific |
| plan-ticket | Graphify + ADR renumber + Go paths | no — Edge-specific |
| open-pr | no (pre-port policy align) | no |
| ci-fix | Go build/lint rows | no — absorbed by `tkt cfg build.*` |
| self-review | Go review criteria | no — absorbed by abstraction |
| respond-to-review | no | no |
| deploy-preview | no | no |
| deploy-ready | prod-freeze (pre-port) | no — Edge company policy |
| complete-deliverable | no | no |
| resume-from-revise | no | no |
| check-blockers | no | no |
| hotfix-revert | no | no |
| sync-skills | loader fix + Codex parity | yes — loader fix already safe; Codex `.agents/skills` note back-ported |

**Net back-port:** one generic clarification (Codex reads the `.agents/skills`
copy) into `sync-skills`. Every other post-port edge change is Edge-specific and
either rejected or already absorbed by the tkt abstraction. Separately, four
illustrative example strings were reworded for provider-agnosticism (sanitization,
not drift).
