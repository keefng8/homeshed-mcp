"""strapi.check, code.dead_scan and code.webhook_retry_check and readroots.py,
the folder guard they share. Case list drafted by the local model; every case runs on a small fixture project in a
temp folder. Nothing touches the network: the /admin probe is replaced."""
import os
from pathlib import Path

import pytest

import readroots
from tools.code import dead_scan as dead_mod
from tools.code import webhook_retry_check as hook_mod
from tools.strapi import check as strapi_mod

strapi_check = getattr(strapi_mod.check, "__wrapped__", strapi_mod.check)
dead_scan = getattr(dead_mod.dead_scan, "__wrapped__", dead_mod.dead_scan)
webhook_check = getattr(hook_mod.webhook_retry_check, "__wrapped__", hook_mod.webhook_retry_check)


def write(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "allowed"
    r.mkdir()
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(r))
    return r


def ids(out, ok=None):
    return [c["id"] for c in out["checks"] if ok is None or c["ok"] is ok]


# --- readroots -------------------------------------------------------------------------------------------------
def test_a_folder_inside_the_root_is_allowed_and_relative_paths_start_there(root):
    (root / "proj").mkdir()
    assert readroots.resolve_folder(str(root / "proj")) == (root / "proj").resolve()
    assert readroots.resolve_folder("proj") == (root / "proj").resolve()


def test_outside_the_root_a_file_or_nothing_is_refused(root, tmp_path):
    (tmp_path / "secret").mkdir()
    write(root, "file.txt", "x")
    for bad in (str(tmp_path / "secret"), "..", str(root / "file.txt"), ""):
        with pytest.raises(readroots.OutsideRoots):
            readroots.resolve_folder(bad)


def test_a_symlink_out_of_the_root_is_refused(root, tmp_path):
    (tmp_path / "outside").mkdir()
    try:
        os.symlink(tmp_path / "outside", root / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights on this machine")
    with pytest.raises(readroots.OutsideRoots):
        readroots.resolve_folder(str(root / "link"))


def test_without_the_setting_the_project_dir_is_the_root(tmp_path, monkeypatch):
    monkeypatch.delenv("READ_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert readroots.allowed_roots() == [tmp_path.resolve()]


# --- strapi.check ----------------------------------------------------------------------------------------------
@pytest.fixture
def strapi(root):
    write(root, "src/api/article/content-types/article/schema.json",
          '{"info": {"singularName": "article"}, "attributes": {"title": {}, "author": {}}}')
    return root


def test_a_clean_project_passes_and_skips_what_it_cant_check(strapi):
    write(strapi, "src/lib/good.js", "fetch('/api/articles?pagination[pageSize]=100'); const t = item.title;\n"
                                     "const q = {filters: {author: {id: 1}}};")
    out = strapi_check(str(strapi))
    assert out["failed"] == 0
    skipped = {c["id"] for c in out["checks"] if c.get("skipped")}
    assert skipped == {"node-env", "admin-exposed"}


def test_each_source_bug_is_found_with_file_and_line(strapi):
    write(strapi, "src/lib/bad.js", "\n".join([
        "fetch('/api/articles')",                       # KB-0019
        "const t = data.attributes.title",              # KB-0020
        "get('/api/x?filters[writer][$eq]=1')",         # KB-0021 (writer isn't in the schema)
        "const ok = 'filters[documentId]'",             # a system field: fine
    ]))
    write(strapi, "src/api/article/routes/custom.js", "module.exports = {routes: [{path: '/api/articles/feed'}]}")
    out = strapi_check(str(strapi))
    bad = {c["id"]: c for c in out["checks"] if not c["ok"]}
    assert set(bad) == {"missing-pagination", "v4-attributes", "filter-path", "route-api-prefix"}
    assert bad["missing-pagination"]["file"].replace("\\", "/") == "src/lib/bad.js" and bad["missing-pagination"]["line"] == 1
    assert bad["v4-attributes"]["line"] == 2 and "'writer'" in bad["filter-path"]["detail"]
    assert bad["missing-pagination"]["kb"] == "KB-0019" and out["failed"] == 4


def test_node_env_counts_only_in_the_runtime_stage(strapi):
    write(strapi, "Dockerfile", "FROM node AS build\nENV NODE_ENV=development\nFROM node\nENV NODE_ENV=production\n")
    assert "node-env" in ids(strapi_check(str(strapi)), ok=True)
    write(strapi, "Dockerfile", "FROM node AS build\nFROM node\nENV NODE_ENV=development\n")
    row = next(c for c in strapi_check(str(strapi))["checks"] if c["id"] == "node-env")
    assert row["ok"] is False and row["line"] == 3


@pytest.mark.parametrize("status, ok", [(200, False), (401, True), (302, True), (403, True)])
def test_admin_probe_only_2xx_is_exposed(strapi, monkeypatch, status, ok):
    monkeypatch.setattr(strapi_mod, "_probe", lambda url, timeout=8.0: status)
    row = next(c for c in strapi_check(str(strapi), url="https://cms.example.com")["checks"] if c["id"] == "admin-exposed")
    assert row["ok"] is ok and str(status) in row["detail"]


def test_admin_probe_goes_through_netguard(strapi, monkeypatch):
    def refuse(host):
        raise strapi_mod.netguard.BlockedHost(f"{host} is not a public address")
    monkeypatch.setattr(strapi_mod.netguard, "resolve_permitted", refuse)
    row = next(c for c in strapi_check(str(strapi), url="http://192.168.1.30:1337")["checks"] if c["id"] == "admin-exposed")
    assert row["ok"] is True and row.get("skipped") and "not checked" in row["detail"]


@pytest.mark.parametrize("url", ["ftp://example.com", "https://user:pw@example.com", "example.com"])
def test_a_bad_url_is_refused(strapi, url):
    with pytest.raises(strapi_mod.StrapiCheckError):
        strapi_check(str(strapi), url=url)


def test_a_repo_outside_the_roots_is_refused(root, tmp_path):
    with pytest.raises(strapi_mod.StrapiCheckError):
        strapi_check(str(tmp_path))


# --- code.dead_scan --------------------------------------------------------------------------------------------
@pytest.fixture
def pyproj(root):
    write(root, "main.py", "import pkg.used\nfrom pkg import helper\n")
    write(root, "pkg/__init__.py", "from .reexported import Thing\n")
    write(root, "pkg/used.py", "X = 1\n")
    write(root, "pkg/helper.py", "Y = 2\n")
    write(root, "pkg/reexported.py", "class Thing: pass\n")
    write(root, "old/dead.py", "Z = 3\n")
    write(root, ".venv/lib/skip.py", "")
    return root


def test_dead_files_and_unused_reexports_are_found(pyproj):
    out = dead_scan(str(pyproj), ["main.py"], detail="full")
    dead = [c["file"].replace("\\", "/") for c in out["checks"] if c["id"] == "dead-file"]
    assert dead == ["old/dead.py"]  # .venv skipped; pkg/__init__ reached as a parent package
    reexp = next(c for c in out["checks"] if c["id"] == "unused-reexport")
    assert "'Thing'" in reexp["detail"] and reexp["kb"] == "KB-0030"
    assert out["unsure"] is False and out["failed"] == 2


def test_a_dynamic_import_makes_the_result_unsure(pyproj):
    write(pyproj, "main.py", "import importlib\nimport pkg.used\nimportlib.import_module(name)\n")
    out = dead_scan(str(pyproj), ["main.py"])
    assert out["unsure"] is True and "unreached-file" in ids(out) and "dead-file" not in ids(out)
    site = next(c for c in out["checks"] if c["id"] == "cant-follow")
    assert site["ok"] is True and "<computed>" in site["detail"]


def test_bad_entries_are_refused(pyproj, tmp_path):
    write(tmp_path, "outside.py", "")
    for entries in ([], ["missing.py"], ["../outside.py"], ["pkg"]):
        with pytest.raises(dead_mod.DeadScanError):
            dead_scan(str(pyproj), entries)


def test_a_file_that_doesnt_parse_is_noted_not_fatal(pyproj):
    write(pyproj, "pkg/used.py", "def broken(:\n")
    out = dead_scan(str(pyproj), ["main.py"])
    assert any(c["id"] == "syntax-error" and c["ok"] for c in out["checks"])


# --- code.webhook_retry_check ----------------------------------------------------------------------------------
SWALLOW = "module.exports = {\n  async paypalWebhook(ctx) {\n    try { await go(); } catch (e) { console.log(e); }\n  },\n};\n"


@pytest.mark.parametrize("catch_body, ok", [
    ("console.log(e);", False),
    ("ctx.status = 500;", True),
    ("ctx.status = 200;", False),
    ("ctx.throw(500, 'failed');", True),
    ("throw e;", True),
    ("return res.status(503).send();", True),
])
def test_a_catch_must_signal_failure(root, catch_body, ok):
    write(root, "src/hooks.js", SWALLOW.replace("console.log(e);", catch_body))
    out = webhook_check(str(root))
    assert out["handlers"] == 1 and out["checks"][0]["ok"] is ok
    if not ok:
        assert out["checks"][0]["id"] == "swallowed-error" and out["checks"][0]["line"] == 3


def test_no_try_catch_is_information_only(root):
    write(root, "src/hooks.js", "const stripeWebhook = async (req, res) => { await go(); res.send('ok'); };\n")
    out = webhook_check(str(root))
    assert out["failed"] == 0 and out["checks"][0]["id"] == "no-try-catch" and out["checks"][0]["info"]


def test_the_handler_pattern_picks_which_functions(root):
    write(root, "src/hooks.js", SWALLOW.replace("paypalWebhook", "onPayment"))
    assert webhook_check(str(root))["handlers"] == 0
    assert webhook_check(str(root), handler_pattern="payment")["failed"] == 1
    write(root, "node_modules/lib/x.js", SWALLOW)
    assert webhook_check(str(root), handler_pattern="payment")["handlers"] == 1  # node_modules skipped
    with pytest.raises(hook_mod.WebhookCheckError):
        webhook_check(str(root), handler_pattern="(")


# --- compact output (findings.py): a live dead_scan returned 87 rows, ~10K tokens ---------------------------------
def test_compact_groups_unreached_files_by_folder_and_caps_rows(root):
    write(root, "main.py", "import importlib\nimportlib.import_module(x)\n")
    for folder in range(25):
        for n in range(3):
            write(root, f"pkg{folder:02}/m{n}.py", "")
    out = dead_scan(str(root), ["main.py"])
    rows = [c for c in out["checks"] if not c["ok"]]
    assert len(rows) == 20 and rows[0]["files"] == 3 and len(rows[0]["examples"]) == 3
    assert out["counts"]["unreached-file"] == 75 and out["failed"] == 75 and "5 more failing rows" in out["more"]
    full = dead_scan(str(root), ["main.py"], detail="full")
    assert sum(1 for c in full["checks"] if not c["ok"]) == 75 and "more" not in full


def test_compact_keeps_small_results_whole_and_lists_what_passed(strapi):
    write(strapi, "src/lib/bad.js", "fetch('/api/articles')")
    out = strapi_check(str(strapi))
    assert out["failed"] == 1 and "v4-attributes" in out["passed"] and "missing-pagination" not in out["passed"]
    assert [c["id"] for c in out["checks"] if not c["ok"]] == ["missing-pagination"]


@pytest.mark.parametrize("call", [
    lambda r: strapi_check(str(r), detail="everything"),
    lambda r: dead_scan(str(r), ["main.py"], detail="verbose"),
    lambda r: webhook_check(str(r), detail=""),
])
def test_detail_must_be_compact_or_full(root, call):
    write(root, "main.py", "")
    with pytest.raises(ValueError):
        call(root)
