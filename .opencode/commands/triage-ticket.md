---
description: Read a ticket, move it to in_progress, and start work via tkt
---

Triage the ticket and move it to In Progress: $ARGUMENTS

1. Read the ticket with `tkt view "$KEY" --json` and extract key, type + type_class, summary, acceptance, labels, components, and blocked_by.
2. Identify affected packages from ticket content.
3. Move the ticket to `in_progress`: `tkt transition "$KEY" in_progress`.
4. Add a work-start comment: `tkt comment "$KEY" "Starting work. Affected packages: <pkg1>, <pkg2>"`.

Output to the next skill: ticket key, type + type_class, summary, acceptance criteria, affected packages, blockers/unknowns.

Never transition a blocked ticket. Speak in roles, not literal lane names.
