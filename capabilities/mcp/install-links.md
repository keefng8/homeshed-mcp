# mcp.install_links

Install instructions for any MCP server, for every major app, from one package name or URL.

## When to use it

You publish or share an MCP server and want one-click buttons, one-line commands and copy-paste configs for
each app, without looking up every format.

## Inputs

| Input | Meaning |
|---|---|
| `package` | The PyPI or npm package that runs the server. Give this **or** `url`. |
| `runner` | `uvx` (PyPI, the default) or `npx` (npm). Ignored with `url`. |
| `url` | A remote (streamable HTTP) server. `https://` only; `http://` only on localhost. |
| `name` | Optional name the apps show. Default: the package's name (without an npm scope) or the URL's host. Letters, numbers, dots, dashes and underscores. |

## Returns

- `name`, `transport` (`stdio` or `http`)
- `buttons`: Cursor, VS Code, VS Code Insiders (web links, so they work inside a GitHub README)
- `commands`: Claude Code, Codex, Gemini CLI and the VS Code CLI for packages; Claude Code only for URLs
- `configs`: Claude Desktop, Cursor, Windsurf, VS Code, Zed, OpenCode, Goose and Codex for packages; Cursor,
  VS Code and OpenCode for URLs. Other apps are left out rather than guessed.
- `markdown`: a ready-to-paste README block

## Example

Input `{"package": "weather-mcp"}` gives, among the rest:

```bash
claude mcp add -s user weather-mcp -- uvx weather-mcp
```

## Risks

Read-only and pure text generation: no network calls, nothing installed. It never puts keys or headers in its
output, so add secrets through each app's own settings. It rejects shell metacharacters in names and credentials
in URLs.

Formats were checked against each app's docs on 2026-09-29 (Cursor, VS Code, Zed, Goose, the MCP registry).
