# /triage-ticket

Read a ticket, extract requirements, transition to `in_progress`, and start tracking. All access via `tkt`.

## Steps

1. Read the ticket: `tkt view "$KEY" --json`.
   Extract: key, type + type_class, summary, acceptance, labels, components, blocked_by.
2. Identify affected packages from ticket content.
3. Move to In Progress: `tkt transition "$KEY" in_progress`.
4. Add a work-start comment: `tkt comment "$KEY" "Starting work. Affected packages: <pkg1>, <pkg2>"`.

## Output

Provide to the next skill: key, type + type_class, summary, acceptance criteria, affected packages, blockers/unknowns.

## Rules

- Speak in roles, not literal lane names.
- Never transition a blocked ticket.
