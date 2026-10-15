"""Contract tests: the fields our code reads must exist, with the same type, in each live service's real answer
(tests/fixtures/contracts, shapes captured by scripts/capture_contracts.py) AND in the fake the unit tests use.
Written after a fake hid a real bug: whoami's proof sits inside "data" on the live site, while the fake put it at the
top level, so every member would have shown "not Pro" (2026-10-15). If a test here fails, either the service changed
(re-capture, then fix the code) or a fake drifted from reality (fix the fake)."""
import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "contracts"

# What each integration's code reads: a path (keys, "[]" for a list's items) and the type it needs there.
READS = {
    "pro_whoami": [("data.proof", "str"), ("data.client", "str")],
    "pro_latest": [(f"data.{k}", "str") for k in ("version", "sha256", "url", "sig", "kid", "record_sha256",
                                                    "requires_homeshed")] + [("data.size", "int")],
    "rtk_daily": [("available", "bool"), ("daily.[].date", "str"), ("daily.[].saved_tokens", "int"),
                  ("daily.[].commands", "int"), ("daily.[].savings_pct", "float")],
    "searxng_search": [("results.[].url", "str"), ("results.[].title", "str"), ("results.[].content", "str"),
                       ("results.[].engine", "str"), ("suggestions", "list")],
}


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return {bool: "bool", int: "int", float: "float", str: "str", type(None): "null"}.get(type(value), "other")


def at(tree, path: str):
    """The shape at a dotted path, or a KeyError naming where it stopped."""
    node = tree
    for part in path.split("."):
        if part == "[]":
            if not isinstance(node, list) or not node:
                raise KeyError(f"{path}: not a non-empty list")
            node = node[0]
        else:
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"{path}: no {part!r}")
            node = node[part]
    return "list" if isinstance(node, list) else ("dict" if isinstance(node, dict) else node)


def live(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["shape"]


@pytest.mark.parametrize("name", sorted(READS))
def test_the_live_answer_has_every_field_the_code_reads(name):
    tree = live(name)
    for path, kind in READS[name]:
        assert at(tree, path) == kind, f"{name}: live {path} is {at(tree, path)}, the code expects {kind}"


# --- the fakes the unit tests use: each one built exactly as its test builds it -----------------------------------
def _fake_whoami(monkeypatch):
    import mavis_pro
    import test_mavis_pro as t
    t.backend(monkeypatch)
    return mavis_pro.httpx.get("https://pro.test/api/pro-api/whoami",
                               headers={"Authorization": f"Bearer {t.KEY}"}).json()


def _fake_latest(monkeypatch):
    import test_pro_plugin as t
    return t.Site(t.wheel()).get("https://pro.example/api/pro-api/packages/homeshed-pro/latest").json()


def _fake_rtk(monkeypatch):
    import test_system_savings as t
    return t.DAILY


def _fake_searxng(monkeypatch):
    import test_web_search as t
    get = t.answer([{"title": "A", "url": "https://a.example/", "content": "c", "engine": "e"}], suggestions=["s"])
    return get("http://searxng-test:8080/search").json()


FAKES = {"pro_whoami": _fake_whoami, "pro_latest": _fake_latest, "rtk_daily": _fake_rtk,
         "searxng_search": _fake_searxng}


@pytest.mark.parametrize("name", sorted(READS))
def test_each_fake_matches_the_live_shape_where_the_code_reads(name, monkeypatch):
    fake, tree = shape(FAKES[name](monkeypatch)), live(name)
    for path, _kind in READS[name]:
        assert at(fake, path) == at(tree, path), f"{name}: the fake's {path} is {at(fake, path)}, live is {at(tree, path)}"


def test_the_capture_script_writes_shapes_never_values():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cc", Path(__file__).parent.parent / "scripts" / "capture_contracts.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    out = cc.shape({"data": {"proof": "kid.secretbody.sig", "client": "Owner's desk PC", "n": 3, "items": [{"a": 1.5}]}})
    assert out == {"data": {"client": "str", "items": [{"a": "float"}], "n": "int", "proof": "str"}}
    assert "secret" not in json.dumps(out) and "desk" not in json.dumps(out)


def test_httpx_is_the_real_one_after_the_fakes():
    assert hasattr(httpx, "get")  # monkeypatch undoes each fake's patching between tests
