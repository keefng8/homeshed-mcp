"""The work and admin pages' own files (static/pages-work-admin.js/.css, the whole-panel revamp, 2026-10-27): they ship,
parse, pass their own tests, reach view-only logins (To-do and Guide are on their pages), are re-checked on every load,
the page has every element they draw into, and the vault's values stay out of everything but the reveal dialog.
Run: python -m pytest -q test_pages_work_admin.py (node is needed for the script checks)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_main import _isolated_state_files, _patch_mcp_client, owner, panel_text, viewer  # noqa: F401 - the fixtures

HERE = Path(__file__).parent
STATIC = HERE / "static"
JS, CSS = STATIC / "pages-work-admin.js", STATIC / "pages-work-admin.css"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed, so scripts can't be run here")


def test_main_finds_the_group_files_and_counts_them_in_the_page_version():
    import main
    assert {"pages-work-admin.js", "pages-work-admin.css"} <= set(main.PAGE_FILES)
    assert all(f.startswith("pages-") and f.endswith((".js", ".css")) for f in main.PAGE_FILES)


@needs_node
def test_the_script_parses_and_its_own_tests_pass():
    r = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["node", str(HERE / "js_tests" / "pages_work_admin_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout


def test_the_page_links_the_styles_after_the_engine_and_loads_the_script_first_for_its_pages():
    """The styles come with the page; the script is fetched the first time one of its pages opens (panel.js's
    PAGE_PARTS, 2026-10-28), before that page's own file, since To-do, Rules and Settings use its parts."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page.index('<link rel="stylesheet" href="/telemetry.css">') < page.index('<link rel="stylesheet" href="/pages-work-admin.css">')
    assert '<script src="/pages-work-admin.js">' not in page
    parts = (STATIC / "panel.js").read_text(encoding="utf-8").split("const PAGE_PARTS = {")[1].split("};")[0]
    for tab, own in (("todo", ", 'pages-todo'"), ("rules", ", 'pages-rules'"), ("settings", ", 'pages-settings', 'pages-setup'"),
                     ("observations", ""), ("projects", "")):
        assert f"{tab}: ['pages-work-admin'{own}]" in parts, tab


def test_every_element_the_script_draws_into_is_on_the_page():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    src = JS.read_text(encoding="utf-8")
    ids = set(re.findall(r"\$\('([A-Za-z][\w-]*)'\)", src))
    made = {"waAsk", "waReveal", "obsDrawer", "prjDrawer", "prjPinPath", "prjPinNote", "prjPinErr", "prjPinBtn", "prjPinForm", "prjPins", "cockpit"}
    missing = sorted(i for i in ids - made if f'id="{i}"' not in page)
    assert ids and not missing, missing
    # every live/updated pair WA.live writes to
    for p in ("obs", "prj", "ru", "todo"):
        assert f'id="{p}Live"' in page and f'id="{p}Updated"' in page, p


def test_the_files_are_rechecked_on_every_load_and_reach_view_only_logins(owner, viewer):
    client, main = owner
    friend, _, _ = viewer
    for f in ("pages-work-admin.js", "pages-work-admin.css"):
        r = client.get("/" + f)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", f
        assert friend.get("/" + f).status_code == 200, f


def test_the_script_never_fetches_another_site_and_keeps_vault_values_out_of_logs_urls_and_storage():
    src = JS.read_text(encoding="utf-8")
    # (github.com only as text: a repo link's prefix is cut off for its label)
    assert not re.search(r"\bfetch\(|XMLHttpRequest|https?://(?!www\.w3\.org|github\.com/')", src)
    assert "console." not in src
    # the one place a value goes: the reveal dialog's code element, by textContent, cleared when it closes
    reveal = src[src.index("WA.reveal = "):src.index("// Rules: four dials")]
    assert "code.textContent = r.value" in reveal and "code.textContent = ''" in reveal
    assert not re.search(r"(localStorage|sessionStorage|innerHTML|location|history)[^;\n]*\.value", reveal)
    assert "setTimeout(() => d.close(), 30000)" in reveal and "pw.value = ''" in reveal
    settings = (STATIC / "pages-settings.js").read_text(encoding="utf-8")
    keys = settings[settings.index("async function loadCredentials()"):settings.index("// Settings > Pro membership: paste a key")]
    assert not re.search(r"/reveal[`'\"]", keys)  # only WA.reveal asks for a value; the list never does
    assert "d.value" not in keys


def test_dangerous_actions_ask_first_and_say_whether_they_can_be_undone():
    page = panel_text()
    for fn in ("async function deleteCred(name)", "async function resetViewer(name)", "async function removeViewer(name)",
               "function todoCancel(id, btn)", "function todoBulkCancel(btn)"):
        body = page[page.index(fn):page.index("\n}\n", page.index(fn))]
        assert "UI.confirm(" in body and "permanent: true" in body, fn
    settings = (STATIC / "pages-settings.js").read_text(encoding="utf-8")
    assert "alert(" not in settings[settings.index("async function loadViewers()"):]
