import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_docs as c  # noqa: E402

CLI = '''import argparse
p = argparse.ArgumentParser()
sub = p.add_subparsers()
s = sub.add_parser("setup")
s.add_argument("--model", action="store_true")
sub.add_parser("doctor")
'''


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    write(tmp_path, "pyproject.toml", '[project]\nname = "demo-mcp"\n')
    write(tmp_path, "cli.py", CLI)
    write(tmp_path, "server.py", 'import os\nPORT = os.environ.get("MCP_PORT")\n')
    write(tmp_path, "local_ai_backends.json", '{"backends": [{"key_env": "GROQ_API_KEY"}]}')
    write(tmp_path, "docs/configuration.md",
          "# Configuration\n\n## HTTP and Docker\n\n<a id=\"http\"></a>\n\n| Variable | Default | What |\n|---|---|---|\n"
          "| `MCP_PORT` | `8765` | port |\n| `GROQ_API_KEY` | none | key |\n\n## Docker\n\n```bash\n# not a heading\n```\n")
    write(tmp_path, "README.md", "# Demo\n\nSee [docker](docs/configuration.md#docker) and [http](docs/configuration.md#http).\n"
                                 "Run `uvx demo-mcp setup --model`, then `uvx demo-mcp doctor`.\n"
                                 '<a href="#demo">top</a>\n')
    return tmp_path


def test_a_matching_repo_passes(repo):
    assert c.main(["--root", str(repo)]) == 0


def test_a_documented_setting_no_code_reads_fails(repo, capsys):
    cfg = repo / "docs/configuration.md"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "| `GHOST_SETTING` | none | nothing reads it |\n", encoding="utf-8")
    assert c.main(["--root", str(repo)]) == 1
    assert "GHOST_SETTING is documented but no code reads it" in capsys.readouterr().out


def test_a_broken_anchor_fails_and_a_heading_inside_a_code_block_doesnt_count(repo, capsys):
    write(repo, "docs/getting-started.md", "[x](configuration.md#not-a-heading) [y](configuration.md#not-a-heading-2)\n")
    assert c.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "#not-a-heading isn't a heading in configuration.md" in out


def test_an_unknown_command_or_option_fails(repo, capsys):
    write(repo, "docs/x.md", "Run `uvx demo-mcp setup --write` and `uvx demo-mcp deploy`.\n")
    assert c.main(["--root", str(repo)]) == 1
    out = capsys.readouterr().out
    assert "--write isn't an option" in out and "`demo-mcp deploy` isn't a command" in out


def test_a_package_name_not_after_uvx_is_not_a_command(repo):
    write(repo, "docs/x.md", "claude mcp add --transport http demo-mcp http://127.0.0.1:8765/mcp\n")
    assert c.main(["--root", str(repo)]) == 0


def test_the_cli_file_can_live_anywhere(repo):
    (repo / "cli.py").rename(repo / "app_cli.py")
    write(repo, "docs/x.md", "Run `uvx demo-mcp deploy`.\n")
    assert c.main(["--root", str(repo), "--cli", "app_cli.py"]) == 1   # found and checked there
    assert c.main(["--root", str(repo)]) == 0                           # default cli.py absent: skipped


def test_without_code_only_links_are_checked(repo, capsys):
    (repo / "cli.py").unlink()
    cfg = repo / "docs/configuration.md"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "| `GHOST_SETTING` | none | x |\n", encoding="utf-8")
    assert c.main(["--root", str(repo)]) == 0
    assert "not checked" in capsys.readouterr().out


@pytest.mark.parametrize("heading, anchor", [
    ("HTTP and Docker", "http-and-docker"),
    ("HomeShed Pro, by Someone (coming soon)", "homeshed-pro-by-someone-coming-soon"),
    ("`setup` and `doctor`", "setup-and-doctor"),
    ("Why people use it", "why-people-use-it"),
])
def test_slugs_follow_github(heading, anchor):
    assert c.slug(heading) == anchor
