---
name: self-review
description: 'Adversarial self-review of changes before PR. Reviews diff, finds issues, fixes them, loops until clean. Build commands via tkt config; no ticketing.'
model_tier: standard
---

# Self-Review

Adversarial review of your own changes. Run AFTER implementation + tests pass,
BEFORE opening a PR. Loops until no blockers remain. Toolchain comes from
`.sdlc/config.toml` via `tkt cfg`; this skill has no ticketing coupling.

## Steps

### 1. Generate the diff

```shell
git diff $(git merge-base HEAD origin/$(tkt cfg vcs.default_branch))...HEAD
```

If the repo has a mex code graph (`.mex/graph.db` exists), run
`mex impact <changed-file>` on the changed files: transitive callers *outside*
the diff feed the **Breaking** row below and belong in the findings.

### 2. Review as adversary (→ `sdlc-reviewer`)

**Delegate to `sdlc-reviewer`**, passing the diff and the plan/ticket summary. The
point is separation: the reviewer is a fresh context that did not write the code,
so it judges the evidence rather than recalling the intent.

**Fallback** (no subagent support): run this pass yourself, but explicitly — re-read
the diff from scratch and argue against it before approving. Pretend it is a
stranger's PR. Do not rubber-stamp your own code.

**Routed review.** The project may send this pass to another harness or model.
Resolve it first:

```shell
R=$(tkt cfg route self-review --json); rc=$?
```

Exit 4: nothing is configured, so review in-session as above. Any other non-zero
exit (2 = invalid routing table): stop and report it. On exit 0, send the pass
out only when `source` is `route`, `harness` is set, and that harness is not the
one you are running in. Judge that by the program `cmd` runs, not by the
`harness` key, which is any name the project chose. Otherwise review in-session,
giving the subagent the resolved `model` when this harness can select one.

A routed reviewer stands in for the read-only `sdlc-reviewer` agent, so its
`cmd` should be a read-only invocation. The prompt also tells it to modify no
file, which is the only guard when the project gives this route a write-capable
`cmd` for `tkt run` (where the route runs the whole phase, fixes included).
Write the prompt to a file: the reviewer instructions in this step and step 3,
the diff, the plan/ticket summary, an instruction not to modify any file, and
the result line it must end with:

```
TKT-RESULT: {"phase":"self-review","outcome":"advance|retry","reason":"<one-line verdict>","findings":[{"file":"...","line":0,"severity":"blocker|warning|nit","description":"...","fix":"..."}]}
```

`advance` is PASS (zero blockers, zero warnings); `retry` is anything else. Then
run the block below **as one shell invocation**; it resolves the route again
itself, so it does not depend on an earlier call's variables. Its exit code says
what to do next: 0, the findings are on stdout; 1 or 4, review in-session (stderr
says why); 2, the resolver failed, so stop and report it, as above. It blocks for the
whole review, so give the call the longest timeout available, or run it in the
background (`[run].invocation_timeout` is how long `tkt run` allows). A call the
shell tool kills is a failed run, like any other below.

```shell
(
# Capture the resolver's own exit code: piping it straight into jq would
# hide a config error behind an empty cmd.
ROUTE=$(tkt cfg route self-review --json); trc=$?
if [ "$trc" -eq 4 ]; then
  echo "self-review: no route configured; reviewing in-session" >&2
  exit 4
elif [ "$trc" -ne 0 ]; then
  echo "self-review: tkt cfg route failed (exit $trc); stopping, fix the routing config" >&2
  exit 2
fi
CMD=$(printf '%s\n' "$ROUTE" | jq -r '.cmd // ""')
PROMPT_FILE=$(mktemp)
trap 'rm -f "$PROMPT_FILE"' EXIT
# ... write the reviewer prompt into "$PROMPT_FILE" ...
# Keep the shell out of it: append command output directly
#   git diff <range> >> "$PROMPT_FILE"
# and literal text (instructions, ticket summary) only through a quoted
# heredoc (cat >> "$PROMPT_FILE" <<'EOF' ... EOF) or your file-write tool,
# never inside a double-quoted shell string or an unquoted <<EOF, which run
# its $(...) and backticks.
SIZE=$(wc -c < "$PROMPT_FILE" | tr -d ' ')
if [ -z "$CMD" ]; then
  echo "self-review: could not read the route's cmd; reviewing in-session" >&2
  exit 1
elif [ "$SIZE" -gt 100000 ]; then
  echo "self-review: prompt too large to route ($SIZE bytes); reviewing in-session" >&2
  exit 1
else
  RUN=$(printf '%s\n' "$CMD" | sed 's/{prompt}/"$(cat "$PROMPT_FILE")"/g')
  OUT=$(PROMPT_FILE="$PROMPT_FILE" sh -c "$RUN" </dev/null); hrc=$?
  RESULT=$(printf '%s\n' "$OUT" | LC_ALL=C sed -n 's/^[[:space:]]*TKT-RESULT: //p' | tail -n 1)
  if [ "$hrc" -eq 0 ] && printf '%s\n' "$RESULT" | jq -se 'length == 1 and (.[0] |
      type == "object" and .phase == "self-review" and (.outcome == "retry" or
      (.outcome == "advance" and ([.findings[]? | .severity? | strings |
      ascii_downcase | select(. == "blocker" or . == "warning")] | length == 0))))' \
      >/dev/null 2>&1; then
    printf '%s\n' "$RESULT" | jq .
  else
    echo "self-review: routed review did not run (exit $hrc, no valid result); reviewing in-session" >&2
    exit 1
  fi
fi
)
```

Once the file is written, the prompt reaches the harness through `cat`, never
through shell parsing, so quotes, `$` and backticks in the diff arrive intact.
Writing the file is the one step where the shell could read hostile ticket or
diff text, hence the rules in the block's comments. That holds because `cmd`
carries `{prompt}` as a bare shell word; `tkt` rejects a quoted, escaped or
embedded one. The prompt travels as one argument, so one over 100000 bytes is
not routed (Linux caps a single argument at 128 KiB). A valid result feeds
step 3 unchanged; an `advance` that lists blockers or warnings is not valid. A
failed run (the block exits 1) is not a finding and not a PASS: say in the output that the routed
review did not run, then review in-session. A pinned `model` stays on every
pass; it never escalates. Only this pass is routed: the fixes in step 4 stay
with the author, and the step-5 checks stay in-session because their evidence
is command output, not judgment.

Hostile-reviewer mindset. Check every changed file for:

| Category | Look for |
| --- | --- |
| **Security** | injection, auth bypass, secrets in code, unvalidated input |
| **Types** | unjustified loose types, missing null checks, unguarded casts |
| **Errors** | unhandled rejections, missing error responses, wrong status codes |
| **Tests** | new code without tests; tests that assert nothing meaningful |
| **Style** | project lint conventions; debug logging left in |
| **Breaking** | changed shared exports / existing API contracts |
| **Performance** | N+1 queries, missing indexes, unbounded loops, large payloads |
| **Edge cases** | empty arrays, nulls, concurrency, expired tokens |

Project-specific rules (import conventions, error types, logging) belong in the
project's own conventions doc — read them and apply.

### 3. List findings

Per issue: file+line, severity (**blocker** / **warning** / **nit**), description,
suggested fix. Plus an overall verdict — PASS (zero blockers, zero warnings) skips
straight to the final check.

The reviewer produces findings only; the author context fixes them in step 4.

### 4. Fix blockers and warnings

Fix all blocker + warning items. Don't skip.

**Test gaps are not fixed here.** If the reviewer flags "new code without tests" or
"tests that assert nothing meaningful," do **not** write tests inline — a fix-pass
test written to close a finding tends to assert whatever the code already does.
Note the gap and hand it to `qa-author` (or the `qa-engineer` agent) after this
loop completes, which writes adversarial tests with red-before-green verification.

Format the flag as:

```
⚠️ TEST GAP: <file:line> — <description>
→ Recommend: invoke `qa-author` targeting <file/module> after self-review passes.
```

### 5. Rebuild and re-test (→ `sdlc-verifier`)

**Delegate to `sdlc-verifier`**, which runs the configured checks and returns a
binary PASS/FAIL with the exact commands and their output as evidence. It never
fixes anything — a FAIL comes back here.

**Fallback:** run them yourself.

```shell
eval "$(tkt cfg build.build --pkg "<pkg>")"
eval "$(tkt cfg build.test --pkg "<pkg>")"
eval "$(tkt cfg build.typecheck)"
```

### 5b. Run behavior specs — non-blocking (if configured)

If the repo binds a behavior-spec runner, run it as a **reported, non-blocking**
signal, mirroring the non-strict BDD posture: pending scenarios inform the author
but never block the flow. Skip cleanly when `build.bdd` is unset. See
[docs/behavior-specs.md](../../docs/behavior-specs.md).

```shell
# Only a missing key (tkt cfg exit 4 = NotFoundError) is a clean skip.
# Surface any other failure (e.g. exit 2 = bad/missing config) instead of
# silently swallowing it. On success stderr is empty, so 2>&1 is safe to eval.
out="$(tkt cfg build.bdd --pkg "<pkg>" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then
  eval "$out" || echo "bdd: behavior-spec runner exited non-zero (non-blocking)"
elif [ "$rc" -ne 4 ]; then
  echo "bdd: could not resolve build.bdd (tkt cfg exit $rc): $out" >&2
fi
```

### 6. Loop

Repeat until a pass yields **zero blockers and zero warnings**. Max 3 passes; if
still finding blockers, stop and flag for human review.

## Output

- Passes completed
- Issues found/fixed (count by severity)
- Remaining nits
- Confidence: high / medium / low
