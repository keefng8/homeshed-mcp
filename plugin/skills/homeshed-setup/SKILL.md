---
name: homeshed-setup
description: Set up, check or fix the HomeShed tool server for this plugin. Use when the user asks to set up HomeShed, when HomeShed's tools are missing or failing to connect, or when they want Claude Code to use their own HomeShed server instead of the local one.
---

# Set up HomeShed

This plugin runs HomeShed on this computer with `uvx homeshed-mcp` (the `plugin:homeshed:homeshed` server in `/mcp`).

## Local

1. Check `uv` is installed: run `uvx --version`. If it isn't found, give the user the install line for their system
   and ask them to open a new terminal afterwards:
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Run `uvx homeshed-mcp doctor`. The first run downloads the `homeshed-mcp` package from PyPI, so tell the user
   before you run it. `doctor` lists anything missing with the exact command that fixes it. Show the user what it
   says before running a fix command.
3. Ask the user to type `/mcp` and reconnect `plugin:homeshed:homeshed`. The first start can take longer than
   Claude Code waits, so a failure straight after installing usually goes away on a reconnect.

If it still fails and `uvx homeshed-mcp==<version> doctor` says there is no such version, uv is using an old list of
versions it saved earlier. `uvx --refresh-package homeshed-mcp homeshed-mcp doctor` fetches a fresh list (it
touches only the downloaded program, never the user's data); then reconnect.

Optional tool groups (Docker, git writes, build and test runners) start switched off. `uvx homeshed-mcp setup`
lists them; only switch one on when the user asks for it.

## Their own server instead

Only when the user says they already run a HomeShed server (Docker or `homeshed-mcp serve --http`); don't bring it
up during a local setup. Give them these steps to do in their own terminal, so the key never passes through the chat:

1. Make a key just for Claude Code on their server (the Control Panel's API access page), granting only the tools
   they want it to use.
2. Put the key in an environment variable named `HOMESHED_TOKEN` in their shell profile, then open a new terminal.
3. Run, with their server's address in place of the example:

   ```
   claude mcp add -s user --transport http homeshed 'http://127.0.0.1:8765/mcp' --header 'Authorization: Bearer ${HOMESHED_TOKEN}'
   ```

   The single quotes matter (in PowerShell, bash and zsh): Claude Code saves only the variable's name and reads the
   key when it connects, so the key never goes into its settings file.
4. Restart Claude Code. In `/mcp`, turn off `plugin:homeshed:homeshed` so the tools don't show twice.

Full guide: https://github.com/keefng8/homeshed-mcp/blob/main/docs/configuration.md#claude-code-server

## Keys

Never ask the user to paste a key into the chat, never write one to a file in a project or in this plugin, and never
repeat a key's value back, even one the user has pasted. If a key has been pasted into the chat, tell the user to
delete that key on their server, make a new one, and keep it in `HOMESHED_TOKEN` as above.

## Upgrading

The plugin runs one exact HomeShed version. To move to a newer one, the user updates the plugin (`/plugin`, then
update `homeshed`) and restarts Claude Code. Data is kept. Other install methods:
https://github.com/keefng8/homeshed-mcp/blob/main/docs/upgrading.md
