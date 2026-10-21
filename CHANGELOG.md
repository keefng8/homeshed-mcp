# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Security fixes are listed under **Security**.

## [Unreleased]

## [0.4.0] - 2026-10-21

### Added
- A Claude Code plugin (`plugin/`): `/plugin marketplace add keefng8/homeshed-mcp`, then
  `/plugin install homeshed@homeshed`. It runs HomeShed through `uvx` and adds a setup skill and a which-tool
  skill. No hooks. Its README shows how to use your own HomeShed server instead.
- `PRIVACY.md`: what stays on your machine, what's downloaded, and when HomeShed talks to anything else.
- `instagram.publish` and `instagram.status`, with a Connect Instagram card on the Control Panel: post an image or a
  carousel of up to 10 (at most 5 hashtags, Instagram's limit). Off until the owner switches it on, only for apps
  granted it, with a daily cap (`INSTAGRAM_DAILY_MAX`, default 25). Each post is read back afterwards, so one
  Instagram didn't keep is reported as not posted.
- `image.fetch`: a finished image job's full-size original (1 MB by default, up to 25 MB on request).
- `image.carousel` and `image.promo_card`: a "hud" style, and a square `1:1` carousel shape (2000x2000).
- `etsy.listing.update` can remove photos and set each photo's alt text; `etsy.listing.get` lists every photo.
- `etsy.listings.list`: `photos=true` adds each listing's first photo, and `detail=true` adds tags, category, shipping
  profile, the full title and every photo, so a whole shop takes about two calls.
- `etsy.shop.get` reports Etsy's own daily request allowance and what's left today (`api_quota`).

### Changed
- `web.read` returns a page's main text by default (menus, cookie banners and images left out), and an error from the
  reading service is reported as an error, not as page text.
- `etsy.listing.update`: after any photo change the photos are numbered again in order, a re-order keeps each photo's
  alt text, and an automatic approval only ever removes a photo as part of a one-for-one artwork swap.
- `ETSY_EDIT_DAILY_MAX` can go up to 500.
- The approval queue retries a shop's "too many requests" answer by itself on its next round, instead of leaving the
  request failed.
- `image.print_file` keeps a pet portrait's chest when it's the same colour as the background.
- `doctor` shows each tool group's full name.
- The PyPI description no longer carries a directory ownership code.
- Docker image: Python 3.12 base image updated.

## [0.3.2] - 2026-10-15

### Added
- `web.search`: search the web through your own SearXNG (free, no API key) and get titles, links and short
  snippets; read a result with `web.read`. Set `SEARXNG_URL` ([configuration](docs/configuration.md#other-connections)).
- `system.savings`: tokens saved today and on each of the last 7 days, with what they'd have cost at a model's API
  price (approximate).

### Changed
- README: HomeShed Pro's first tools, the to-do list and trend signals, are available to members with
  `homeshed-mcp pro update` (HomeShed 0.3.1 or newer).
- Tests: contract tests check each live service's answer shape against what the code reads.

## [0.3.1] - 2026-10-15

### Security
- HomeShed Pro: a Pro tools package is checked against its signature and its file list before it's unpacked, and
  again every time HomeShed starts, so a copied or edited package folder never loads. The Pro website's answer counts
  only with a signed membership proof made for this install, and the 7-day offline grace runs from the signed time.
  Pro tools packages need HomeShed 0.3.1 or newer.

### Added
- `registry.version`: a package's latest version and when it went up, from PyPI, npm or GitHub releases, in a few
  hundred characters.
- `etsy.ledger`: what Etsy actually charged and paid the shop (fees, sales, deposits), with a total per type.
  Read-only.
- `etsy.shop.get` also returns the shop's sections and today's listing edits against the daily limit.
- `docs/upgrading.md`: how to move to a new version for each install method, with steps an AI agent can follow.

### Changed
- A call from the owner's own session with an input name the tool doesn't take is refused, and the answer names the
  inputs it does take (before, the name was dropped and the tool ran with its default). Apps' calls are only logged.
- `etsy.listing.update` can remove an attribute (give it as `null`); it waits for the owner's approval like any edit.
- `printify.products.list` rows include the Etsy listing id once a product is published.
- `ETSY_EDIT_DAILY_MAX` can go up to 200 (default 50).
- The owner can name bots whose approval counts for Etsy listing details only (`approvals_trusted_bots`, empty by
  default); never for a publish, and logged as the bot's.

## [0.3.0] - 2026-10-14

### Added
- Never lose your place: a new built-in check, `compact-keeper`. Just before Claude Code compacts a long session it
  notes the last asks, the files changed, the open to-do items and the last reply, and straight after it gives that
  note back once. It never delays a compaction, and the note stays on your machine. HomeShed Pro's Token Saver pack
  (1.3.0) switches it on, or name it in your own pack.
- `homeshed-mcp packs compact-at <1-99|off>`: when Claude Code compacts, as a percentage of its usual point. It shows
  the one settings line it changes and asks first.

- README: a "Try it: things to ask" section with example uses, and the v0.2.0 highlights (Proxmox and Terminal tabs,
  per-app spend caps).

### Changed
- The rule-pack hook also runs just before a compaction (engine version 4). After upgrading, run
  `uvx homeshed-mcp packs on` again; until then `doctor` says the hook needs refreshing.

## [0.2.0] - 2026-10-13

### Breaking
- `threads.publish` now requires `ai_generated` (true or false). Threads asks for AI-made posts to be labelled and its
  API has no field for it, so an AI-made post ends with a short "Made with AI." line. Calls without it are refused.

### Added
- 13 new tools (132 in all):
  - `spend.today` and `spend.cap_set`: what each app spent today on paid images and model calls, against a daily dollar
    cap the owner sets per app; an app at its cap gets `SPEND_CAP_REACHED`, and free and local tools keep working.
  - `repo.stats`: stars, forks, issues, release and PyPI downloads, and (with a GitHub token) views, clones and
    referrers, with daily traffic kept past GitHub's 14 days.
  - `proxmox.health`: the whole cluster at a glance, with the alarms active now.
  - `image.preview`, `image.compare`, `image.embed` and `image.cancel` (local image service).
  - `pinterest.status`, `pinterest.boards.list`, `pinterest.board.create`, `pinterest.pin.create`: every board and pin
    waits for the owner's approval.
  - `etsy.shop.update`: shop title, announcement and policies, applied only after the owner approves the full new text.
- Control Panel:
  - Terminal tab: SSH in the browser to your own machines (owner only, off by default, host keys checked, its own key).
  - Proxmox tab: nodes, storage, VMs and containers with graphs, and alarms sent as notifications (ntfy) when
    something fires and when it clears.
  - Release progress card with GitHub stats, and a Released card for the live version.
  - Per-app daily spend caps on the API access page, and a Connect Pinterest card.
  - A new sign-in page.
- A loader for optional HomeShed Pro tools (`homeshed-mcp pro update`): the download is checked against its size and
  SHA-256 before it's unpacked, and a broken Pro package never stops the free tools from starting.

### Changed
- `printify.product.update` and `update_live`: the margin floor is 20% (15% for hoodies), down from 30%.

## [0.1.2] - 2026-10-11

### Changed
- The PyPI package description carries an ownership token for the mcprush.com directory listing. No code changes.

## [0.1.1] - 2026-10-10

### Fixed
- Control Panel: the Summary screen's cards no longer cut off, rows line up, and the Release progress card shows its
  title, bars and next step without clipping.
- Control Panel: Settings is tidier (shops and social have their own section) and its switches work properly on phones.
- Control Panel: the menu's Projects icon ships with the package (it was missing from a clean install).
- Control Panel: the Observations form's example rule ID is a generic one.

## [0.1.0] - 2026-10-10

### Added
- First public release: a self-hosted MCP server with 119 tools for memory, reasoning, git, files, network checks,
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
