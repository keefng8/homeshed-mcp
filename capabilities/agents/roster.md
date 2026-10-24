# agents.roster, agents.roster_set

Who's who among the agents. Claude Code's ListAgents shows names only, so sessions forgot which was which (the owner,
2026-10-24: "Environment setup = library builder"). The roster gives each agent a role and how to reach it, and the
owner confirms every row.

## Use
- `agents.roster()`: every row: `name` (as ListAgents or agents.list shows it), `role`, `reach`, `how`, `confirmed`,
  `by`, `updated`, `gone` (not set or seen for 7 days), and `proposed` when an agent's change waits for the owner.
- `agents.roster_set(name, role, reach="session", as_session="")`: add yourself, or another agent, e.g. a cloud
  session or a mailbox-only app. `reach` is `session` (SendMessage on the owner's PC), `cloud` (a cloud session) or
  `mailbox` (agents.send only). Setting the same words again marks the row seen.

## The owner's wording wins
New and changed rows show in the Control Panel's approvals bar ("Agent roster": Confirm / Decline). Once a row is
confirmed, an agent's change waits beside it as `proposed` until the owner confirms it; Decline drops that change (or a
row never confirmed). Every Claude session shares the owner's token, so only the Control Panel's route
(`POST /agents/roster/{name}/{confirm|decline|remove}`, optional body `{"role"}` for the owner's own wording) counts as
the owner.

## At the start of a session
The session-start hook adds the roster's live rows (unconfirmed ones marked), and asks a session that isn't on it to add
itself.

## Limits and storage
50 rows; name up to 60 characters, role up to 200; control characters removed. `DATA_DIR/usage/agents_roster.json`
(`AGENTS_ROSTER_FILE` overrides it). Implementation: `agents_roster.py`, `tools/agents/roster.py`.

## Machine-readable definition
`roster.json`, `roster_set.json`
