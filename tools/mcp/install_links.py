"""mcp.install_links. See ../../capabilities/mcp/install-links.md."""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, urlencode, urlsplit

from registry import tool

PACKAGE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[A-Za-z0-9][A-Za-z0-9._-]{0,213}$")
RUNNERS = {"uvx": lambda p: [p], "npx": lambda p: ["-y", p]}


class InstallLinksError(ValueError):
    """Bad input: an invalid package name, runner or URL."""


def _compact(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def _pretty(obj) -> str:
    return json.dumps(obj, indent=2)


def _check_url(url: str) -> str:
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise InstallLinksError("url must be https:// (or http:// on localhost)")
    if not parts.hostname or parts.username or parts.password:
        raise InstallLinksError("url must have a host and no credentials in it")
    return url


def _buttons(name: str, cursor_cfg: dict, vscode_cfg: dict) -> dict:
    vscode = "https://insiders.vscode.dev/redirect/mcp/install?" + urlencode(
        {"name": name, "config": _compact(vscode_cfg)}, quote_via=quote)
    return {
        "cursor": "https://cursor.com/en/install-mcp?" + urlencode(
            {"name": name, "config": base64.b64encode(_compact(cursor_cfg).encode()).decode()}, quote_via=quote),
        "vscode": vscode,
        "vscode_insiders": vscode + "&quality=insiders",
    }


def _stdio(name: str, package: str, runner: str) -> dict:
    args = RUNNERS[runner](package)
    base = {"command": runner, "args": args}
    shell_args = " ".join(args)
    return {
        "buttons": _buttons(name, base, {"type": "stdio", **base}),
        "commands": {
            "claude_code": f"claude mcp add -s user {name} -- {runner} {shell_args}",
            "codex": f"codex mcp add {name} -- {runner} {shell_args}",
            "gemini": f"gemini mcp add {name} {runner} {shell_args}",
            "vscode": f"code --add-mcp '{_compact({'name': name, **base})}'",
        },
        "configs": {
            "claude_desktop": {"file": "claude_desktop_config.json", "json": _pretty({"mcpServers": {name: base}})},
            "cursor": {"file": "~/.cursor/mcp.json", "json": _pretty({"mcpServers": {name: base}})},
            "windsurf": {"file": "~/.codeium/windsurf/mcp_config.json", "json": _pretty({"mcpServers": {name: base}})},
            "vscode": {"file": ".vscode/mcp.json", "json": _pretty({"servers": {name: {"type": "stdio", **base}}})},
            "zed": {"file": "settings.json",
                    "json": _pretty({"context_servers": {name: {"source": "custom", **base}}})},
            "opencode": {"file": "opencode.json",
                         "json": _pretty({"mcp": {name: {"type": "local", "command": [runner, *args], "enabled": True}}})},
            "goose": {"file": "~/.config/goose/config.yaml",
                      "yaml": (f"extensions:\n  {name}:\n    type: stdio\n    name: {name}\n    enabled: true\n"
                               f"    cmd: {runner}\n    args: {_compact(args)}\n    timeout: 300\n")},
            "codex": {"file": "~/.codex/config.toml",
                      "toml": f'[mcp_servers.{name}]\ncommand = "{runner}"\nargs = {_compact(args)}\n'},
        },
    }


def _http(name: str, url: str) -> dict:
    # Only formats checked against each client's docs; other clients are left out rather than guessed.
    return {
        "buttons": _buttons(name, {"url": url}, {"type": "http", "url": url}),
        "commands": {"claude_code": f"claude mcp add -s user --transport http {name} {url}"},
        "configs": {
            "cursor": {"file": "~/.cursor/mcp.json", "json": _pretty({"mcpServers": {name: {"url": url}}})},
            "vscode": {"file": ".vscode/mcp.json", "json": _pretty({"servers": {name: {"type": "http", "url": url}}})},
            "opencode": {"file": "opencode.json",
                         "json": _pretty({"mcp": {name: {"type": "remote", "url": url, "enabled": True}}})},
        },
    }


def _markdown(name: str, out: dict) -> str:
    b = out["buttons"]
    lines = [
        f'<a href="{b["cursor"]}"><img alt="Add to Cursor" src="https://cursor.com/deeplink/mcp-install-dark.svg" height="32"></a>',
        f'<a href="{b["vscode"]}"><img alt="Install in VS Code" src="https://img.shields.io/badge/VS_Code-Install-0098FF?style=for-the-badge&logo=visualstudiocode&logoColor=white"></a>',
        "",
        "```bash", out["commands"]["claude_code"], "```",
        "",
        "<details><summary><b>Other apps</b></summary>",
        "",
    ]
    for client, cfg in out["configs"].items():
        kind = next(k for k in ("json", "yaml", "toml") if k in cfg)
        lines += [f"**{client}** (`{cfg['file']}`)", "", f"```{kind}", cfg[kind].rstrip("\n"), "```", ""]
    lines.append("</details>")
    return "\n".join(lines)


@tool(name="install_links", category="mcp", doc="mcp/install-links.md")
def install_links(package: str = "", runner: str = "uvx", url: str = "", name: str = "") -> dict:
    """One-click install buttons, one-line commands and per-app configs for any MCP server. Pure text
    generation: no network calls, nothing installed.

    Args:
        package: the PyPI or npm package that runs the server (stdio). Give this or url.
        runner: "uvx" (PyPI) or "npx" (npm). Ignored with url.
        url: a remote (streamable HTTP) server address instead of a package.
        name: the name apps show for it (default: the package name, or the URL's host).

    Returns:
        {name, transport, buttons: {cursor, vscode, vscode_insiders}, commands: {...}, configs: {...}, markdown}

    Raises:
        InstallLinksError: neither or both of package/url, a bad package name, runner or URL.
    """
    package, url, name = package.strip(), url.strip(), name.strip()
    if bool(package) == bool(url):
        raise InstallLinksError("give exactly one of package or url")
    if url:
        _check_url(url)
        name = name or urlsplit(url).hostname
        out = _http(name, url)
    else:
        if not PACKAGE.match(package):
            raise InstallLinksError(f"not a valid PyPI or npm package name: {package!r}")
        if runner not in RUNNERS:
            raise InstallLinksError(f"runner must be one of {sorted(RUNNERS)}")
        name = name or package.rsplit("/", 1)[-1]
        out = _stdio(name, package, runner)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name):
        raise InstallLinksError("name may use letters, numbers, dots, dashes and underscores only")
    return {"name": name, "transport": "http" if url else "stdio", **out, "markdown": _markdown(name, out)}
