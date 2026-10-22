# Upgrading HomeShed

New versions add tools and fixes ([what changed](../CHANGELOG.md)). Upgrading keeps everything HomeShed saved: your
memory, settings, vault and packs live in a data folder of their own
([where it is](configuration.md#where-state-lives)), not in the program.

**Which version do I have?** In a terminal, paste `uvx homeshed-mcp doctor` and press **Enter**. The first line says,
for example, `HomeShed doctor - 0.3.0 - ...`. The newest version is on the
[Releases page](https://github.com/keefng8/homeshed-mcp/releases/latest).

## Steps

Pick the way you installed HomeShed.

### The Claude Code plugin

The plugin runs one exact HomeShed version. In Claude Code, type `/plugin`, choose **homeshed**, then **Update**, and
restart Claude Code.

### Claude Code, Cursor, VS Code and other apps (the one-line install)

Your app starts HomeShed with `uvx`, which keeps the version it downloaded first until you ask for a newer one.

1. **Get the newest version.** In a terminal, paste this and press **Enter**:

   ```
   uvx homeshed-mcp@latest doctor
   ```

   *You should see:* `HomeShed doctor - ` and the newest version number on the first line. From now on, your app uses
   this version.

2. **Restart your AI app completely** (quit it, then open it again). In Claude Code you can type `/mcp` and choose
   **Reconnect** instead.

   *You should see:* HomeShed's tools in your app as before.

3. **If you use rule packs:** paste `uvx homeshed-mcp packs on` and press **Enter**. It shows the change and asks
   first. Only needed when `doctor` says the hook needs refreshing.

**Still the old version?** Quit your AI app, paste `uvx --refresh-package homeshed-mcp homeshed-mcp@latest doctor` and
press **Enter**, then open the app again. It fetches a fresh version list and touches only the downloaded program,
never your data.

### Claude Desktop (the `.mcpb` file)

1. Download the new `homeshed-mcp.mcpb` from the
   [Releases page](https://github.com/keefng8/homeshed-mcp/releases/latest), under **Assets**.
2. Double-click it, as when you first installed it.
3. Restart Claude Desktop completely.

### pip

In the same virtual environment you installed it into: `pip install -U homeshed-mcp`, then restart your AI app.

### Docker

In the folder where you cloned HomeShed:

```
git pull
docker compose up -d --build
```

If you run the Control Panel too, use `docker compose --profile panel up -d --build` instead. Your data stays in the
Docker volumes, and `.env` is kept.

*You should see:* `docker compose ps` lists the server as running.

## For AI agents

When the user asks you to upgrade HomeShed, do this. Every command is safe to run without questions unless it says
otherwise.

1. Read the installed version: `uvx homeshed-mcp doctor --json`, field `version`. Compare it with the latest release
   (`https://pypi.org/pypi/homeshed-mcp/json`, field `info.version`).
2. Upgrade:
   - One-line install (the app's MCP settings run `uvx homeshed-mcp`): run `uvx homeshed-mcp@latest doctor --json`,
     and check that `version` is now the latest.
   - Docker: in the cloned folder, `git pull`, then `docker compose up -d --build` (add `--profile panel` if the
     panel runs).
   - Claude Desktop `.mcpb`: you can't install it for the user. Give them the steps above.
3. Tell the user to restart their AI app (in Claude Code: `/mcp`, then reconnect `homeshed-mcp`). The running
   server is still the old version until they do.
4. After the restart, run `uvx homeshed-mcp doctor --json` again. If `packs.state` is `broken`, the rule-pack hook
   needs refreshing: ask the user first, then run `uvx homeshed-mcp packs on --yes`.
5. If `version` is still old, ask the user to quit the app, then run
   `uvx --refresh-package homeshed-mcp homeshed-mcp@latest doctor --json`. It refreshes only the downloaded program,
   not the data folder.

Don't edit the app's MCP settings to pin `homeshed-mcp@latest`: that checks the network every time the app starts.
