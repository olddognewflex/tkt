---
mode: agent
description: 'Produce a structured bug report with minimal reproduction steps; optionally file it via tkt.'
tools: ['search/codebase', 'terminal', 'file']
---

# QA Bug Report

Distill a failing scenario — test output, error log, user report, or observed behavior — into a minimal, actionable bug report.

## Steps

1. Identify the failure: what was attempted, what went wrong (error, wrong output, crash, hang), under what conditions.
2. Reduce to minimal reproduction: the fewest steps and least setup that still trigger it. Prefer a test or a `curl` over UI steps.
3. Classify severity: **Critical** (data loss, security breach, outage) · **High** (core feature broken, no workaround) · **Medium** (degraded, workaround exists) · **Low** (cosmetic, rare edge case).
4. Classify reproducibility: **Always** (100%) · **Often** (>50%) · **Intermittent** (<50%, timing or state dependent) · **Rare**.
5. Read the code path and identify the suspected root cause — which module is at fault, and whether it's logic, data, a race, or configuration.
6. Format the report (below).
7. **Optional, only on explicit confirmation** — file it:
   ```shell
   tkt create --type Bug --summary "<one-line summary>" \
     --body "<full report body>" --priority "<mapped priority>"
   ```
   Never create the ticket automatically. Ask first.

## Output

```markdown
## Bug Report

**Summary:** <one line>   **Severity:** ...   **Component:** ...   **Reproducibility:** ...

### Steps to Reproduce
1. <setup>  2. <action>  3. Observe: <what goes wrong>

### Expected / Actual Behavior
### Environment          (branch, commit, runtime, relevant config)
### Evidence             (trimmed log, stack trace, or test output)
### Root Cause (suspected)
### Suggested Fix
### Regression?          (yes — worked in X / no / unknown — needs bisection)
```

## Rules

- Read-only by default; create a ticket only when the user confirms.
- Document the bug, don't fix it. No code changes.
- Never `tkt transition` an existing ticket. No commits or pushes.
- Every field must be specific. "Something is broken" is not a bug report.
- Mark uncertainty as "suspected" — never present a guess as fact.
