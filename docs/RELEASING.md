# Releasing

## One-time setup

- [ ] PyPI → your project → Publishing → add a **Trusted Publisher**: repository `keefng8/homeshed-mcp`, workflow
      `release.yml`, environment `pypi`. No API token is ever stored.
- [ ] The same on **test.pypi.org** (a separate account): workflow `release-test.yml`, environment `testpypi`. Before
      the first real tag, run **Release rehearsal (TestPyPI)** from the Actions tab and install the `.devN` it uploads:
      the real release then can't fail on a publisher setting.
- [ ] GitHub → Settings → Environments → create `pypi` with **required reviewer** = a maintainer.
- [ ] Settings → Code security: private vulnerability reporting, secret scanning + push protection,
      Dependabot alerts. Settings → General: Discussions on.
- [ ] Branch protection on `main`: require the CI and CodeQL checks.

## Each release

- [ ] `CHANGELOG.md`: move **Unreleased** to the new version, with today's date.
- [ ] Bump the version in `pyproject.toml`, `server.json` (both `version` fields) and `manifest.json`.
- [ ] Locally: `pytest`, `python scripts/gen_tools_table.py --check`,
      `python scripts/gen_install.py --package homeshed-mcp --check`.
- [ ] `pip-licenses --from=mixed` and compare with `THIRD_PARTY.md`; read the LICENSE of anything new.
- [ ] `python scripts/release_smoke.py`: builds the wheel, installs it into a fresh virtualenv with no settings, and
      checks `init`, `setup`, `doctor`, and over stdio the server name, the tool list and a memory round trip.
      All 10 checks must pass.
- [ ] `python scripts/check_bundle.py`: packs the Claude Desktop bundle, unpacks it and runs `manifest.json`'s own
      command (`doctor` in place of `serve`). It must end with "Nothing is broken". CI runs it on every push.
- [ ] Commit, tag `vX.Y.Z`, push the tag.
- [ ] Approve the `pypi` environment when the release workflow asks.
- [ ] The workflow then publishes to PyPI, pushes `ghcr.io/keefng8/homeshed-mcp`, builds the `.mcpb`, creates the GitHub
      Release with artifacts and build provenance, and publishes `server.json` to the MCP registry.
- [ ] Check from a clean machine: `uvx homeshed-mcp doctor`, the Cursor and VS Code buttons, the registry listing.

## First release only: get listed

- [ ] Repo topics: `mcp`, `mcp-server`, `model-context-protocol`, `claude`, `self-hosted`, `llm-tools`.
- [ ] The official MCP registry is automatic (release workflow; the README keeps its `mcp-name:` line).
- [ ] One-line PR to [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers) in the right
      category.
- [ ] Submit to Glama, PulseMCP, mcp.so and Smithery; the Docker MCP Catalog once the image is public.
- [ ] Check that the README's first screen shows the demo GIF and the one-line install: listings link straight to it.

## If a release is bad

- [ ] **Yank** it on PyPI (never delete), then publish a fixed patch version.
- [ ] If it was a security problem, publish a GitHub security advisory and add it under **Security** in the
      changelog.
