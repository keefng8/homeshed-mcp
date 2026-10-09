## What this changes

<!-- One tool or one fix per PR. Link the issue if there is one: Fixes #123 -->

## How I tested it

<!-- Commands you ran, and what you saw. -->

## Checklist

- [ ] `pytest` passes
- [ ] New or changed tools have an updated `capabilities/<category>/<action>.json` and `.md`
- [ ] Ran `python scripts/gen_tools_table.py` if tools changed
- [ ] Write tools are off by default; a missing backend returns `unavailable: set X`
- [ ] No hard-coded hosts, IPs, paths, tokens or keys
