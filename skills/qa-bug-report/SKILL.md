---
name: qa-bug-report
description: 'Produce a structured bug report with minimal reproduction steps from a failing scenario. Optionally creates a ticket via tkt create.'
model_tier: cheap
---

# QA Bug Report — Structured Reproduction

Produce a structured, actionable bug report from a failing scenario. Distills
test output, error logs, or user reports into minimal reproduction steps with
clear expected/actual behavior.

## Input

- A failing scenario: test output, error log, user report, or observed behavior
- Optionally: the code path involved

## Steps

### 1. Identify the failure

From the input, extract:
- What operation was attempted
- What went wrong (error message, wrong output, crash, hang)
- Under what conditions (specific inputs, timing, environment)

### 2. Determine minimal reproduction steps

Strip away everything that isn't required to trigger the failure:
- What's the minimum setup?
- What's the fewest steps to reproduce?
- Can it be triggered from a test? From a curl command? From the UI?

### 3. Classify severity

| Severity | Criteria |
| --- | --- |
| **Critical** | Data loss, security breach, complete service outage |
| **High** | Core feature broken, no workaround, affects many users |
| **Medium** | Feature degraded, workaround exists, affects some users |
| **Low** | Cosmetic, rare edge case, minimal user impact |

### 4. Classify reproducibility

| Reproducibility | Meaning |
| --- | --- |
| **Always** | Reproduces 100% of the time with given steps |
| **Often** | Reproduces >50% of attempts |
| **Intermittent** | Reproduces <50% — timing/state dependent |
| **Rare** | Hard to reproduce — specific conditions required |

### 5. Identify suspected root cause

From reading the code path and error:
- What function/module is likely at fault?
- Is this a logic error, data issue, race condition, or configuration problem?
- What would a fix likely involve?

### 6. Format the report

## Output

```markdown
## Bug Report

**Summary:** <one-line description of the bug>
**Severity:** Critical / High / Medium / Low
**Component:** <package/module/service>
**Reproducibility:** Always / Often / Intermittent / Rare

### Steps to Reproduce
1. <precondition or setup>
2. <action>
3. <action>
4. Observe: <what goes wrong>

### Expected Behavior
<what should happen>

### Actual Behavior
<what actually happens, including error messages>

### Environment
- Branch: <branch name>
- Commit: <short sha>
- OS/Runtime: <if relevant>
- Relevant config: <if relevant>

### Evidence
```
<error log, stack trace, or test output — trimmed to relevant portion>
```

### Root Cause (suspected)
<brief analysis of what's likely wrong>

### Suggested Fix
<what the fix would involve, if known>

### Regression?
- [ ] Yes — worked in <commit/version>, broken since <commit/version>
- [ ] No — appears to be a pre-existing issue
- [ ] Unknown — needs bisection
```

### 7. Optional: Create ticket

If the user confirms, create a ticket:

```shell
tkt create \
  --type Bug \
  --summary "<one-line summary>" \
  --description "<full report body>" \
  --priority "<severity-mapped-priority>"
```

Do NOT create the ticket automatically — always ask first.

## Guardrails

- **Read-only by default.** Only create tickets when explicitly confirmed by user.
- **No code modifications.** Document the bug, don't fix it.
- **No ticket transitions.** Never `tkt transition` on existing tickets.
- **No git mutations.** Never commit or push.
- **Minimal, precise reports.** Every field must be specific and actionable.
  "Something is broken" is not a bug report.
- **No speculation without marking it.** If the root cause is uncertain, say
  "suspected" — don't present guesses as facts.
