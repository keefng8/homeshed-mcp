# Contributing to HomeShed

Thanks for helping. Every fix, tool, doc improvement and bug report makes this better for everyone.

## Ways to help

- Report a bug or suggest a tool: [open an issue](https://github.com/keefng8/homeshed-mcp/issues/new/choose).
- Improve the docs: a clearer sentence or a missing example helps the next person.
- Add a tool or fix a bug: see below.

## Development setup

You need Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[test,panel]"   # -e: your edits take effect without reinstalling; panel: the Control Panel's tests
pytest
```

Most tests are mocked, so you don't need a GPU, a Docker daemon or a memory store. The git, DNS, files and
port tests run for real against temporary fixtures.

## Adding a tool

Tool IDs follow `<category>.<resource>.<action>`, for example `docker.container.inspect`.

1. Create `tools/<category>/<resource>_<action>.py` with a function decorated
   `@tool(name="<resource>.<action>", category=..., doc=...)`.
2. Add the manifest `capabilities/<category>/<action>.json`. Required fields: `id`, `name`, `version`,
   `description`, `category`, `risk` (`read` or `write`) and `transport` (`"mcp"`). Also fill in `aliases`
   (phrases a person would actually type), `related` (other tool IDs) and `requires` (the backend or setting it
   needs). The server refuses to start if a manifest is invalid.
3. Add the human page `capabilities/<category>/<action>.md`: what it does, inputs, an example, and its risks.
4. Add tests in `tests/`.
5. Regenerate the README tool list with `python scripts/gen_tools_table.py`. CI fails if it's stale.

## Rules every tool follows

- **Tools that act on a machine start switched off** (Docker, git writes, running code), and the owner can turn
  each one on or off. Tools that only keep notes (memory, observations) may start on.
- A tool whose backend isn't configured returns a clear `unavailable: set X` instead of crashing.
- Never hard-code hosts, IPs or paths: use settings.
- Never log request text or secrets.
- No telemetry.
- Tests don't touch the network, apart from the existing real DNS and port tests.

## Pull requests

- Keep them small and focused: one tool or one fix per PR.
- Say how you tested it.
- Update the tool's `.md` page if its behaviour changes.
- Commit messages: a short subject in the imperative ("Add git.tag", not "Added git tag").

## Reporting security issues

**Please don't open a public issue for a security problem.** Report it privately as described in
[SECURITY.md](SECURITY.md).

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
