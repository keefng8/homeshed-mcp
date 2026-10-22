# HomeShed plugin for Claude Code

Adds [HomeShed](https://github.com/keefng8/homeshed-mcp), a self-hosted MCP tool server, to Claude Code: memory that
lasts between sessions, a logic solver, git, network and machine checks, a known-bugs list and release checks. It
also adds two skills: one sets HomeShed up, and one tells Claude which tool fits a job.

No account and no key are needed. HomeShed is free and open source (Apache-2.0).

## What you need

**[uv](https://docs.astral.sh/uv/)**, which runs HomeShed on your computer. Install it once:
- Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then open a new terminal.

## Install

In Claude Code:

```
/plugin marketplace add keefng8/homeshed-mcp
/plugin install homeshed@homeshed
```

Then restart Claude Code and type `/mcp`: `plugin:homeshed:homeshed` should say connected. The first start downloads
HomeShed, so if it says it timed out, choose it in `/mcp` and reconnect. If it still fails just after a new version
came out, run `uvx --refresh-package homeshed-mcp homeshed-mcp doctor` (it fetches a fresh version list and touches
only the downloaded program, never your data) and reconnect.

If you added HomeShed before with `claude mcp add` or `uvx homeshed-mcp setup`, remove that entry
(`claude mcp remove homeshed-mcp -s user`) so its tools don't show twice.

## What it downloads and what it sends

- **Downloads:** the first time Claude Code starts HomeShed, `uvx` downloads the `homeshed-mcp` package and its
  dependencies from PyPI (https://pypi.org/project/homeshed-mcp/). The plugin names one exact HomeShed version, so
  nothing changes until you update the plugin (`/plugin`, then update `homeshed`), which moves it to the new version.
- **Sends:** this plugin makes no network calls of its own. It has no hooks, and collects no usage data. HomeShed
  calls another service only when you set one up (your own model, a cloud model with your key, Docker, ntfy and so
  on) or when a tool's job is to reach the address you give it (for example a DNS lookup or a port check).
- **Stays switched off:** tools that change things on your machines (Docker, git writes, build and test runners) start
  off. `uvx homeshed-mcp setup` lists them, and you choose.

Full details: [PRIVACY.md](https://github.com/keefng8/homeshed-mcp/blob/main/PRIVACY.md).

## Already run your own HomeShed server?

This plugin runs HomeShed on your computer. To connect Claude Code to a HomeShed server you already run
(Docker, or `homeshed-mcp serve --http`) instead, follow [Connect Claude Code to your server](https://github.com/keefng8/homeshed-mcp/blob/main/docs/configuration.md#claude-code-server).

## Uninstall

`/plugin uninstall homeshed@homeshed`. Your HomeShed data (memory, settings) stays in its data folder; `uvx homeshed-mcp
doctor` shows where.

## Help

- Setup problems: `uvx homeshed-mcp doctor` gives the exact command to fix each one.
- Docs: https://github.com/keefng8/homeshed-mcp
- Issues: https://github.com/keefng8/homeshed-mcp/issues
- Security: see [SECURITY.md](https://github.com/keefng8/homeshed-mcp/blob/main/SECURITY.md)
