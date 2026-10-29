# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Security fixes are listed under **Security**.

## [Unreleased]

## [0.7.1] - 2026-10-29

### Added
- **Text to speech** (`tts.voices`, `tts.synthesize`, `tts.fetch`, `tts.usage`) with Google's Chirp 3 HD voices. It signs in as a Google service account you create (guide: Control Panel > Guide > Google text to speech). It stays inside Google's free 1,000,000 characters a month unless you allow paid characters in Settings > Text to speech; characters are counted before each request, on Google's US Pacific month.
- **Trusted browsers for sign-in**: when you approve a sign-in, you can tick "Trust this app on the browser that asked", so that app's later sign-ins from that browser, to the same return address, don't wait for you (90 days, up to 50; not while the app is suspended). The API page lists them, each with Remove.
- HomeShed Pro: **checked code work in the to-do list**. An item marked for checking is done only when a different session verifies its commit, and a verdict from the builder's own model family also needs a second opinion from another family. The To-do page shows each check (commit, verified or rejected, by whom).
- Approvals: one owner approval can cover a batch of wording-only shop updates (up to 500 requests, for up to 7 days); never a publish, and each request keeps its own one-use id and the daily limits.
- `image.fetch` takes `raw=true` to keep the image bytes for a script that saves the file, even for an app set to receive pictures.
- `image.compare` can compare against an Etsy listing photo; tools that reuse an image still refuse Etsy photos.

### Changed
- Control Panel: faster to open and lighter when idle. Each page's code loads the first time you open it; Settings asks for the plan once; costly style rules were replaced.
- Control Panel: the third theme is named **Neon**.
- Control Panel: script errors in the browser reach the panel's log (short fields, at most 30 a minute), to help find display problems.
- GitHub Actions update: setup-uv 10.3.2.
- The plugin runs 0.7.1.

## [0.7.0] - 2026-10-27

### Added
- **One-click sign-in for chat apps (OAuth)**: the standard MCP sign-in (OAuth 2.1 with PKCE, dynamic client registration, client metadata documents), so MCP apps that support sign-in connect with no pasted token; tested with Claude's custom connectors. Set `MCP_PUBLIC_URL` to your server's https address; add its host to `MCP_ALLOWED_HOSTS`; you approve each sign-in on the Control Panel (Waiting for you > Sign-in waiting) with a matching 6-digit code. Tokens are stored only as hashes, and a reused refresh token ends that sign-in. Off unless `MCP_PUBLIC_URL` is set.
- **The front door**: five tools in place of the full list, so an app doesn't pay for every tool definition (160+) on every turn, and a small model picks the right tool more often. `tools.find` (best tools for a job, instant, no model call), `tools.groups`, `tools.describe`, `tools.call` (runs any tool with the same access, approvals and spend checks as a direct call) and `tools.result`. Every tool an app is granted still runs when called by name.
- Each app has a **Tool view** setting (full list or front door). Apps added from now on start on the front door; existing apps keep the full list until you switch them.
- Shorter replies: `tools.call` takes `fields` to keep only the parts you need, and `to_file` to keep a big reply on the server and read it a page at a time with `tools.result` (24 hours, only the app that made it, key-shaped secrets hidden).
- Pictures as MCP image content: a per-app **Images** setting sends images as image blocks rather than base64 text, which a model reads for much less.
- Control Panel: a new Overview (pick up where you left off, tokens saved, GitHub), and a Waiting for you button for sign-ins.
- `approvals.status` and `GET /approvals/{id}`: what happened to one request that waited for the owner, and who decided.
- HomeShed Pro: per-app tool sets, ranking learned from use, approve-in-chat cards and a front-door savings report.

### Changed
- The approvals history is kept 30 days (was 7).
- The plugin runs 0.7.0.

## [0.6.4] - 2026-10-27

### Added
- Settings > Claude Code > "When Claude Code compacts a chat": choose Claude Code's own compaction point (its default, or 100k to 1M tokens). **This edits a Claude Code settings file of yours** (`settings.json`, only the `autoCompactWindow` key). The old file is kept as `settings.json.homeshed-bak`, a file that can't be read is never written, and `CLAUDE_CONFIG_DIR` is followed. Claude Code reads it when a chat starts, so restart open chats (exit, then `claude --continue`). A value above your model's window means Claude Code's default.
- In Docker, `docker-compose.yml` has a commented mount for this setting; without it, saving a size is refused with the reason and nothing changes.
- `bugs.seen` counts failures per day and how many matched a known bug with a fix.
- Online shops setting "Bots that may delete listing photos" (empty by default): a named bot may delete an Etsy photo only when it adds a replacement in the same request, never the first photo, within a daily cap.

### Changed
- Shop approvals: a live product update whose product changed since it was asked is withdrawn (ask again), and repeated stale updates of one product are parked for a day.
- The plugin runs 0.6.4.

## [0.6.3] - 2026-10-25

### Added
- `llm.chat` takes an `effort` option (low, medium, high, xhigh or max) for Anthropic models, so a long answer isn't spent on thinking. A bad value, or `effort` with another provider, is refused before any call.

### Changed
- GitHub Actions updates: setup-python 7.1.0, setup-uv 10.3.1, CodeQL action 4.39.1.
- The plugin runs 0.6.3.

## [0.6.2] - 2026-10-24

### Added
- `docs.read` reads PDF, Word, Excel and CSV files.
- `web.watch` and `web.watch.check` watch a page and report when it changes.
- `schedule.add`, `schedule.list`, `schedule.pause` and `schedule.remove` run read-only tools on a timetable.
- `email.send` and `email.status`: each email waits for the owner's approval before it goes.
- `backup.export` and `backup.restore` (Pro, owner only).
- `image.describe` describes a picture in words. `image.generate` can save paid providers' images into folders.
- `agents.roster` and `agents.roster_set`: a who's-who of the agents (name, role, how to reach them), with each row
  confirmed by the owner in the Control Panel.
- `bugs.seen` notes failures automatically, and suggests to the owner any bug that repeats across sessions. Known
  bugs can be shared with a HomeShed Library server.
- `publisher.sign` signs read-only library scripts with a key in the vault. The owner approves each one in the
  Control Panel. It's off until that key is stored.
- `printify.products.list` takes `max_price`.

### Changed
- `llm.chat` adds OpenAI and Gemini, answers up to 16,000 tokens, and can carry on when an answer is cut off
  (`continue_if_cut`).
- One-off commands that hog the CPU for 10 minutes are stopped, and the owner is told. Setting:
  `stray_command_stop`, on by default.
- Settings: the Library's address, and context-monitor levels down to 10%.
- `printify.products.list` returns shorter titles.
- The CodeQL and PyPI-publish actions are updated (SHA-pinned).
- The plugin runs 0.6.2.

### Fixed
- Approval buttons work for items whose id contains a backslash.
- A bare `TOKEN=value` is now redacted.

## [0.6.1] - 2026-10-23

### Security
- Every code-scanning finding on the repository is fixed:
  - `network.exposure_check` no longer calls a redirect "protected (Cloudflare Access)" just because that name
    appears somewhere in the address. It checks the redirect's real host, and looks for sign-in words in the path
    only.
  - Control Panel errors go through one helper, which removes addresses, paths and tracebacks and keeps the detail
    in the log.
  - Sign-in failures show a fixed sentence, never the provider's text.
  - Session keys are signed with HMAC. **You'll be signed out of the Control Panel once after upgrading.**
  - The first-run panel password is written to a private file (`/data/first-run-password.txt` in Docker) instead of
    the log, and the file is deleted when you change the password
    ([panel README](panel/README.md)).
  - Long inputs are refused before a pattern match that could slow down on them.

### Added
- HomeShed now tells connecting apps which tool groups it has and to use `registry.find` to pick one, so an app's
  tool search finds the right tool.
- `image.ip_check`: copyright verdicts `celebrity`, `watermark` and `artwork`, and `unsafe` now covers partial
  nudity. A `garment` option for hero photos (`tee`, `hoodie`, `sweatshirt`, `kids_tee`) flags a scene showing a
  different garment.
- `etsy.shop.get`: `api_requests_today`, the real Etsy requests made today by each app and tool, refusals included.

### Fixed
- `image.ip_check`: `artwork` means someone else's identifiable work, not any polished illustration, and `watermark`
  needs a visible mark, so clear original designs are no longer flagged.
- `etsy.listing.update` `hero_swap`: the photo that stays is judged by its alt text after this request's changes, and
  a photo with no alt text counts as not AI-made.

### Changed
- README and the plugin's setup skill: connecting Claude Code to your own server keeps the key in an environment
  variable (`HOMESHED_TOKEN`), never in a settings file or the chat, and the skill never repeats a key back.
- The Claude Code plugin runs HomeShed 0.6.1.

## [0.6.0] - 2026-10-22

Highlights: Claude joins the model relay, a vision check for images before they're listed, and a reminder that since
0.5.0 every AI app connected to HomeShed can message every other one (`agents.list`, `agents.send`,
`agents.inbox`): Claude Code sessions, other assistants and bots, each on its own key and grants
([how to use it](capabilities/agents/how-to.md)). On mcprush, HomeShed is rated grade A for security and listed as
working with 14 MCP clients.

### Added
- `llm.chat` can use Claude (`provider="anthropic"`, key `CLAUDE_WORKSPACE_API`, default model `claude-haiku-5-5`,
  images supported). Off until the owner switches it on and sets a daily cap. Requests use prompt caching, so a
  repeated system prompt costs less.
- A monthly spend limit for every model provider (Settings > Language models, default no monthly stop): requests stop
  once the month's estimated spend reaches it. `llm.providers.list` shows the month's spend and what's left.
- `image.ip_check`: checks an image for a brand, a known character, a real person or unsafe content before you use
  it, and for a lifestyle photo, a visible child or something covering the printed design. It runs on Claude through
  `llm.chat`, and when the check can't run it answers "not clear", never "clear".
- `image.carousel`: a `caption` option, a thin disclosure bar along the bottom (up to 70 characters), for example to
  say a scene is AI-made.
- `etsy.listing.update`: `hero_swap` lets an automatic approval replace the first photo with its copy carrying a
  disclosure bar, only when the alt texts match `ETSY_HERO_ALT` and a non-AI photo stays on the listing. Anything
  else waits for the owner.
- README: an mcprush badge.

### Changed
- `llm.chat` no longer sends a temperature to Claude models, which refuse it.
- The Claude Code plugin runs HomeShed 0.6.0. Its README, its setup skill and the upgrading guide now say
  `uvx --refresh-package homeshed-mcp ...` when uv hasn't seen a new version yet (`uv cache clean` didn't always
  clear it).
- `etsy.listing.update` docs list `swap`, `remove_images` and `images_alt`, which were missing.

## [0.5.1] - 2026-10-22

### Added
- `image.delete`: delete a kept image now instead of waiting for the 7-day sweep. An app can delete only its own;
  "not found:" and "not yours:" errors are final, so callers know not to retry.

### Security
- `image.store` re-encodes every image: EXIF and all other metadata (camera, GPS location, time) are removed, and
  images over 4096 px a side or built to blow up when decoded are refused.

### Changed
- The Claude Code plugin runs HomeShed 0.5.1, and its README and setup skill say what to do when uv hasn't seen a
  new version yet (`uv cache clean homeshed-mcp`).

## [0.5.0] - 2026-10-22

### Added
- Agent mailbox: connected AI apps can leave each other short messages (`agents.list`, `agents.send`,
  `agents.inbox`), with per-app grants, rate limits, an audit log and one on/off switch.
  [How to use it from your app](capabilities/agents/how-to.md).
- `image.store`: keep an image you already have (PNG, JPEG or WebP, up to 10 MB) for 7 days under a name the other
  image tools take, for example as a reference for `image.generate`.
- The Claude Code plugin has an icon.

### Changed
- The Claude Code plugin runs one exact HomeShed version (0.5.0 here); updating the plugin moves it to the next one.
- Connecting Claude Code to your own HomeShed server is now in
  [the configuration guide](docs/configuration.md#claude-code-server), instead of the plugin's README.

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
