# HomeShed Control Panel

A web page for your HomeShed install: what's running, what it has saved you, your AI models and tools, your keys, and
who else may use HomeShed. It's optional. HomeShed works fully without it, and you can add it at any time without
reinstalling or losing anything.

The panel runs on this machine only (127.0.0.1) and always asks for a password.

## Start it

You need HomeShed running over HTTP first, because the panel talks to it there.

### Without Docker

1. In the folder you use for HomeShed, make its settings file (once):

   ```
   uvx homeshed-mcp init
   ```

2. Start HomeShed over HTTP, and leave it running:

   ```
   uvx homeshed-mcp serve --http
   ```

3. In a second terminal, in the same folder, start the panel:

   ```
   uvx --from "homeshed-mcp[panel]" homeshed-mcp panel
   ```

4. Open http://127.0.0.1:9090 in your browser.

The first time the panel starts, it prints a password in that terminal, once. Sign in with it, then choose your own
under **Settings > Account and sharing**.

### With Docker

1. Start HomeShed and the panel together:

   ```
   docker compose --profile panel up -d
   ```

2. Find the first-run password (it's printed once):

   ```
   docker compose logs panel
   ```

3. Open http://127.0.0.1:9090 and sign in.

## What you'll see

| Page | What it shows |
|---|---|
| Overview | What works right now, the tokens kept out of your AI's context, recent tool calls, and who is using HomeShed. |
| To-do | Tasks for your AI agents. It comes with HomeShed Pro; without it you see an example list. |
| Savings | Tokens saved, day by day. |
| AI | Models: the models your tools try, in order, and why each answer came from where it did. Reasoning: try the reasoning tools by hand. |
| Servers | Containers (needs Docker), Uptime (needs Uptime Kuma) and Tools: switch any tool off, and see how much each is used. |
| Rules | Rule monitoring and Observations (corrections and broken rules). |
| API access | A key for each other app or project, with only the tools you grant, a rate limit and an optional expiry. |
| Settings | Every setting, your keys and passwords (stored encrypted by HomeShed), view-only logins, and the Guide. |

A part you haven't set up says **Not set up yet** and what it needs. It never shows as broken.

## Settings

The panel reads these from its environment (Docker: the `.env` file; without Docker, `homeshed-mcp panel` fills in the
first three for you).

| Setting | What it's for |
|---|---|
| `MCP_BASE_URL` | Where HomeShed answers over HTTP, e.g. `http://127.0.0.1:8765`. Required. |
| `MCP_AUTH_TOKEN` | HomeShed's owner token (`init` writes it). Required. It stays on the panel's server; your browser never sees it. |
| `MCP_HOST_HEADER` | The host and port HomeShed expects, e.g. `127.0.0.1:8765`. Required. |
| `DASHBOARD_PASSWORD` | The owner's password. Leave it empty and the panel makes one on its first start, prints it once and keeps only its hash. The sign-in can't be turned off. |
| `DASHBOARD_SESSION_SECRET` | Signs the sign-in cookie. Optional: by default it comes from the owner password, so changing the password signs everyone out. |
| `DASHBOARD_PUBLIC_URL` | The address people outside your network use, sent with new view-only logins. Optional. |
| `DASHBOARD_VIEWER_PASSWORD` | An older single view-only password. It's moved into the view-only logins list once; then remove it. |
| `VAULT_REVEAL_KEY` | Lets the owner show a stored key's value (after re-entering the password). Optional: without it, values can be stored and used, but never shown. |
| `MEMORY_CORE_BASE_URL` | A separate memory-core service. Optional: leave it empty and the panel shows HomeShed's own built-in memory. With it, also set `MEMORY_CORE_BEARER`, `MEMORY_SERVICE_ID`, `MEMORY_USER_KEY`, `MEMORY_TEAM_ID`, `MEMORY_USER_ID` and `MEMORY_AGENT_ID`. |
| `NTFY_HEALTH_URL` | Your ntfy server's health address, e.g. `https://ntfy.sh/v1/health`, so the Overview can say whether phone alerts work. Optional. |
| `GUIDE_LIBRARY_URL` | A central guide library to read the Guide page from. Optional: empty shows the guides that come with the panel. |
| `GPU_SERVICE_URL`, `GPU_SERVICE_TOKEN` | Only for setups that run a separate helper service; a standard install leaves them empty. |
| `TRUST_PROXY_HEADERS` | Set to `1` only when the panel sits behind a proxy you run (it then trusts the visitor's address from it, for the sign-in limit). |
| `PANEL_DATA_DIR` | Where the panel keeps its own files (owner password hash, view-only logins, its settings). Default `/data` in Docker; `homeshed-mcp panel` uses HomeShed's data folder. Each file can also be moved on its own: `PANEL_SETTINGS_FILE`, `OWNER_FILE`, `VIEWERS_FILE`, `REVOKED_SESSIONS_FILE`, `LIFETIME_FILE`. |

## Keeping it safe

- The sign-in is always on, with a limit on wrong tries. Only a salted hash of the password is kept.
- It's published on 127.0.0.1 only. To reach it from elsewhere, put it behind HTTPS (a reverse proxy you run) and set
  `TRUST_PROXY_HEADERS=1`.
- HomeShed's token and the reveal key stay on the panel's server; the browser never gets them.
- View-only logins (Settings > Account and sharing) can look at the Overview, Savings, the To-do example and the Guide,
  and change nothing.

## Remove it

Stop and remove just the panel: `docker compose rm -sf panel` (a plain `down` would stop HomeShed too), or close the
`homeshed-mcp panel` terminal. HomeShed is untouched. The
panel's own files are in its data folder (above) if you want them gone too.
