"""The Overview below the cockpit's top half (overview-lower, 2026-10-27): its files ship, parse, pass their own tests,
reach view-only logins, are re-checked on every load; the page loads them after the cockpit and every loader hands its
reply over without depending on them. Run: python -m pytest -q test_ovlower.py (node is needed for the script checks)."""
import re
import shutil
import subprocess
from pathlib import Path

from test_main import _isolated_state_files, _patch_mcp_client, owner, panel_text, viewer  # noqa: F401 - the fixtures

HERE = Path(__file__).parent
STATIC = HERE / "static"
import pytest  # noqa: E402

needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed, so scripts can't be run here")


def test_the_files_are_the_ones_main_names():
    import main
    assert main.OVERVIEW_LOWER_FILES == ("pages-ovlower.js", "pages-ovlower.css")
    assert all((STATIC / f).is_file() for f in main.OVERVIEW_LOWER_FILES)


@needs_node
def test_the_script_parses_and_its_own_tests_pass():
    r = subprocess.run(["node", "--check", str(STATIC / "pages-ovlower.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["node", str(HERE / "js_tests" / "ovlower_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout


def test_the_page_loads_it_after_the_cockpit_and_every_loader_hands_over_safely():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/pages-ovlower.css">' in page
    a, b = (page.index(f'<script src="/{f}"></script>') for f in ("cockpit.js", "pages-ovlower.js"))
    assert a < b < page.index('<script src="/panel.js"></script>')
    page = panel_text()  # the loaders' code
    for kind in ("context", "activity", "caps", "risk", "memory", "tokparts", "pipe", "rw"):  # the public cards' hand-overs
        i = page.find(f"OVL.ingest('{kind}'")
        assert i > 0, kind
        line = page[page.rfind("\n", 0, i):i]
        assert "window.OVL" in line, kind  # guarded: a file that didn't load never breaks the loader
    # a helper used inside a row template is always guarded, so a file that didn't load never breaks a loader
    for helper in ("ringMark", "gates", "relBezel", "traffic", "split"):
        assert f"window.OVL ? OVL.{helper}(" in page, helper
    assert "shadowBlur" not in page[page.index("function drawNeon"):page.index("// Which memory categories are expanded")]


def test_the_files_are_rechecked_on_every_load_and_reach_view_only_logins(owner, viewer):
    client, main = owner
    friend, _, _ = viewer
    for f in main.OVERVIEW_LOWER_FILES:
        r = client.get("/" + f)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", f
        assert friend.get("/" + f).status_code == 200, f


def test_it_never_fetches_and_never_loads_from_another_site():
    for f in ("pages-ovlower.js", "pages-ovlower.css"):
        src = (STATIC / f).read_text(encoding="utf-8")
        assert not re.search(r"\bfetch\(|XMLHttpRequest|setInterval|https?://|@import", src), f


def test_cockpit_off_brings_the_classic_cards_back():
    css = (STATIC / "pages-ovlower.css").read_text(encoding="utf-8")
    assert ".ovl-i { display: none; }" in css and "#opsView.ovl-ck .ovl-plain { display: none !important; }" in css
    js = (STATIC / "pages-ovlower.js").read_text(encoding="utf-8")
    assert "ck.classList.contains('classic')" in js and "attributeFilter: ['class']" in js
