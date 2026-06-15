---
mode: agent
description: 'End-to-end SDLC pipeline from ticket selection to shipped via tkt.'
tools: ['terminal', 'file']
---

# Automated SDLC

End-to-end pipeline from "what should I work on?" to "shipped." Orchestrates sub-skills with explicit human gates and lane-time annotation.

## Pipeline

1. **select-ticket** — find next work
2. **triage-ticket** → `in_progress`
3. Route by `type_class`:
   - `full_sdlc` → continue
   - `deliverable` → **complete-deliverable**, then stop
4. **plan-ticket** — implementation plan
5. Implement + test (build/test via `tkt cfg build.*`)
6. **self-review** — adversarial review loop
7. **open-pr** → `review`
8. **ci-fix** — green CI loop
9. **respond-to-review** — approve loop
10. **deploy-preview** — confirm preview URL
11. Promote to `qa_ready` and stop for human QA
12. **deploy-ready** — merge, staging, prod gate

## Rules

- All ticketing via `tkt`.
- All repo/toolchain values via `tkt cfg`.
- Lane times annotated with `tkt worklog` (no-op when time tracking disabled).
