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

Optional tool groups (Docker, git writes, build and test runners) start switched off. `uvx homeshed-mcp setup`
lists them; only switch one on when the user asks for it.

## Their own server instead

For a user who already runs a HomeShed server (Docker or `homeshed-mcp serve --http`):

1. The user makes a key for Claude Code on their server: on the Control Panel's API access page, or with
   `POST /clients`. Suggest granting only the tools they want Claude to use.
2. The user puts the key in an environment variable named `HOMESHED_TOKEN` in their own shell profile, and opens a
   new terminal.
3. They run this, with their server's address in place of the example. Keep the single quotes: they keep the key out
   of Claude Code's settings file, which stores only the variable's name.

   ```
   claude mcp add -s user --transport http homeshed 'http://127.0.0.1:8765/mcp' --header 'Authorization: Bearer ${HOMESHED_TOKEN}'
   ```
4. They restart Claude Code and check `/mcp`. They can turn this plugin's local server off there, so the tools don't
   show twice.

Never ask the user to paste the key into the chat, and never write it to a file in a project or in this plugin.
If a key has been pasted into the chat, tell the user to delete that key on their server and make a new one.

## Upgrading

`uvx homeshed-mcp@latest doctor`, then reconnect in `/mcp`. Data is kept. Full steps:
https://github.com/keefng8/homeshed-mcp/blob/main/docs/upgrading.md
