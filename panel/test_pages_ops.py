"""The ops pages (Savings, Models, Proxmox, Containers, Uptime, Tools, Run; 2026-10-27 revamp): their own files ship,
parse, pass their own tests, reach view-only logins (Savings is a viewer's page), load after the engine, and the page
has every element they draw into. Run: python -m pytest -q test_pages_ops.py (node is needed for the script checks)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_main import _isolated_state_files, _patch_mcp_client, owner, viewer  # noqa: F401 - the fixtures

HERE = Path(__file__).parent
STATIC = HERE / "static"
JS = STATIC / "pages-ops.js"
# Elements only the owner's page has (in a private block, so the public copy's page lacks them); the script skips them.
OWNER_ONLY = {"gauges"}
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed, so scripts can't be run here")


@needs_node
def test_the_ops_script_parses_and_its_own_tests_pass():
    r = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["node", str(HERE / "js_tests" / "pages_ops_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout


def test_the_page_loads_the_ops_parts_when_one_of_their_pages_opens_and_has_every_element_they_draw_into():
    """The styles come with the page; the script is fetched the first time one of its pages opens (panel.js's
    PAGE_PARTS, 2026-10-28), long after the engine, and before the AI pages' own file."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/pages-ops.css">' in page and '<script src="/pages-ops.js">' not in page
    parts = (STATIC / "panel.js").read_text(encoding="utf-8").split("const PAGE_PARTS = {")[1].split("};")[0]
    for tab in ("savings", "tools", "run", "docker", "monitors", "proxmox"):
        assert f"{tab}: ['pages-ops']" in parts, tab
    assert "models: ['pages-ops', 'pages-ai']" in parts
    src = JS.read_text(encoding="utf-8")
    ids = set(re.findall(r"\$\('([A-Za-z][\w-]*)'\)", src))
    missing = sorted(i for i in ids if f'id="{i}"' not in page and f'id="{i}"' not in src  # or one the script draws
                     and i not in OWNER_ONLY)
    assert ids and not missing, missing
    assert "if (!$('gauges')) return;" in src  # the public page has no GPU gauges, so drawing them is skipped


def test_the_moved_loaders_live_only_in_the_ops_script():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("loadMonitors", "loadDockerContainers", "loadTools", "loadRun", "loadSavings"):
        assert f"async function {name}(" not in page, name
    assert "const Pve = (() =>" not in page
    src = JS.read_text(encoding="utf-8")
    for name in ("loadMonitors", "loadDockerContainers", "loadTools", "loadRun", "loadSavings", "Pve"):
        assert re.search(rf"\b{name}\b", src), name


def test_the_ops_script_talks_only_to_this_panel():
    src = JS.read_text(encoding="utf-8") + (STATIC / "pages-ops.css").read_text(encoding="utf-8")
    assert not re.search(r"https?://", src)
    assert all(u.startswith("/api/") for u in re.findall(r"""fetch\(\s*[`'"]([^`'"]+)""", src))


def test_the_ops_files_reach_view_only_logins_and_are_rechecked(owner, viewer):
    client, _ = owner
    friend, _, _ = viewer
    for f in ("pages-ops.js", "pages-ops.css"):
        r = client.get("/" + f)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", f
        assert friend.get("/" + f).status_code == 200, f
