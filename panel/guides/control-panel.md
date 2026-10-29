---
title: The Control Panel, page by page
slug: control-panel
audience: everyone
updated: 2026-10-28
---

# The Control Panel, page by page

The Control Panel is an optional web page for your HomeShed install. It shows what HomeShed is doing and what it has
saved you, and lets you change how it behaves. HomeShed works fully without it, and you can add or remove it at any time
without losing anything.

It runs on your own machine and opens in any browser. This guide explains each page in plain words: what it shows, what
you can do there, and what to do when something looks wrong.

## Words you'll see

- **Your AI app**: the assistant you talk to, such as Claude Code, Claude Desktop, Cursor or VS Code. It does the
  thinking.
- **HomeShed**: the tool server the panel looks after. Your AI apps use its tools.
- **Tool**: one thing HomeShed can do, like "remember a fact", "check a port" or "restart a container". A **read** only
  looks at something; a **write** changes something.
- **Call**: one use of a tool.
- **Token**: the unit AI services count and charge by, roughly four letters of text.
- **Your own model**: an AI model you run yourself (Ollama, llama.cpp and similar). It's free to use and keeps your
  words on your machine.
- **Cloud model**: an AI run by a company (NVIDIA or Groq, for example), used only if you add a key for it.
- **Memory**: facts kept between conversations, so your AI doesn't forget. It's built into HomeShed.
- **Client**: another app or project with its own key for HomeShed, and only the tools you grant it.
- **Container**: a boxed-up program running under Docker.
- **RTK** (Rust Token Killer): a separate, optional tool that trims shell output before your AI reads it, and counts
  what it cut.
- **Measured** and **estimated**: a measured figure is a real count. An estimate is worked out from sizes (about 4
  characters per token). The panel labels every figure as one or the other, or **no data**, and never adds the two
  together.

## Signing in

The panel always asks for a password, and there's no way to turn that off.

- **The first time:** the panel makes a password and prints it once, where it was started:
  - Docker: run `docker compose logs panel`;
  - without Docker: look in the terminal where you ran `homeshed-mcp panel`.
- **Choose your own:** after signing in, open Settings, then **Account and sharing**. Changing the password signs out
  every other browser and phone.
- **Keep me signed in for 30 days:** tick this on your own devices only.
- **View-only logins:** someone given a view-only login can look at the Overview, Savings, the To-do example and the
  Guide, and can't change anything. You can hide Savings from them in Settings, **Account and sharing**.

## Starting up

When the panel opens, a short start-up screen shows each part being checked: **Sign-in**, **Tool server**, **Tools**,
**Settings**, **Memory** and **AI models**. Every word on it comes from the real checks.

- It opens by itself as soon as the first figures are in.
- If it takes more than a moment, **Open the panel** appears; **Esc** does the same.
- If something is off, it says what before it opens. If start-up stalls, it says which part it's waiting for, with
  **Retry**.
- Later, if the connection drops, a small **Reconnecting to the Control Panel…** note shows until it's back.

## Finding your way

The pages are listed down the left side. Related pages share one entry, which opens the one you used last and lists
the others under it: **AI** holds Models and Reasoning, **Servers** holds Proxmox, Terminal, Containers, Uptime, Tools
and Run, **Rules** holds Rules and Observations, and **Settings** holds Settings and this Guide.

| Page | What it's for |
|---|---|
| Overview | What works right now, and what HomeShed is doing |
| Projects | Every folder your agents work in |
| To-do | Tasks for your AI agents (HomeShed Pro; otherwise an example) |
| Savings | What your setup has kept out of your AI's paid context |
| Models | Which AI answers first, and why |
| Reasoning | Try the reasoning tools by hand |
| Proxmox | Your Proxmox cluster, live (needs Proxmox) |
| Terminal | SSH in the browser |
| Containers | Your Docker containers (needs Docker) |
| Uptime | Your Uptime Kuma checks (needs Uptime Kuma) |
| Tools | Every tool, with an on/off switch |
| Run | Run any tool by hand |
| Rules | Rule packs for your AI's working rules (needs the guard hooks) |
| Observations | Corrections and broken rules your AI has logged |
| API access | Keys for other apps and projects |
| Settings | Every setting, your keys and passwords, and sign-ins |
| Guide | This guide |
| Pro | What HomeShed Pro adds |

- **Keys:** **1** to **9** open the entries down the left side, in order. **[** folds the menu to a slim strip of
  icons, and back.
- **Phone:** a bar along the bottom shows **Overview**, **To-do**, **AI** and **Settings**; **More** lists everything
  else.
- **Themes:** **Light**, **Dark** and **Neon**, at the bottom of the left side (at the top on a phone).
  Your choice is kept on this browser.
- **"Not set up yet"** on a page isn't a fault. It means that part needs something you haven't added, and the page says
  what.

### Why the panel is light on your computer

- **Calm:** the decoration (glows, pings, the live core's breathing) rests after about 15 seconds with nothing
  happening. It wakes when you move the mouse, click, type or scroll, or when a real tool call arrives.
- The menu icons move only while the pointer is over them or they have the keyboard focus.
- A hidden browser tab does nothing: no drawing and no updates until you come back.
- Figures redraw only when a real reading changes.

## Overview

The home page: is everything working, and what is it doing right now? At the top, by the page title:

- **Summary screen:** everything on one screen with no scrolling, for a spare monitor. **Full screen** fills the
  screen; **Exit** or **Esc** goes back. Add `#summary` to the panel's address to open it straight away.
- **Message all sessions:** type one message, such as "Restarting in 10 minutes", and every open Claude session reads it
  at its next step.
- **Cockpit:** on shows dials, traces and the token map; off shows the same figures as plain numbers, with nothing
  moving. The choice is kept on this browser, and the Savings, Models and Proxmox pages have the same switch.
- **Refresh** updates everything now.

### The cockpit

- **The status reactor and line:** the live core says at a glance whether things are working. Its orbiting dots are
  your AI models (bright means online), and every real tool call flies into the centre: blue for a read, amber for a
  write. Beside it, one sentence says whether everything is working, and what works right now.
- **Motion** turns the decorative movement off or on (the figures still update). If movement is off for another reason,
  such as your system asking for reduced motion, it says so.
- **Reset layout** puts every window back where it started, at its first size.

### The windows

Each window can be moved, resized and minimised, and the layout is kept on this browser:
- drag the dotted grip to move a window (or focus it and use the arrow keys);
- the size button steps through **narrow**, **medium**, **wide** and **full width**;
- the minimise button sends it to a dock at the top; press its name there to bring it back.

From the top:

- **Pick up where you left off:** your recent Claude Code chats. Search them, press **Resume** to reopen one in its own
  folder, or **×** to hide it (**Undo** brings it straight back; nothing is deleted). **Hidden chats** lists the ones
  you hid, with **Bring back**.
- **Tokens kept out of Claude:** two separate counts, never added together.
  - **Verified saved** is measured by RTK (switch between **All**, **30 d** and **7 d**). Without RTK it says
    "RTK isn't set up".
  - **Estimated saved** is HomeShed's estimate for work done locally and the front door.
  - Under them, every source on its own line, each marked measured, estimate or no data.
  - **As money**: two rows, **Measured** and **Estimated**, each priced on its own at API prices. Pick the model, or
    **Tokens only**, in Settings, **System**, *Show tokens saved as money at*. **Open Savings** has every day's figures.
- **GitHub:** stars, downloads, views, clones and the latest release of your public repos, with a 14-day trace of
  views and clones. Views and clones need a GitHub token with traffic access. **Release card** shows every figure.
- **Pulse:** activity over time, one trace at a time:
  - **Saved a day** (measured by RTK), over **7 d**, **30 d** or **90 d**;
  - **Calls an hour** and **Estimated an hour**, over **24 h**, **7 d** or **14 d**;
  - **Response time** of the recent tool calls, with failed calls in red.
  Hover a trace, or focus it and use the arrow keys, to read each point. **Table** shows the figures as a table.
- **Systems:** a gauge of all tool calls and how many worked, then calls a minute, median response, errors and refused
  calls. Below them: tools available, memory facts, AI models online, and whether HomeShed and its memory service are
  running.
- **Token pipeline:** a map of tokens on their way to Claude, and where each saving turns some away (RTK trimming, the
  front door, permissions, tool calls, memory, your own model). Select a stage to see its figures and how they're
  worked out. What still reaches Claude is counted by the AI company, not HomeShed, so it shows as no data.

### Further down

- **Claude sessions:** how full each chat is, and your alarm.
- **Projects:** release progress, and what's released and how it's doing.
- **Live activity:** reads and writes, recent calls, your AI models, and **Clients live** (who's using HomeShed).
- **Tools and memory.**

## Waiting for you

When something needs your OK, a **Waiting for you** button appears at the top of every page. **New replies** and
**Messages** buttons appear the same way when your agents have written back.

A chat app signing in shows as a **Sign-in waiting** card with a 6-digit code, how long is left, and whether the app
is *checked* or *unverified*. Approve only a sign-in you just started, with the same code on both screens. Give it an
app name and an access level (**No tools**, **Read-only (recommended)** or **Everything**), then press **Approve**, or
**Decline**.

Tick **Trust this app on the browser that asked** to skip the code next time. For 90 days, the same app signing in
again from that browser, back to the same place, goes straight through. Anyone else still waits for your OK. The
**Trusted browsers** list on the **API access** page shows each one; **Remove** makes it ask again.

## Projects

Every folder a Claude Code session has worked in.

- At the top: a ring of **Where the space goes** (the biggest folders), and totals: project folders, open now, git
  repos, changed files, commits to push and agents seen.
- Search, sort (recently active, size or name), and show them as **Cards** or a **Table**.
- Each project shows its state (working now, open, or when it was last active), size, files and git status.
- Open one for who works there, its git details and key files. Click a path to copy it.
- **Pinned for agents:** files or folders an agent working there should read. Type a path in the folder and press
  **Pin**; **Unpin** takes it off (the file itself isn't touched).

## To-do

Tasks for your AI agents, with who's working on what and what's waiting for you. It comes with HomeShed Pro. Without
Pro, you see an example list, so you can see how it works.

- At the top: a trace of tasks finished and failed each day over 14 days, and counts for **Need you**, **Running**,
  **Open**, done and failed. Press a count to show those tasks.
- **Add task:** say what an agent should do and how it can tell it's done, then pick the agent, priority and repeat.
  A task that publishes, deletes, pushes, pays, sends or deploys waits for your OK, unless you tick **Run without
  asking**.
- Search, filter by agent, and switch between **List** and **Board** (one column per agent; drag a task to hand it
  over).
- **Code checked by another session:** open a task and set **Check** to "Code: another session checks it first". The
  agent that writes the code can't mark it done. It hands over its change (a commit, which is a saved version of the
  code), and a different agent tries it and marks it **Verified**, saying what it ran, or **Rejected**, saying why, which
  sends the task back to the first agent. Most agents here are Claude, so a Claude agent checking a Claude agent's code
  also has to ask a different kind of AI (usually your free local model) to look over the change, and record its
  answer. The task row shows the commit, who checked it and what kind of AI they are, for example "verified by
  Reviewer (claude) + qwen second opinion"; open the task to read the second opinion. If you're happy without a
  check, open the task and press **Mark done without a check**; the task notes that you did.

## Savings

How much your setup saves. Every job your own model does, and every long output trimmed before your AI reads it, means
fewer paid tokens.

- **Tokens kept out of Claude:** **Trimmed by RTK** (measured) and **Local work** (estimated), side by side, never
  added together, with the sources listed under them.
- The figures: trimmed by RTK, average reduction, commands filtered, local work and front door, and missed savings.
- **Trimmed each day:** a trace over **14 d**, **30 d** or **90 d**, with a **Table** view.
- **Every day:** twelve weeks of squares, one per day: darker means more saved.
- **Missed savings:** commands in your Claude Code history that ran without RTK, about what RTK would have cut, and the
  RTK way to run each.
- Without RTK, the RTK parts say "RTK isn't set up yet".

## Models

The AI models HomeShed's tools use. New ones appear here by themselves.

- **Which AI answers first:** the order they're tried: the model on your PC first, then cloud models as a backup. Each
  shows where it runs and whether it's ready. A model that just failed is **Benched** for a while; **Try again now**
  brings it back.
- **Why this model?** shows how long each question took, and a list of recent questions: which model answered, and why
  the others were passed over. Prompts and answers are never recorded.
- With no model set up, it says so. Add yours in Settings, then **AI and tools** (the address of your own model server,
  ending in `/v1`), or add a cloud key in **Keys and passwords**.

## Reasoning

Try the reasoning tools by hand. Type something and see what the tool returns:
- **Solve** finds values that satisfy a set of rules, or proves there are none (the example is filled in for you);
- **Decompose** breaks a job into ordered steps, using your own model;
- **Route** suggests whether your own model or Claude should take a job (press **Recommend**);
- **Delegate** lets your own model answer when it can, and says when Claude is needed.

## Proxmox

Your Proxmox cluster, updated every 15 seconds while the page is open.

- At the top: nodes online, VMs and containers running, and backups over 2 days old. Then dials for CPU, RAM and
  storage across the cluster.
- **Alarms:** anything that needs a look, and what cleared in the last week. **Alarm settings** opens their settings.
- **Nodes:** CPU, RAM, disk, disk health and uptime. Select one for its graphs (hour, day, week, month or year).
- **VMs and containers:** search, filter and sort them. The menu on each has **Reboot…**, **Shut down…**,
  **Power off…** or **Start…**. Nothing happens straight away: each change waits for your OK under **Waiting for
  you**.
- **Backups and failed tasks**, from Proxmox's own task log.

## Terminal

SSH (a secure remote command line) to the machines on your network, in the browser. Switch it on in Settings,
**System**, **Terminal**.

- **Add a machine** with its name, address, user and port, then press **Set up access** once with its password.
- **This Control Panel's key** is what machines let in. Copy it if you add it yourself.
- **Claude Code on your machines** walks through installing and signing in to Claude Code on each one; the **Claude**
  button then opens the machine and starts it.
- Away from your home network, it asks for your password again every 12 hours. Closing the page keeps a session for 5
  minutes.

## Containers

Your Docker containers: how many are running or stopped, which images are in use and which have ports open.

- Search by name, image or ID, filter by state, and sort.
- Each container's menu has **Details**, **Logs**, **Restart…** and **Stop…** (or **Start**). The page asks before
  restarting or stopping anything.
- It needs HomeShed to reach Docker. Until then it says so and points to the Docker section of HomeShed's
  configuration guide.

## Uptime

Your [Uptime Kuma](https://github.com/louislam/uptime-kuma) checks: how many are up, down, waiting for a first
reading, or paused in Kuma. Press a count to show those checks, or search and filter by state and kind. Until HomeShed
can read Kuma's data, the page says how to connect it.

## Tools

Every tool HomeShed offers: how often each was used, whether its calls succeeded, how fast they were, and when each was
last used.

- At the top: switched on, switched off, calls so far, how many succeeded, and tools ever used.
- Search, filter by area, on or off and risk, and sort by name, calls, recent use, speed or failures.
- Each tool has an **on/off switch**. A switched-off tool answers "switched off" instead of running, for everyone.
- Open one for its details, usage and recent calls, or **Run it by hand**.
- Tools that change things on your machines (Docker, git writes, running code) start switched off.
- A tool with no calls isn't broken: your AI app may do that job with its own built-in tools.

## Run

Run any tool by hand. Find a tool (filter by area or risk), fill in its form and press **Run**; **Copy answer** copies
the result. **Save** keeps the form under a name in **Saved runs**, to run again later. Every run goes through the
tool server's own switches and limits, and is in its activity log.

## Rules

Rule packs: the working rules your AI follows, and the checks that make sure it does. They run on the guard hooks,
separate scripts for Claude Code. Until those are set up, the page says so and the numbers stay empty. HomeShed's tools
work without them.

With the guard hooks set up:
- **The protocol, request by request:** four dials for how often requests checked memory first, were routed, used your
  own model, and (for code changes) were tested. Beside them: how many times a guard stepped in.
- **Recent activity** lists what the checks stopped or spoke up about. **Daily self-review** shows which slips were
  stopped the day before, and how often Claude tried the same thing again.
- **Rule packs** are listed one tile per installed pack. For each check you choose **Stop** (your AI can't do it, and is
  told why), **Warn** (it can, and is told first why it may go wrong), **Note only** (it can, and it shows here) or
  **Off**.
- **Get more packs** installs more with HomeShed Pro. Installed packs keep working if Pro ends.

## Observations

Corrections and broken rules your AI has logged, each with what happened and how it was fixed.

- At the top: a trace of corrections logged each day, and counts by status. Press a count to show those.
- Search, and filter by status, rule and date.
- Each one's menu has **Mark fixed**, **Park** or **Decline** (or **Reopen**).
- When the same rule is broken twice, it's flagged so an automatic check can be added.
- **Log a correction** adds one yourself.

## API access

Give each other app, coding agent or person its own **client**. Press **Add client**:
- its own **token**, shown only once (copy it straight away);
- only the tools you grant: none to start with, all read-only tools, or everything;
- a limit on **calls per minute**, and an optional **expiry** (1, 7, 30 or 90 days, or never).

At the top: clients, used in the last 7 days, suspended or expired, calls by clients and refused calls. Search and
filter the list by status, last use and access. Open a client for **Overview**, **Permissions**, **Limits and
spending**, **Memory** and **Lifecycle**:
- **Tool view:** **Full list**, or **Front door**: five tools that find and run the rest, so the app uses fewer
  tokens. New apps start on the front door.
- **Images:** **Inline**, or **As pictures** (shown as real images in the chat).

**Connect a chat app (Claude, ChatGPT…)** shows how a chat app signs in without a token: you approve it under
**Waiting for you**. **Front door savings** shows what the front door saved each app.

You can suspend a client, give it a new token, or delete it. **Suspend all** is the emergency stop if a token may have
leaked. **Recent changes** lists who changed what; tokens are never recorded. You (your own AI app and this panel)
always have full access, so you don't appear in the list.

## Settings

Everything you can change, one section at a time. **Search settings** looks across every section. Each change saves
straight away.

- **Claude:** when you, and Claude, are warned that a chat is getting full; your plan usage; and how strictly the rules
  are enforced.
- **Agents:** the to-do list.
- **AI and tools:**
  - whether your own model may hand questions to cloud models when it's busy;
  - the address of your own model server;
  - switching whole groups of tools on or off;
  - known bugs: an optional search of public GitHub reports, off until you switch it on, which sends only a cleaned
    version of the error;
  - the APIs HomeShed knows about.
- **Keys and passwords:** every key, token and password HomeShed uses, stored encrypted by HomeShed. Your AI uses them
  through its tools and never sees the values.
  - **Connect cloud AI** adds a cloud model: pick a provider, paste a key from its site, press **Test**, then **Save**.
    What you send these services leaves your machine.
  - **Show** displays a stored value for 30 seconds, after you type your password again, and the change log records
    it. It only works if the panel's `VAULT_REVEAL_KEY` is set; without it, values can be stored and used, but never
    shown.
- **Alerts:** where phone notifications go, if you use [ntfy](https://ntfy.sh), and the Proxmox alarms.
- **Account and sharing:** your Pro key, your password, defaults for new clients, what view-only logins can see, and
  **view-only logins** (add a person, give them a new password, or remove them, which signs them out at once).
- **System:** how the panel itself behaves (its name, how often it updates, moving pictures, how long a sign-in lasts,
  the Terminal, and money for savings), **Connections** (the services it talks to, and whether each one is
  answering) and where files are kept.
- **Claude Code:** *When Claude Code compacts a chat* (its default, or 100k to 1M tokens). It changes Claude
  Code's own settings file and keeps a backup. Restart open chats to pick it up. In Docker it needs the `~/.claude`
  mount in docker-compose.yml.

## Guide

This guide and any others that came with your install, with a list of sections to jump to. **Open the welcome screen**
shows the first-run steps again.

## Pro

What HomeShed Pro adds to the free HomeShed, side by side: every curated rule pack (Token Saver, Safe Operator, Team
Sessions, Release Ready, Beginner Mode), updates and new packs as they come, and the known-bugs feed (fixes other users
found, looked up when your AI hits an error). HomeShed and this panel stay free and complete. Already a member? Paste
your Pro key in Settings, **Account and sharing**.

## Keeping secrets safe

- Keys and passwords belong in Settings, then **Keys and passwords**. HomeShed stores them encrypted, and your AI uses
  them through tools without seeing them.
- HomeShed's owner token is in the `.env` file next to HomeShed. Never share it or commit it. The panel keeps it on
  its own server, never in your browser.
- If a key ever leaks (pasted into a chat, or committed), replace it at the service that issued it. Deleting the file
  isn't enough.
- HomeShed can check your Claude Code history for keys left there (the `secrets.transcript_scan` tool). It tells you
  where to replace each one, and never shows the key.

## When something looks wrong

- **"Not set up yet".** Not a fault: that part needs something you haven't added, and the message says what.
- **"HomeShed isn't answering".** It may be restarting: wait a minute and press **Refresh**. With Docker,
  `docker compose ps` shows whether it's running.
- **"New version: reload".** The panel was updated since you opened it. Press it to load the new one.
- **Nothing is moving.** That's the calm rest, not a fault: move the mouse and it wakes. If **Motion** is off, or your
  system asks for reduced motion, the decoration stays still.
- **You're signed out.** Sign-ins last a set time, and changing the password signs everyone out. Sign in again.
- **Forgotten password.** If you set `DASHBOARD_PASSWORD` in the `.env` file, that's the password: change it there
  and restart the panel. Otherwise delete `owner.json` from the panel's data folder and restart the panel. It prints a
  new password once.
  - With Docker: `docker compose exec panel rm /data/owner.json`, then `docker compose restart panel`, then
    `docker compose logs panel`.
  - Without Docker: the file is in HomeShed's data folder, under `panel`.
- **Still stuck?** Ask your AI app: it can read this guide and check HomeShed with its tools, or run
  `homeshed-mcp doctor`.
