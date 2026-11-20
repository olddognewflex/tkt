---
name: self-review
description: Adversarial self-review of changes before PR via tkt config.
trigger: manual
---

# Self-Review

Adversarial review of your own changes. Run AFTER implementation + tests pass, BEFORE opening a PR.

## Steps

1. Generate diff:
   ```shell
   // turbo
   git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD
   ```
   If the repo has a mex code graph (`.mex/graph.db` exists), run `mex impact <changed-file>`
   on the changed files: transitive callers outside the diff feed the breaking-change
   category and belong in the findings.
2. Review as adversary across: security, types, errors, tests, style, breaking changes, performance, edge cases.

   Routed review: first run `tkt cfg route self-review --json`. Exit 4: review in-session. Any other non-zero exit (2 = invalid routing table): stop and report it. On exit 0, send this pass out only when `source` is `route`, `harness` is set, and that harness (judged by the program `cmd` runs, not the key name) is not the one running; otherwise review in-session with the resolved `model`. The routed `cmd` stands in for the read-only reviewer and should be read-only; the prompt also forbids file changes. Write its prompt (these categories, the diff, the plan summary, an instruction to modify no files, and a required last line `TKT-RESULT: {"phase":"self-review","outcome":"advance|retry","reason":"...","findings":[...]}`) to a temp file `$PROMPT_FILE`, appending command output directly (`git diff ... >> "$PROMPT_FILE"`) and literal text only through a quoted heredoc (`<<'EOF'`) or a file-write tool, never inside a double-quoted shell string or an unquoted heredoc; over 100000 bytes, review in-session instead. In one shell call, re-run `tkt cfg route self-review --json` and capture its exit code before parsing (never pipe it straight into `jq`): exit 4 means review in-session, any other non-zero means stop and report, never fall back; only on exit 0 read `cmd` with `jq -r '.cmd // ""'`, replace its bare `{prompt}` with `"$(cat "$PROMPT_FILE")"`, and run the result as `PROMPT_FILE="$PROMPT_FILE" sh -c "$RUN" </dev/null` with a long timeout. The findings are the last stdout line starting `TKT-RESULT: `, if it holds exactly one JSON object for phase `self-review` with outcome `retry`, or `advance` (PASS) with no blocker or warning findings. Delete the temp file afterwards; it holds the diff. A non-zero exit, a killed call or no valid line means the routed review did not run: say so and review in-session; it is not a PASS. A pinned `model` never escalates. Fixes and the rebuild/re-test stay in-session.
3. List findings: file+line, severity (blocker/warning/nit), description, suggested fix.
4. Fix all blockers and warnings.
5. Rebuild/re-test:
   ```shell
   // turbo
   tkt cfg build.build --pkg "<pkg>"
   // turbo
   tkt cfg build.test --pkg "<pkg>"
   // turbo
   tkt cfg build.typecheck
   ```
6. Loop until zero blockers and warnings. Max 3 passes.

## Output

- Passes completed
- Issues found/fixed by severity
- Remaining nits
- Confidence: high / medium / low

## Rules

- Toolchain comes from `tkt cfg`.
- Max 3 passes; escalate if still finding blockers.
