---
title: The Control Panel, page by page
slug: control-panel
audience: everyone
updated: 2026-10-01
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
- **Your own model**: an AI model you run yourself (Ollama, LM Studio or llama.cpp). It's free to use and keeps your
  words on your machine.
- **Cloud model**: an AI run by a company (NVIDIA or Groq, for example), used only if you add a key for it.
- **Memory**: facts kept between conversations, so your AI doesn't forget. It's built into HomeShed.
- **Client**: another app or project with its own key for HomeShed, and only the tools you grant it.
- **Container**: a boxed-up program running under Docker.

## Signing in

The panel always asks for a password, and there's no way to turn that off.

- **The first time:** the panel makes a password and prints it once, where it was started:
  - Docker: run `docker compose logs panel`;
  - without Docker: look in the terminal where you ran `homeshed-mcp panel`.
- **Choose your own:** after signing in, open Settings, then **Account and sharing**. Changing the password signs out
  every other browser and phone.
- **Keep me signed in for 30 days:** tick this on your own devices only.
- **View-only logins:** someone given a view-only login can look at the Overview, Savings, the To-do example and the
  Guide, and can't change anything.

## Finding your way

The pages are listed down the left side. Related pages share one entry, which opens the one you used last and lists
the others under it: **AI** holds Models and Reasoning, **Servers** holds Containers, Uptime and Tools, **Rules** holds
Rules and Observations, and **Settings** holds Settings and this Guide. On a phone, the bar at the bottom shows Overview,
To-do, AI and Settings, and **More** lists everything else.

| Page | What it's for |
|---|---|
| Overview | What works right now, and what HomeShed is doing |
| To-do | Tasks for your AI agents (HomeShed Pro; otherwise an example) |
| Savings | What your setup has kept out of your AI's paid context |
| Models | Which AI answers first, and why |
| Reasoning | Try the reasoning tools by hand |
| Containers | Your Docker containers (needs Docker) |
| Uptime | Your Uptime Kuma checks (needs Uptime Kuma) |
| Tools | Every tool, with an on/off switch |
| Rules | Rule packs for your AI's working rules (needs the guard hooks) |
| Observations | Corrections and broken rules your AI has logged |
| API access | Keys for other apps and projects |
| Settings | Every setting, your keys and passwords, and sign-ins |
| Guide | This guide |
| Pro | What HomeShed Pro adds |

The number keys **1** to **9** open the entries down the left side, in order. The colour themes are at the bottom of the
left side.

**"Not set up yet"** on a page isn't a fault. It means that part needs something you haven't added, and the page says
what.

## Overview

The home page: is everything working, and what is it doing right now?

- **The status line** says in one sentence whether everything is working, for example "Everything is running, but no AI
  model is set up yet." The chips under it show each part: green works, grey isn't set up.
- **The numbers** show how many tools are available, how many memory facts are saved, how many AI models are online,
  and whether HomeShed and its memory are running. Memory says **Built in** when HomeShed's own memory is in use.
- **Tokens kept out of your AI's context** estimate what your setup saved: work done by your own model, and, if you use
  RTK, command output trimmed before your AI read it.
- **Reads and Writes** count every tool call; **Recent calls** lists the latest ones.
- **Clients live** shows who is using HomeShed right now: you, and each app with its own key.
- **Release progress** shows how far a project's release has got, when an AI agent reports it with
  `repo.progress_update`.

## To-do

Tasks for your AI agents, with who's working on what and what's waiting for you. It comes with HomeShed Pro. Without
Pro, you see an example list, so you can see how it works.

## Savings

How much your setup saves. Every job your own model does, and every long output trimmed before your AI reads it, means
fewer paid tokens (the units AI services charge by).

- **Daily savings** shows each day: darker squares mean more saved.
- **Where the savings come from** splits it into writing and reading done locally.
- The RTK parts fill in once you use RTK (Rust Token Killer), a separate tool that trims shell output before your AI
  reads it. Without it, they say "RTK isn't set up yet".

## Models

The AI models HomeShed's tools use, in the order they're tried: your own model first, then any cloud models you've
added a key for. New ones appear here by themselves.

- **Why this model?** lists recent questions: which model answered, and why the others were passed over. Prompts and
  answers are never recorded.
- With no model set up, it says so. Add yours in Settings, then **AI and tools** (the address of your own model server,
  ending in `/v1`), or add a cloud key in **Keys and passwords**.

## Reasoning

Try the reasoning tools by hand. Type something and see what the tool returns:
- **Solve** finds values that satisfy a set of rules, or proves there are none (the example is filled in for you);
- **Decompose** breaks a job into ordered steps, using your own model;
- **Route** suggests whether your own model or a bigger one should take a job;
- **Delegate** lets your own model answer when it can, and says when a bigger one is needed.

## Containers

Your Docker containers: which are running, with **restart** and **stop** for each. The page asks before restarting or
stopping anything. It needs HomeShed to reach Docker; until then it says the Docker socket isn't mounted and points to
the setup steps (docs/configuration.md, "Docker").

## Uptime

Your [Uptime Kuma](https://github.com/louislam/uptime-kuma) checks: every service it watches, with anything that's
down listed first. Until HomeShed can read Kuma's database (`KUMA_DB_PATH`), the page says it isn't connected.

## Tools

Every tool HomeShed offers: how often each was used, whether its calls succeeded, how fast they were, and when each was
last used. Filter by area, or by read and write.

- Each tool has an **on/off switch**. A switched-off tool answers "switched off" instead of running, for everyone.
- Tools that change things on your machines (Docker, git writes, running code) start switched off.
- A tool with no calls isn't broken: your AI app may do that job with its own built-in tools.

## Rules

Rule packs: the working rules your AI follows, and the checks that make sure it does. They run on the guard hooks,
separate scripts for Claude Code. Until those are set up, the page says so and the numbers stay empty. HomeShed's tools
work without them.

With the guard hooks set up:
- **The numbers at the top** show how often requests checked memory first, were routed, used your own model and were
  tested, and how many times a check stepped in.
- **Rule packs** are listed one tile per installed pack. For each check you choose **Stop** (your AI can't do it, and is
  told why), **Warn** (it can, and is told first why it may go wrong), **Note only** (it can, and it shows here) or
  **Off**.
- **Get more packs** installs more with HomeShed Pro. Installed packs keep working if Pro ends.

## Observations

Corrections and broken rules your AI has logged, each with what happened and how it was fixed. Mark each one fixed,
parked or declined. When the same rule is broken twice, it's flagged so an automatic check can be added. **Log a
correction** adds one yourself.

## API access

Give each other app, coding agent or person its own **client**:
- its own **token**, shown only once (copy it straight away);
- only the tools you grant: none to start with, all read-only tools, or everything;
- a limit on **calls per minute**, and an optional **expiry** (1, 7, 30 or 90 days, or never).

You can suspend a client, give it a new token, or delete it. **Suspend every client** is the emergency stop.
**Recent changes** lists who changed what; tokens are never recorded. You (your own AI app and this panel) always have
full access, so you don't appear in the list.

## Settings

Everything you can change, one section at a time. Each change saves straight away.

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
  - **Show** displays a stored value for a moment, after you type your password again. It only works if the panel's
    `VAULT_REVEAL_KEY` is set; without it, values can be stored and used, but never shown.
- **Alerts:** where phone notifications go, if you use [ntfy](https://ntfy.sh).
- **Account and sharing:** your password, and **view-only logins** (add a person, give them a new password, or remove
  them, which signs them out at once).
- **System:** how the panel itself behaves (its name, how often it updates, animations, how long a sign-in lasts), and
  **Connections**: the services it talks to, and whether each one is answering.

## Pro

What HomeShed Pro adds to the free HomeShed: every curated rule pack, updates as they come, and the known-bugs feed
(fixes other users found, looked up when your AI hits an error). HomeShed and this panel stay free and complete.

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
- **You're signed out.** Sign-ins last a set time, and changing the password signs everyone out. Sign in again.
- **Forgotten password.** If you set `DASHBOARD_PASSWORD` in the `.env` file, that's the password: change it there
  and restart the panel. Otherwise delete `owner.json` from the panel's data folder and restart the panel. It prints a
  new password once.
  - With Docker: `docker compose exec panel rm /data/owner.json`, then `docker compose restart panel`, then
    `docker compose logs panel`.
  - Without Docker: the file is in HomeShed's data folder, under `panel`.
- **Still stuck?** Ask your AI app: it can read this guide and check HomeShed with its tools, or run
  `homeshed-mcp doctor`.
