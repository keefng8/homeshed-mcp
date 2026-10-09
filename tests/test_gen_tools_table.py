import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gen_tools_table as g  # noqa: E402

README = "# x\n\n<!-- TOOLS:START old -->\nstale\n<!-- TOOLS:END -->\n\ntail\n"


def cap(root: Path, cat: str, action: str, **extra):
    d = root / cat
    d.mkdir(parents=True, exist_ok=True)
    m = {"id": f"{cat}.{action}", "category": cat, "risk": "read", "description": f"{action} | thing\n  wrapped"}
    m.update(extra)
    (d / f"{action}.json").write_text(json.dumps(m), encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    caps = tmp_path / "capabilities"
    cap(caps, "web", "read")
    cap(caps, "git", "push", risk="write", requires=["git on PATH"])
    cap(caps, "image", "generate")
    (tmp_path / "README.md").write_text(README, encoding="utf-8")
    return tmp_path


def args(repo, *extra):
    # Point --toolswitch into the fixture, so the real repo's toolswitch.py never leaks into these tests.
    switch = [] if "--toolswitch" in extra else ["--toolswitch", str(repo / "toolswitch.py")]
    return ["--capabilities", str(repo / "capabilities"), "--readme", str(repo / "README.md"), *switch, *extra]


def test_writes_block_and_keeps_surrounding_text(repo):
    assert g.main(args(repo)) == 0
    out = (repo / "README.md").read_text(encoding="utf-8")
    assert out.startswith("# x\n") and out.endswith("\n\ntail\n")
    assert "stale" not in out
    assert "**3 tools** in 3 groups" in out
    assert "| `git.push` | push \\| thing wrapped | write | git on PATH |" in out
    assert "| `web.read` | read \\| thing wrapped | read | nothing |" in out


def test_exclude_drops_categories(repo):
    g.main(args(repo, "--exclude", "image"))
    out = (repo / "README.md").read_text(encoding="utf-8")
    assert "image.generate" not in out and "**2 tools**" in out


def test_exclude_drops_single_tool_ids(repo):
    g.main(args(repo, "--exclude", "git.push"))
    out = (repo / "README.md").read_text(encoding="utf-8")
    assert "git.push" not in out and "web.read" in out and "**2 tools** in 2 groups" in out


def test_counts_in_prose_are_filled_from_the_manifests(repo):
    readme = repo / "README.md"
    readme.write_text(README + "All <!--count:total-->99<!--/count--> tools; <!--count:ready-->0<!--/count--> ready, "
                      "<!--count:needs-->0<!--/count--> need setup.\n", encoding="utf-8")
    g.main(args(repo))
    out = readme.read_text(encoding="utf-8")
    assert "<!--count:total-->3<!--/count-->" in out           # web.read, git.push, image.generate
    assert "<!--count:ready-->2<!--/count-->" in out           # no `requires`
    assert "<!--count:needs-->1<!--/count-->" in out           # git.push requires git
    assert g.main(args(repo, "--check")) == 0


def test_default_off_tools_are_counted_apart_and_marked(repo):
    (repo / "toolswitch.py").write_text('DEFAULT_OFF = frozenset({"git.push"})\n', encoding="utf-8")
    readme = repo / "README.md"
    readme.write_text(README + "<!--count:ready-->0<!--/count--> <!--count:off-->0<!--/count--> "
                      "<!--count:needs-->0<!--/count-->\n", encoding="utf-8")
    g.main(args(repo, "--toolswitch", str(repo / "toolswitch.py")))
    out = readme.read_text(encoding="utf-8")
    assert "<!--count:ready-->2<!--/count-->" in out and "<!--count:off-->1<!--/count-->" in out
    assert "<!--count:needs-->0<!--/count-->" in out and "| write 🔒 |" in out


def test_a_stale_count_fails_the_check(repo):
    g.main(args(repo))
    readme = repo / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "<!--count:total-->57<!--/count-->\n", encoding="utf-8")
    assert g.main(args(repo, "--check")) == 1


def test_check_fails_when_stale_then_passes(repo):
    assert g.main(args(repo, "--check")) == 1
    g.main(args(repo))
    assert g.main(args(repo, "--check")) == 0


def test_missing_markers_is_an_error(repo):
    (repo / "README.md").write_text("no markers", encoding="utf-8")
    with pytest.raises(SystemExit):
        g.main(args(repo))
