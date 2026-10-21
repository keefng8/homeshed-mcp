# Privacy

HomeShed (the `homeshed-mcp` package, its Claude Desktop extension and its Claude Code plugin) runs on your own
machine or on a server you run. It has no accounts, no analytics and no telemetry: nothing about you or your use of it
is sent to its author.

## What stays on your machine

Memory, settings, app keys, logs and usage counts are kept in HomeShed's data folder on the machine it runs on
(`uvx homeshed-mcp doctor` shows where). Saved credentials are encrypted in a vault there and are never shown to the AI.

## What it downloads

- `uvx homeshed-mcp` (and the Claude Code plugin, which runs that command) downloads the `homeshed-mcp` package and
  its dependencies from PyPI the first time, and again only when you upgrade.
- The Claude Desktop extension and the Docker image come from this repo's GitHub releases and its GitHub container
  registry.

## When it talks to anything else

Only when you set something up, or ask a tool to:

- **Services you connect**: your own model, cloud models with your own keys, Docker, ntfy, Uptime Kuma, Proxmox, a
  search engine (SearXNG), shops and social accounts. Each is off until you add its address or key, and each gets only
  what that tool needs to do the job you asked for.
- **Tools whose job is to reach somewhere**: for example a DNS lookup, a port check, reading a web page, or looking
  up a package's latest version on PyPI, npm or GitHub. They contact the address or service the tool is asked about.
- **Public GitHub issue search** in `bugs.find`: off by default. When you switch it on (`bugs_search_public`), only
  the error's general words are sent, with paths, numbers, quoted values and secrets removed.
- **HomeShed Pro** (optional, paid): nothing is sent to the Pro website until you connect a Pro key. After that,
  HomeShed checks the membership and downloads the Pro tools, rule packs and known-bugs feed from it. HomeShed never asks for your
  Pro password.

## The Claude Code plugin

The plugin adds HomeShed and two skills to Claude Code. It has no hooks and makes no network calls of its own.

## Questions

Open an issue: https://github.com/keefng8/homeshed-mcp/issues. For anything private, use
[Security → Report a vulnerability](https://github.com/keefng8/homeshed-mcp/security/advisories/new).
