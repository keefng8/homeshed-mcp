# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Security fixes are listed under **Security**.

## [Unreleased]

### Added
- First public release: a self-hosted MCP server with 64 tools for memory, reasoning, git, files, network checks,
  Docker, monitoring, notifications, known bugs and release checks.
- Built-in memory (SQLite): in Claude Code each project gets its own automatically; an app with its own key always has
  its own memory, may read the shared one (a switch per app), and its writes to the shared one wait for the owner.
- Per-app keys with tool grants, rate limits, expiry and an audit log of changes; an app only sees the tools it's
  granted.
- `setup` finds a local model if you run one and offers to add HomeShed to Claude Code (`--yes` / `--no` for scripts);
  for other apps it prints the settings to paste.
- `doctor` checks everything and gives the exact command to fix anything that's missing.
- One-line install with `uvx`, one-click install for Cursor and VS Code, and a Claude Desktop extension (`.mcpb`).
- `init` generates the owner token and vault key for the server; `serve --http` reads them from `.env`.
- An optional web Control Panel: `homeshed-mcp panel`, or the Docker `panel` profile. HomeShed never needs it.
- Back up and restore steps for the Docker server, tested on a clean machine.
- Rule packs: checks that run before Claude Code runs a command or writes a file, through one hook that
  `homeshed-mcp packs on` adds (it shows the change and asks first). Write your own packs; `packs check` validates them.
- HomeShed Pro (part of the Mavis Pro membership): paste a key made on the Pro website into the Control Panel
  (Settings → Pro membership) or `homeshed-mcp pro connect`, for the curated rule packs and the known-bugs feed
  (`bugs.sync`).

### Security
- On your own machine (the one-line install) HomeShed talks only to the app that started it. As a server, every
  request needs a token (except `/healthz`, which only says the server is up), it listens on 127.0.0.1 by default,
  and in Docker it runs as an ordinary user.
- Tools that act on your machines (Docker, git writes, running code) start switched off.
- Stored keys are encrypted in a vault and never shown to the AI; showing a value needs a separate reveal key.
- HomeShed never asks for your Pro password: Pro connects with a key you make on the Pro website, checked with the
  site and kept in the vault. Nothing is sent to the Pro website until you connect.
