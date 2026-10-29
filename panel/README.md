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

2. Show the first-run password (the panel made it on its first start and keeps it in a file only it can read):

   ```
   docker compose exec panel cat /data/first-run-password.txt
   ```

3. Open http://127.0.0.1:9090 and sign in. Then change the password in Settings > Account and sharing: the file is
   deleted when you do.

## What you'll see

| Page | What it shows |
|---|---|
| Overview | What works right now, your recent chats to resume, the tokens kept out of your AI's context, GitHub figures, tool calls over time, and a map of where the savings happen. Move, resize or minimise any window; the panel remembers your layout in this browser (Reset layout puts it back). The Motion switch turns decorative movement off. |
| To-do | Tasks for your AI agents. It comes with HomeShed Pro; without it you see an example list. |
| Savings | Tokens saved, day by day. |
| AI | Models: the models your tools try, in order, and why each answer came from where it did. Reasoning: try the reasoning tools by hand. |
| Servers | Containers (needs Docker), Uptime (needs Uptime Kuma) and Tools: switch any tool off, and see how much each is used. |
| Rules | Rule monitoring and Observations (corrections and broken rules). |
| API access | A key for each other app or project, with only the tools you grant, a rate limit and an optional expiry. |
| Settings | Every setting, your keys and passwords (stored encrypted by HomeShed), view-only logins, and the Guide. |

A part you haven't set up says **Not set up yet** and what it needs. It never shows as broken.

### Where the Overview's numbers come from

Each figure says what kind it is. A solid line or ring means measured, a dashed one means estimated, and a hatched one
means there's no data. Measured and estimated figures are never added into one number.

| Figure | Source | Kind |
|---|---|---|
| Command output trimmed | RTK's own count of the shell output it cut (needs RTK on your PC) | Measured |
| Tool lists not loaded, replies trimmed | HomeShed's front door counts the characters it didn't send | Characters measured; tokens estimated (characters ÷ 4) |
| Writing, reading and search done locally | HomeShed's sizes-only counters, one rule per tool | Estimated (characters ÷ 4) |
| Tool calls, errors, refusals (all time, per hour) | HomeShed's counters | Counted |
| Response time | HomeShed's last 200 calls (cleared when it restarts) | Measured |
| GitHub stars, downloads, views, clones | GitHub and PyPI, through the release reports | Reported by GitHub |
| As money | The tokens above at the API price you choose in Settings | Estimated |
| Cached input | Your AI provider keeps this; HomeShed isn't sent it | No data |
| Context dial and gauges | Each Claude session's own status line, through the context monitor on your PC | Measured |
| Release gates dial | What the agent preparing the release last reported (gates passed, readiness checks) | Reported |
| Call tape, read/write dials | HomeShed's last 200 calls (when, how long, which kind) and its all-time counts | Measured, counted |
| Tools and memory rings | The tool list (by group) and the memory store's fact counts | Counted |

The windows under the token pipeline (`static/pages-ovlower.js` and `.css`) draw these figures in the same style as the
top half. With the **Cockpit** switch off they go back to plain numbers and thin bars.

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

## How the page is put together

Plain files in `static/`, no build step. `index.html` holds the markup and a small first script (the theme, before
anything is drawn). `panel.css` has the shared styles and each page group has its own `pages-<area>.css`, all linked
by the page. `panel.js` is the core: shared helpers, the sidebar and `switchTab`, the poll and the page-file loader.
`pages-overview.js` is the Overview, and `panel-start.js` starts everything once those are in. Every other page's code
is in its group's `pages-<area>.js` (Models and Reasoning in `pages-ai.js`, Settings in `pages-settings.js`, and so on),
fetched the first time that page opens; the file says what opening, leaving and polling its page does with
`Pages.on(...)`, and the group's entry in `PAGE_PARTS` (in `panel.js`) lists the files it needs. `node
js_tests/panel_files_test.js` checks the files load in an order that works and that each page opens without an error.

## Remove it

Stop and remove just the panel: `docker compose rm -sf panel` (a plain `down` would stop HomeShed too), or close the
`homeshed-mcp panel` terminal. HomeShed is untouched. The
panel's own files are in its data folder (above) if you want them gone too.
