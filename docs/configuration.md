# Configuration

HomeShed works with no settings at all. Everything on this page is optional: add a setting only when you want the
feature it turns on.

Settings are environment variables. Where you put them depends on how you run HomeShed:

- **One-line install (`uvx`)**: in your AI app's MCP config, in the server's `"env"` block. HomeShed doesn't read a
  `.env` file on its own. `uvx homeshed-mcp setup` prints a ready-made config with the ones it can detect.
  ```json
  "homeshed-mcp": {
    "command": "uvx",
    "args": ["homeshed-mcp"],
    "env": { "ENABLE_TOOLS": "git.commit,git.push" }
  }
  ```
- **Docker**: in the `.env` file next to `docker-compose.yml`, which `homeshed-mcp init` writes. Add settings to that
  file; [`.env.example`](../.env.example) lists every one you can add (don't copy it over a `.env` that `init` already
  wrote). Docker Compose reads it when the container starts.
- **A server without Docker** (`homeshed-mcp serve --http`): in the `.env` file in the folder you start it from, the
  one `homeshed-mcp init` writes (`--env-file` points it elsewhere). A setting already in the environment wins.

Run `uvx homeshed-mcp doctor` at any time: it shows which features are ready and what each one still needs.

**Contents:** [HTTP and Docker](#http-and-docker) · [Where state lives](#where-state-lives) ·
[Safety switches](#safety-switches) · [Your own model](#your-own-model) · [Cloud models](#cloud-models) ·
[Memory](#memory) · [Notifications](#notifications) · [Docker](#docker) · [Other connections](#other-connections) ·
[Tool groups](#tool-groups) · [Paid language models](#paid-language-models-llmchat) · [Image generation](#image-generation) ·
[Online shops](#online-shops-etsy-printify) · [Threads](#threads) · [Proxmox](#proxmox) · [HomeShed Pro](#homeshed-pro) ·
[Keep secrets safe](#keep-secrets-safe)

## HTTP and Docker

<a id="http"></a>Only needed when HomeShed runs as a server over HTTP (Docker, or one machine serving others). The one-line install
talks to your AI app directly and needs none of these.

| Variable | Default | What it does |
|---|---|---|
| `MCP_AUTH_TOKEN` | none | The password every client sends. Over HTTP the server won't start without it. `homeshed-mcp init` creates one. |
| `MCP_ALLOWED_HOSTS` | none | The addresses clients may use to reach the server, comma separated. Over HTTP the server won't start without it. `homeshed-mcp init` writes `localhost:8765,127.0.0.1:8765`. |
| `MCP_ALLOWED_ORIGINS` | empty | Only for browser-based clients. Leave empty for Claude Code, Claude Desktop and other apps. |
| `MCP_PORT` | `8765` | The port the server listens on. |
| `MCP_BIND` | `127.0.0.1` | The address it listens on over HTTP. The default answers this machine only. The Docker image sets `0.0.0.0` inside the container, and `docker-compose.yml` still publishes the port on `127.0.0.1` only. Leave it out of `.env` (don't even leave `MCP_BIND=` empty): the container would then listen nowhere reachable. |
| `VAULT_KEY` | none | Encrypts credentials the server stores. `homeshed-mcp init` creates one. Keep it: without it, stored credentials can't be read. |

Create the token and key in one step. Run it in the folder with `docker-compose.yml`; it adds them to `.env` and never
replaces a value that's already there:

```bash
uvx homeshed-mcp init
```

If other machines connect, two things are needed. First, the port must be reachable: `docker-compose.yml` publishes it
on `127.0.0.1` only, so either reach it through an SSH tunnel from the other machine
(`ssh -L 8765:127.0.0.1:8765 you@server`, then use `http://127.0.0.1:8765/mcp` there), or change the compose file's
`ports` line to the server's LAN address (for example `192.0.2.20:8765:8765`, with your server's own address; never `0.0.0.0` without an access
proxy, see [Security](../README.md#security)). Second, add the name or address they use, with the port:

```env
MCP_ALLOWED_HOSTS=localhost:8765,127.0.0.1:8765,myserver.lan:8765
```

## Where state lives

| Variable | Default | What it does |
|---|---|---|
| `DATA_DIR` | see below | The folder for everything HomeShed saves: memory, observations, known bugs, settings, usage counts. |

The default is a folder of its own:
- Windows: `%LOCALAPPDATA%\homeshed-mcp`
- macOS: `~/Library/Application Support/homeshed-mcp`
- Linux: `~/.local/share/homeshed-mcp` (or `$XDG_DATA_HOME/homeshed-mcp` if you set that)
- Docker: `/data` inside the image, where `docker-compose.yml` keeps it in three named volumes (`usage`, `vault` and
  `observations`).

## Back up and restore

Keep a copy of two things: the data folder above, and, for the Docker server, the `.env` file next to
`docker-compose.yml`. `.env` holds your owner token and `VAULT_KEY`; without that key, saved credentials can't be read,
even from a backup.

**One-line install.** Close your AI apps first, so nothing is being written, then copy the data folder somewhere safe.
To restore, close them again and copy the folder back.

**Docker server.** Run these in the folder with `docker-compose.yml`. The volume names start with that folder's name
(`homeshed-mcp_` for a normal clone; `docker volume ls` shows yours). They were tested on Linux, and they stop the
server for a few seconds.

Back up:
```bash
docker compose stop
for v in usage vault observations; do
  docker run --rm -v "homeshed-mcp_$v:/from" -v "$PWD:/backup" alpine:3 tar czf "/backup/homeshed-$v.tgz" -C /from .
done
cp .env homeshed.env.backup
docker compose start
```

Restore (with those four files in the same folder):
```bash
docker compose down
cp homeshed.env.backup .env
docker compose create
for v in usage vault observations; do
  docker run --rm -v "homeshed-mcp_$v:/to" -v "$PWD:/backup" alpine:3 sh -c "rm -rf /to/* && tar xzf /backup/homeshed-$v.tgz -C /to"
done
docker compose start
```
Afterwards your memory, saved credentials, app keys and settings are back as they were. Keep the backups as private
as `.env` itself: the vault backup is encrypted, but `.env` holds the key to it.

## Safety switches

| Variable | Default | What it does |
|---|---|---|
| `ENABLE_TOOLS` | empty | Switches on tools that act on your machines. They all start off. |
| `NETWORK_ALLOWED_CIDRS` | empty | Lets the network tools reach your own network. By default they reach public addresses only. |
| `READ_ALLOWED_ROOTS` | your project folder | The folders `files.list` and the code scanners may read. By default: the project Claude Code opened (`CLAUDE_PROJECT_DIR`), or the folder the server started in. |

**Tools that start off:** git commit, push, pull, clone and branch; Docker container start, stop and restart; and
every `dev.*` tool, which runs code. List what you want on, comma separated, by name or by group:

```env
ENABLE_TOOLS=git.commit,git.push
ENABLE_TOOLS=git.*
```

`*` switches on all of them **except** `dev.*`. Those run real code on your machine, so they must be named
(`dev.*`, or one tool such as `dev.python`). Only do that on a server that only you can reach.

**Network tools** (`network.port_check`, `network.dns.lookup`) refuse private, loopback and cloud-metadata
addresses, so nobody can use your server to look around your home network. To check your own machines, list your
network's ranges:

```env
NETWORK_ALLOWED_CIDRS=192.168.0.0/16
```

A range that isn't valid stops the tool with an error, rather than being quietly ignored.

**File tools** (`files.list`, `code.dead_scan`, `code.webhook_retry_check`, `strapi.check`) only read inside allowed
folders, so nobody can point them at your SSH keys or system files. To let them read other folders, list them,
separated by `;` on Windows and `:` on macOS, Linux and Docker:

```env
READ_ALLOWED_ROOTS=C:\Code;D:\Work
READ_ALLOWED_ROOTS=/home/you/code:/srv/apps
```

In Docker, the server can only see folders mounted into its container. Mount the project you want scanned, then
list the path inside the container, for example `READ_ALLOWED_ROOTS=/app:/data`.

## Your own model

| Variable | Default | What it does |
|---|---|---|
| `LOCAL_AI_BASE_URL` | none | Your model server's address. Any OpenAI-compatible server works. |
| `LOCAL_AI_MODEL` | none | Which model on that server to use. |

Ollama, llama.cpp, LM Studio and vLLM all work. If Ollama, LM Studio or llama.cpp is running on its usual port,
`setup` finds it and prints the two settings to use:

```bash
uvx homeshed-mcp setup
```

Or set them yourself. For Ollama:

```env
LOCAL_AI_BASE_URL=http://localhost:11434/v1
LOCAL_AI_MODEL=llama3.1
```

## Cloud models

| Variable | Default | What it does |
|---|---|---|
| `NVIDIA_API_KEY` | none | NVIDIA's hosted models. Free key at [build.nvidia.com](https://build.nvidia.com). |
| `GROQ_API_KEY` | none | Groq. Free tier, about 100 requests a minute. Key at [console.groq.com](https://console.groq.com). |
| `OPENROUTER_API_KEY` | none | OpenRouter's free models: 20 a minute, 50 a day. Key at [openrouter.ai](https://openrouter.ai). |
| `MISTRAL_API_KEY` | none | Mistral. Free tier, about 1 request a second. Key at [console.mistral.ai](https://console.mistral.ai). |
| `GEMINI_API_KEY` | none | Google Gemini. Free tier, about 15 a minute and 1,500 a day. Key at [aistudio.google.com](https://aistudio.google.com). |
| `OVH_AI_ENDPOINTS_TOKEN` | none | OVHcloud AI Endpoints. Works without a key at 2 a minute; a free token raises it. |
| `LOCAL_AI_GATEWAY_URL`, `LOCAL_AI_GATEWAY_KEY` | none | An OpenAI-compatible gateway you run (for example LiteLLM). Only its `nvidia-*` models are used. |

Set any you have. `local_ai.ask` uses them when your own model is busy or you don't run one; your own model is
always tried first. Free-tier limits change, so check each provider's current terms.
**Prompts sent to a cloud model leave your machine.** For anything private, the calling AI passes
`allow_cloud=false`, which keeps the prompt on your own model or fails.

## Memory

Memory works with nothing set. HomeShed keeps it in a built-in SQLite file, and each project can have its own memory:
- **One-line install with Claude Code:** automatic. Claude Code tells the server which folder it's working in
  (`CLAUDE_PROJECT_DIR`).
- **Other apps, and the Docker server:** the memory tools take a `project_dir` argument, and your AI passes the folder
  it's working in when you ask it to (their descriptions say so).

Anything without a project uses the shared memory.

**Apps with their own key** (made on the Control Panel's API access page, or with `POST /clients`):
- Each app always has its own memory, never yours or another app's. Naming a folder never gets it into a project's
  memory.
- It can read the shared memory unless you switch that off for it: **Stop shared memory reads** on its card in the
  Control Panel, or `POST /clients/<name>/shared-read-off` (`shared-read-on` turns it back on).
- When it asks to save something to the shared memory (`scope="global"`), the write waits for you. Approve or decline
  it in the Control Panel (API access, **Shared memory: waiting for you**), or with `GET /memory/pending`, then
  `POST /memory/pending/<id>/approve` or `.../decline`. At most 50 wait per app.

These routes need the owner token, like every other admin route:
```bash
curl -s -H "Authorization: Bearer $MCP_TOKEN" http://127.0.0.1:8765/memory/pending
```

| Variable | Default | What it does |
|---|---|---|
| `MEMORY_DB_PATH` | `DATA_DIR/usage/memory.db` | Where the built-in memory is stored. |

**Optional: memory-core instead.** If you run [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory)'s
memory-core, set all seven of these and HomeShed uses it instead of the built-in store. Nothing is copied between
the two.

| Variable | Default | What it does |
|---|---|---|
| `MEMORY_CORE_BASE_URL` | none | memory-core's address, e.g. `http://localhost:8420`. Setting it switches memory to memory-core. |
| `MEMORY_CORE_BEARER` | none | memory-core's gateway key. |
| `MEMORY_SERVICE_ID` | `default` | memory-core's service id. |
| `MEMORY_USER_KEY` | none | The key of the user memory-core created. |
| `MEMORY_TEAM_ID`, `MEMORY_USER_ID`, `MEMORY_AGENT_ID` | none | That user's ids. |

## Notifications

`notify.send` lets your AI send a push notification to your phone or desktop through [ntfy](https://ntfy.sh).

| Variable | Default | What it does |
|---|---|---|
| `NTFY_BASE_URL` | none | Your ntfy server, e.g. `https://ntfy.sh`. |
| `NTFY_AUTH_TOKEN` | none | Required. An ntfy access token (create one in your ntfy account). |
| `NTFY_DEFAULT_TOPIC` | `mcp-server` | The topic used when the AI doesn't name one. |

Subscribe to your topic in the ntfy app, or the notifications go nowhere.

```env
NTFY_BASE_URL=https://ntfy.sh
NTFY_AUTH_TOKEN=tk_your_token
NTFY_DEFAULT_TOPIC=my-homeshed-alerts
```

## Docker

The Docker tools (list, inspect, logs, and start, stop and restart once switched on) need access to Docker.

> **Warning:** access to the Docker socket is full admin (root) access to that machine. Give it only to a server that
> only you can reach.

- **One-line install:** the tools use the Docker running on your machine. Nothing to set.
- **HomeShed in Docker:** the socket is left out by default. The server runs as an ordinary user, so it needs the
  socket and the socket's group. In `docker-compose.yml`, remove the `#` in front of both lines, put your socket's
  group number in the second, then run `docker compose up -d` again:
  ```yaml
  # - /var/run/docker.sock:/var/run/docker.sock
  # group_add: ["999"]
  ```
  On Linux, `stat -c %g /var/run/docker.sock` prints the number to use.

| Variable | Default | What it does |
|---|---|---|
| `DOCKER_HOST` | unset | Leave unset to use the local Docker. To manage another machine, use SSH: `ssh://user@my-server`. |

Never use a bare `tcp://` address: port 2375 is Docker's unauthenticated API, and anyone who can reach it gets root.

## Other connections

| Variable | Default | What it does |
|---|---|---|
| `KUMA_DB_PATH` | `DATA_DIR/kuma/kuma.db` | [Uptime Kuma](https://github.com/louislam/uptime-kuma)'s database, for `uptime.status`. It's only read, never changed. In Docker, mount Kuma's data volume read-only and point this at `kuma.db`. |
| `GITHUB_SEARCH_TOKEN` | none | Optional. Raises GitHub's rate limit for `bugs.find`'s GitHub search, which is off until the owner switches on the `bugs_search_public` setting. |

## Tool groups

Every tool's description goes to your AI in every conversation, so HomeShed only offers the groups you use. The core
(memory, reasoning, git, files, web, network checks, known bugs, release checks and the rest) is always there.
`uvx homeshed-mcp setup` lists the optional groups, with the ones it detects already ticked; type numbers to switch a
group on or off, then press Enter. Without a terminal (or with `--yes` or `--no`) it asks nothing and keeps the
detected groups.

| Group | What it adds |
|---|---|
| `local-ai` | Your own model (Ollama, LM Studio, llama.cpp) for drafts, summaries and quick decisions |
| `docker` | List and inspect containers (start and stop once switched on) |
| `notify` | Phone notifications through ntfy |
| `images` | [Image generation](#image-generation) |
| `paid-ai` | The [paid-model relay](#paid-language-models-llmchat) |
| `proxmox` | [Proxmox](#proxmox) |
| `shops` | [Etsy and Printify](#online-shops-etsy-printify) |
| `threads` | [Posting to Threads](#threads) (off until you switch posting on) |
| `uptime` | Uptime Kuma monitors |
| `graphify` | Code maps from graphify |
| `dev` | Code-running tools (build, lint, test), for disposable environments only |

The choice is saved in `DATA_DIR/usage/tool_groups.json` (`TOOL_GROUPS_FILE` puts it elsewhere). Run `setup` again to
change it, then restart your AI app. `uvx homeshed-mcp doctor` lists what's hidden. An install that never ran `setup`
hides nothing. Hiding a group doesn't change its safety switches: tools that change things or cost money still start
off.

## Paid language models (llm.chat)

`llm.chat` lets an app think with a paid model without ever holding its key: the server reads the key from its vault,
calls the provider and returns the answer. It spends money, so it's **off** until you switch it on in the Control
Panel (**Settings → AI and tools → Language models**): turn on the provider and set a daily request cap above 0
(optionally a daily token cap too). Your own free model stays `local_ai.*`.

| Name | What |
|---|---|
| `XAI_API_KEY` | Your xAI key. Store it in the Control Panel's vault (or the environment). |

Requests are counted per day across all apps; the usage log keeps tokens and cost, never prompts or answers. `llm.chat`
is a write-risk tool, so grant it to an app by its exact id.

## Image generation

`image.generate` makes pictures with the provider you choose; `image.providers.list` shows what's set up, what each
costs, and whether its pictures may be sold. In the Control Panel (**Settings → AI and tools → Image generation**)
every provider and model has a switch, and each paid provider a daily cap. Paid providers start **off** with a cap of
0, even when their key is stored: a paid provider is never chosen silently.

| Provider | Key name | Notes |
|---|---|---|
| `local-sdcpp` | none | Optional: a local image service you run yourself (stable-diffusion.cpp), at `IMAGE_BASE_URL`. Free. |
| `openai` | `OPENAI_API_KEY` | |
| `stability` | `STABILITY_API_KEY` | Check its terms before selling pictures |
| `replicate` | `REPLICATE_API_TOKEN` | |
| `fal` | `FAL_KEY` | |
| `together` | `TOGETHER_API_KEY` | |
| `xai` | `XAI_API_KEY` | Check its terms before selling pictures |

`IMAGE_DEFAULT_PROVIDER` picks the provider when a call names none. Pictures are saved in `IMAGE_OUTPUT_DIR` (default
`DATA_DIR/usage/images`), each with a file recording its licence and cost.

## Online shops (Etsy, Printify)

Read-only tools that let an app see your shop without holding its keys. Each is **off** until you switch it on
(Control Panel → **Settings → Shops and social**). Buyers' names, emails and addresses are left out of every answer.

| Name | What |
|---|---|
| `ETSY_KEYSTRING`, `ETSY_SHARED_SECRET` | Your Etsy app's keystring and shared secret (etsy.com/developers/your-apps) |
| `ETSY_REFRESH_TOKEN` | Set for you by **Connect Etsy** (below). HomeShed stores the new one Etsy issues on each refresh |
| `ETSY_SHOP_ID` | The shop's number |
| `PRINTIFY_API_TOKEN` | A Printify personal access token (My account → Connections) |
| `PRINTIFY_SHOP_ID` | The shop the shop-level tools use (`printify.shops.list` shows the ids) |

**Connect Etsy:** save your Etsy app's keystring and shared secret first. Then, in the Control Panel under
**Settings → Shops and social**, add the return address it shows to your app on Etsy's "Your Apps" page, and choose
**Connect Etsy**: Etsy's own sign-in opens, read-only (`shops_r listings_r transactions_r`), and HomeShed keeps the
connection and your shop number. If the panel isn't on https, use any https address you own as the return address
and paste the address Etsy sends you to back into the panel. A connection lasts 90 days; connect again to renew it.

## Threads

`threads.publish` posts text (and an optional link) to your Threads account; `threads.status` shows the connection.
Posts are **public**, so it's **off** until you switch on "Let apps post to Threads" (Control Panel → **Settings →
Shops and social**). Connect your account there with **Connect Threads**: Threads' own sign-in, after you save your
Threads app's id and secret under the names that card shows. HomeShed keeps the connection (`THREADS_ACCESS_TOKEN`,
`THREADS_USER_ID`) in its vault and renews it before it expires.

| Name | Default | What |
|---|---|---|
| `THREADS_DAILY_MAX` | `25` | Most posts in 24 hours (Threads allows 250) |

Grant `threads.publish` to an app by its exact id; every post is in the audit log.

**Posts with images** need the image at a public address for a moment, so HomeShed stages it in your own Cloudflare R2
bucket. The bucket stays private: each file gets a signed link that works for about an hour, and the file is deleted
once the post is published.

| Name | What |
|---|---|
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | An R2 API token's key pair, stored in the vault |
| `R2_CLOUDFLARE_S3` | The bucket's S3 address, `https://<account>.r2.cloudflarestorage.com` |
| `R2_BUCKET` | The bucket name (default `homeshed-studio`) |

## Proxmox

Tools that read a Proxmox VE cluster (nodes, VMs and containers, storage, tasks), and three that change it: power,
snapshot and create. **A change never runs from a tool call:** a dry run (the default) checks it, and a real call only
queues it for you to approve with the owner token (`GET /proxmox/pending`, then
`POST /proxmox/pending/<id>/approve` or `/decline`). Nothing can delete.

| Name | What |
|---|---|
| `PROXMOX_HOST` | Required: `https://<address>:8006` |
| `PROXMOX_API_TOKEN` | The whole API token, `user@realm!tokenname=secret` (or `PROXMOX_TOKEN_ID` + `PROXMOX_TOKEN_SECRET`) |
| `PROXMOX_CA_PATH` | The CA file to trust, for a host with Proxmox's own self-signed certificate: a copy of the host's `/etc/pve/pve-root-ca.pem`. HomeShed always checks the certificate; it can't be switched off. |

For reading, give the token `Sys.Audit` and `VM.Audit` (the built-in `PVEAuditor` role). For changes, add only what
you need: `VM.PowerMgmt`, `VM.Snapshot`, or `VM.Allocate` with `Datastore.AllocateSpace`.

## HomeShed Pro

Optional. [HomeShed Pro](../README.md#homeshed-pro) adds the curated rule packs and the known-bugs feed. The free
install never needs it, and nothing is sent to the Pro website until you connect.

**Connect:** on the Pro website, open your account page, then **Access keys**, and make a key (it starts with `mav_`
and is shown once). In the Control Panel, go to **Settings → Account and sharing → Pro membership**, choose
**Paste a Pro key**, paste it and choose **Connect**. Without the Control Panel, run `uvx homeshed-mcp pro connect`
and paste it there (with HomeShed in Docker, add `--server http://127.0.0.1:8765`; see
[Rule packs](rule-packs.md#with-homeshed-in-docker)).

- HomeShed checks the key with the Pro website and keeps it only if the site knows it, in the vault as
  `MAVIS_PRO_TOKEN`. HomeShed never asks for your Pro password: a copy that does isn't ours.
- Once connected, HomeShed asks the Pro website about your membership at most every 10 minutes, and only while the
  Control Panel is open or a Pro feature is used. If the site can't be reached, a membership confirmed in the last
  7 days still counts. A membership that has ended, or a key revoked on the website, ends Pro at once.
- `bugs.sync` fetches the known-bugs feed into `bugs.find`.
- **Disconnect** (same place) forgets the key on this install. It stays on your account until you revoke it on the
  Pro website.

| Variable | Default | What it does |
|---|---|---|
| `MAVIS_PRO_URL` | `https://api.mavis-ai.co.uk` | The Pro website's backend. Your Pro key is sent there, so leave it unset. |

## Keep secrets safe

- Never commit `.env`. The repository's `.gitignore` already excludes it.
- Your AI app's MCP config file holds any keys you put in its `"env"` block in plain text. Keep that file private,
  and don't paste it into issues or chats.
- If a key leaks, replace it at its source (ntfy, NVIDIA, Groq, GitHub) and update your settings.
