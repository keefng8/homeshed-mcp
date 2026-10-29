"""The Overview cockpit (2026-10-27): the telemetry engine's files ship, parse, pass their own tests, reach view-only
logins, are re-checked on every load, and the page has every element the cockpit draws into. Run: python -m pytest -q
test_cockpit.py (node is needed for the script checks)."""
import re
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from test_main import _isolated_state_files, _patch_mcp_client, owner, panel_text, viewer  # noqa: F401 - the fixtures

HERE = Path(__file__).parent
STATIC = HERE / "static"
needs_node = pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed, so scripts can't be run here")


def test_the_engine_files_are_the_ones_main_names():
    import main
    assert main.ENGINE_FILES == ("telemetry.js", "telemetry.css", "cockpit.js")
    assert all((STATIC / f).is_file() for f in main.ENGINE_FILES)


@needs_node
def test_the_engine_scripts_parse_and_its_own_tests_pass():
    for f in ("telemetry.js", "cockpit.js"):
        r = subprocess.run(["node", "--check", str(STATIC / f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
    r = subprocess.run(["node", str(HERE / "js_tests" / "telemetry_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert " 0 failed" in r.stdout


@needs_node
def test_the_page_figures_helpers_pass_their_own_tests():
    """HSTelemetry.figures (the end of telemetry.js): margin, trend percent, gaps in a trace, counts (2026-10-27)."""
    r = subprocess.run(["node", str(HERE / "js_tests" / "figures_test.js")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and " 0 failed" in r.stdout, r.stdout + r.stderr


def test_the_page_loads_the_engine_before_its_own_script_and_has_every_element_it_draws_into():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/telemetry.css">' in page
    a, b = (page.index(f'<script src="/{f}"></script>') for f in ("telemetry.js", "cockpit.js"))
    assert a < b < page.index('<script src="/panel.js"></script>')  # the cockpit exists before the loaders feed it
    ids = set(re.findall(r"\$\('([A-Za-z][\w-]*)'\)", (STATIC / "cockpit.js").read_text(encoding="utf-8")))
    missing = sorted(i for i in ids if f'id="{i}"' not in page)
    assert ids and not missing, missing
    # the page hands its replies over; a missing cockpit (a file that didn't load) never breaks a loader
    code = panel_text()
    for kind in ("tokens", "savings", "activity", "progress", "clients", "viewer"):
        assert f"window.HSCockpit && HSCockpit.ingest('{kind}'" in code, kind
    assert "window.HSCockpit && HSCockpit.summary(on)" in code


def test_the_engine_files_are_rechecked_on_every_load_and_reach_view_only_logins(owner, viewer):
    client, main = owner
    friend, _, _ = viewer
    for f in main.ENGINE_FILES:
        r = client.get("/" + f)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", f
        assert friend.get("/" + f).status_code == 200, f
    assert client.get("/favicon.ico").headers.get("cache-control") != "no-cache"  # only the engine's files


def test_viewers_get_the_hourly_counts_and_front_door_totals_but_no_app_names(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    usage = {"since": 1, "estimated_tokens_saved": 5, "by_rule": {}, "capabilities": {},
             "clients": {"secret-app": {"calls": 3}},
             "front_door_total": {"listings": 2, "hidden_chars": 400, "calls": 1, "trimmed_chars": 4, "app": "secret-app"},
             "hours": [{"t": 3600, "calls": 2, "errors": 0, "refused": 1, "saved_chars": 9, "fd_chars": 0, "client": "secret-app"}]}
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(lambda req: httpx.Response(200, json=usage)))
    seen = friend.get("/api/tokens").json()["estimate"]
    assert seen["hours"] == [{"t": 3600, "calls": 2, "errors": 0, "refused": 1, "saved_chars": 9, "fd_chars": 0}]
    assert seen["front_door_total"] == {"listings": 2, "hidden_chars": 400, "calls": 1, "trimmed_chars": 4}
    assert "secret-app" not in friend.get("/api/tokens").text
    assert "secret-app" in client.get("/api/tokens").text  # the owner still gets everything


def test_the_cockpit_never_fetches_and_the_engine_never_loads_from_another_site():
    for f in ("telemetry.js", "cockpit.js"):
        src = (STATIC / f).read_text(encoding="utf-8")
        assert not re.search(r"\bfetch\(|XMLHttpRequest|https?://(?!www\.w3\.org/2000/svg)", src), f


def test_the_cockpit_switch_and_the_message_button_sit_in_the_overview_header():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    head = page[page.index('<header class="page-head">'):page.index("</header>", page.index('<header class="page-head">'))]
    assert 'id="ckMode" type="button" aria-pressed="true"' in head  # a real button, keyboard reachable, with its state
    assert 'id="announceOpen" type="button" hidden aria-expanded="false" aria-controls="announceCard"' in head
    css = (STATIC / "telemetry.css").read_text(encoding="utf-8")
    assert ".ck.classic .tw.tw-min { display: flex; }" in css  # off: every window shows, so none leaves a gap
    js = (STATIC / "cockpit.js").read_text(encoding="utf-8")
    assert "T.store.set('cp-cockpit'" in js and "T.store.get('cp-cockpit')" in js  # remembered (try/catch inside store)