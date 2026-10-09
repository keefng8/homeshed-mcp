# Memory — Stats

## ID
`memory.stats`

## Purpose
How much memory has been read (`memory.recall`, `recall_facts`, `recall_relevant`) and written (`memory.capture`,
`remember_fact`) since counting began. An app sees only its own totals, split by category: give each of your agents its
own category (e.g. `agent-design-agent`; lowercase letters, digits and hyphens) and you get per-agent totals. The owner
sees every app with its share of all memory use, so both dashboards can show their totals.

## Returns
App: `{"app", "reads", "writes", "categories": {name: {"reads", "writes"}}, "since"}`.
Owner: `{"reads", "writes", "apps": {name: {"reads", "writes", "share"}}, "since"}`.

Only successful calls count. At most 200 categories are tracked per app.
