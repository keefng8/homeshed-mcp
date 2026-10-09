import base64
import json
import sys
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

if "registry" not in sys.modules:  # staging only: in mcp-server the real registry is importable
    stub = types.ModuleType("registry")
    stub.tool = lambda **_: (lambda f: f)
    sys.modules["registry"] = stub
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.mcp import install_links as mod  # noqa: E402

install_links = getattr(mod.install_links, "__wrapped__", mod.install_links)


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_uvx_buttons_decode_to_the_stdio_config():
    out = install_links(package="demo-mcp")
    cursor = query(out["buttons"]["cursor"])
    assert cursor["name"] == "demo-mcp"
    assert json.loads(base64.b64decode(cursor["config"])) == {"command": "uvx", "args": ["demo-mcp"]}
    vs = query(out["buttons"]["vscode"])
    assert json.loads(vs["config"]) == {"type": "stdio", "command": "uvx", "args": ["demo-mcp"]}
    assert query(out["buttons"]["vscode_insiders"])["quality"] == "insiders"


def test_npx_scoped_package_uses_dash_y_and_short_name():
    out = install_links(package="@acme/weather-mcp", runner="npx")
    assert out["name"] == "weather-mcp"
    assert out["commands"]["claude_code"] == "claude mcp add -s user weather-mcp -- npx -y @acme/weather-mcp"
    desktop = json.loads(out["configs"]["claude_desktop"]["json"])
    assert desktop == {"mcpServers": {"weather-mcp": {"command": "npx", "args": ["-y", "@acme/weather-mcp"]}}}


def test_every_json_config_parses_and_client_formats_match_their_docs():
    out = install_links(package="demo-mcp")
    for client, cfg in out["configs"].items():
        if "json" in cfg:
            json.loads(cfg["json"])
    assert json.loads(out["configs"]["zed"]["json"])["context_servers"]["demo-mcp"]["source"] == "custom"
    goose = out["configs"]["goose"]["yaml"]
    assert all(f in goose for f in ("type: stdio", "name: demo-mcp", "cmd: uvx", "timeout: 300"))


def test_http_url_gives_only_verified_clients():
    out = install_links(url="https://example.com/mcp", name="example")
    assert out["transport"] == "http"
    assert json.loads(base64.b64decode(query(out["buttons"]["cursor"])["config"])) == {"url": "https://example.com/mcp"}
    assert set(out["configs"]) == {"cursor", "vscode", "opencode"}
    assert out["commands"] == {"claude_code": "claude mcp add -s user --transport http example https://example.com/mcp"}


def test_markdown_holds_buttons_command_and_every_config():
    out = install_links(package="demo-mcp")
    md = out["markdown"]
    assert "Add to Cursor" in md and out["commands"]["claude_code"] in md
    assert all(f"**{c}**" in md for c in out["configs"])


@pytest.mark.parametrize("kwargs", [
    {},                                                    # neither
    {"package": "a", "url": "https://x.dev/mcp"},          # both
    {"package": "bad name; rm -rf /"},                     # shell metacharacters
    {"package": "ok", "runner": "curl"},                   # unknown runner
    {"url": "http://example.com/mcp"},                     # plain http off localhost
    {"url": "https://user:pw@example.com/mcp"},            # credentials in the URL
    {"url": "file:///etc/passwd"},
    {"package": "ok", "name": "has space"},
])
def test_rejects_bad_input(kwargs):
    with pytest.raises(mod.InstallLinksError):
        install_links(**kwargs)


def test_localhost_http_is_allowed():
    assert install_links(url="http://localhost:8765/mcp")["name"] == "localhost"
