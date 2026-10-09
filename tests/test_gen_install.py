import base64
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_install as g  # noqa: E402

PKG = "demo-mcp"
README = "# x\n<!-- INSTALL:START old -->\nstale\n<!-- INSTALL:END -->\ntail\n"


def query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_cursor_link_decodes_to_uvx_config():
    q = query(g.links(PKG)["cursor"])
    assert q["name"] == PKG
    assert json.loads(base64.b64decode(q["config"])) == {"command": "uvx", "args": [PKG]}


def test_vscode_links_decode_to_stdio_config():
    l = g.links(PKG)
    for key in ("vscode", "vscode_insiders"):
        q = query(l[key])
        assert q["name"] == PKG
        assert json.loads(q["config"]) == {"type": "stdio", "command": "uvx", "args": [PKG]}
    assert query(l["vscode_insiders"])["quality"] == "insiders"
    assert "quality" not in query(l["vscode"])


def test_block_has_every_client_and_valid_json_snippets():
    b = g.block(PKG)
    for needle in (f"claude mcp add -s user {PKG} -- uvx {PKG}", f"codex mcp add {PKG}", f"gemini mcp add {PKG}",
                   "code --add-mcp", "Windsurf", "Cline", "Zed", "OpenCode", "Goose", ".mcpb", "ENABLE_TOOLS"):
        assert needle in b, needle
    snippets = [s.split("\n", 1)[1] for s in b.split("```json")[1:]]
    for s in snippets:
        json.loads(s.split("```")[0])  # every JSON snippet parses
    assert len(snippets) == 4


def test_client_formats_match_their_official_docs():
    b = g.block(PKG)
    zed = json.loads(b.split("#### Zed")[1].split("```json")[1].split("```")[0])
    assert zed == {"context_servers": {PKG: {"source": "custom", "command": "uvx", "args": [PKG]}}}  # zed.dev docs
    goose = b.split("#### Goose")[1].split("```yaml")[1].split("```")[0]
    for field in ("type: stdio", f"name: {PKG}", "enabled: true", "cmd: uvx", "timeout: 300"):  # goose config-file docs
        assert field in goose, field
    assert "about two minutes" not in b  # never claim an untimed setup


def test_no_secret_values_in_output():
    assert "Bearer" not in g.block(PKG) and "API_KEY=" not in g.block(PKG)


@pytest.fixture
def readme(tmp_path):
    p = tmp_path / "README.md"
    p.write_text(README, encoding="utf-8")
    return p


def test_writes_idempotently_and_keeps_surroundings(readme):
    args = ["--package", PKG, "--readme", str(readme)]
    assert g.main(args) == 0
    once = readme.read_text(encoding="utf-8")
    assert g.main(args) == 0
    assert readme.read_text(encoding="utf-8") == once
    assert once.startswith("# x\n") and once.endswith("\ntail\n") and "stale" not in once


def test_check_mode(readme):
    args = ["--package", PKG, "--readme", str(readme)]
    assert g.main(args + ["--check"]) == 1
    g.main(args)
    assert g.main(args + ["--check"]) == 0


def test_missing_markers(readme):
    readme.write_text("nothing", encoding="utf-8")
    with pytest.raises(SystemExit):
        g.main(["--package", PKG, "--readme", str(readme)])
