---
name: triage-ticket
description: 'Read a ticket, move it to in_progress, and start work via tkt. Invoke as slash command, not automatically.'
disable-model-invocation: true
allowed-tools: [Bash, Read, Edit]
---

# Triage Ticket

Read a ticket, extract actionable requirements, transition to the `in_progress`
lane, and start time tracking. All access via `tkt`.

## Steps

1. Read the ticket:
   ```shell
   tkt view "$KEY" --json
   ```
   Extract: key, type + type_class, summary, acceptance, labels, components, blocked_by.
2. Identify affected packages from ticket content.
3. Move to In Progress:
   ```shell
   tkt transition "$KEY" in_progress
   ```
4. Add a work-start comment:
   ```shell
   tkt comment "$KEY" "Starting work. Affected packages: <pkg1>, <pkg2>"
   ```

## Output

Provide to the next skill: key, type + type_class, summary, acceptance criteria, affected packages, blockers/unknowns.

## Rules

- Speak in roles, not literal lane names.
- Never transition a blocked ticket.
