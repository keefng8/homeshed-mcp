"""The front door (frontdoor.py, tools/frontdoor/front.py, server.py's tools.call): what a front-door app sees, that
tools.call runs the inner tool with the same checks as a direct call, trimming, stored results, and pictures as image
blocks. R&D's review points (27 Oct) each have a test here. Files go under tmp_path."""
import asyncio
import importlib
import json
import sys
from types import SimpleNamespace

import pytest

import clients
import frontdoor


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(clients, "_cache", (None, {"clients": {}}))
    for name in ("_windows", "_last_seen", "_touched", "_connections"):
        monkeypatch.setattr(clients, name, {})
    monkeypatch.setattr(frontdoor, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(frontdoor, "toolset_filter", None)
    return tmp_path


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    srv = importlib.import_module("server")
    pic = {"job_id": "abc", "status": "done", "image_b64": "QUJD", "image_mime": "image/jpeg"}
    srv.mcp.add_tool(lambda: dict(pic), name="test.picture", description="a picture")
    srv.mcp.add_tool(lambda: {"items": [{"name": f"n{i}", "big": "x" * 50, "id": i} for i in range(30)], "count": 30},
                     name="test.list", description="a list")
    srv.mcp.add_tool(lambda: {"queued": True, "request_id": "r1", "status": "waiting for the owner", "summary": "s"},
                     name="test.queue", description="queues")
    return srv


def _ctx(auth):
    return SimpleNamespace(request=SimpleNamespace(headers={"authorization": auth}, client=None))


def _app(name="app1", **view):
    token = clients.create(name, preset="none")
    clients.grant(name, "test.picture", "read", "test")
    clients.grant(name, "test.list", "read", "test")
    clients.grant(name, "test.queue", "read", "test")
    if view:
        clients.set_view(name, **view)
    return _ctx(f"Bearer {token}")


def _call(server, ctx, tool, **arguments):
    return asyncio.run(server.mcp._handle_call_tool(ctx, SimpleNamespace(name=tool, arguments=arguments)))


def _json(result):
    return json.loads(result.content[0].text)


# --- what an app sees -------------------------------------------------------------------------------------------------

def test_a_new_app_starts_on_the_front_door_and_an_old_one_keeps_the_full_list(server):
    ctx = _app()
    listed = {t.name for t in asyncio.run(server.mcp._handle_list_tools(ctx, None)).tools}
    assert listed == set(frontdoor.FRONT_TOOLS)
    assert clients.list_clients()["clients"][0]["tool_view"] == "front_door"
    data = clients._editable()
    for key in ("tool_view", "images"):
        data["clients"]["app1"].pop(key)  # an app made before 27 Oct has neither key
    clients._save(data)
    listed = {t.name for t in asyncio.run(server.mcp._handle_list_tools(ctx, None)).tools}
    assert "test.picture" in listed and frontdoor.view("app1") == "full" and frontdoor.image_mode("app1") == "inline"
    assert frontdoor.view(None) == "full" and frontdoor.image_mode(None) == "inline"  # the owner's sessions


def test_front_door_tools_need_no_grant_but_an_explicit_deny_wins(server):
    _app()
    assert clients.is_allowed("app1", "tools.find", "read", "tools")
    clients.revoke("app1", "tools.call", "read", "tools")
    assert not clients.is_allowed("app1", "tools.call", "read", "tools")


def test_set_view_checks_its_values():
    clients.create("app1")
    with pytest.raises(clients.ClientError):
        clients.set_view("app1", tool_view="everything")
    with pytest.raises(clients.ClientError):
        clients.set_view("app1", images="png")


# --- tools.call -------------------------------------------------------------------------------------------------------

def test_tools_call_runs_a_granted_tool_and_refuses_an_ungranted_one(server):
    ctx = _app(images="inline")
    out = _call(server, ctx, "tools.call", name="test.picture")
    assert not out.is_error and _json(out)["image_b64"] == "QUJD"
    refused = _call(server, ctx, "tools.call", name="docker.container.restart")
    assert refused.is_error and "not granted" in refused.content[0].text


def test_tools_call_refuses_nesting_and_bad_inputs(server):
    ctx = _app()
    assert "directly" in _call(server, ctx, "tools.call", name="tools.find").content[0].text
    assert _call(server, ctx, "tools.call", name="test.list", arguments=[1]).is_error
    assert _call(server, ctx, "tools.call", name="test.list", fields="name").is_error
    assert "no input named" in _call(server, ctx, "tools.call", name="test.list", limit=3).content[0].text


def test_usage_counts_the_inner_tool_and_tools_call_apart(server, monkeypatch):
    import usage
    seen = []
    monkeypatch.setattr(usage, "record", lambda tool, *a, **k: seen.append(tool))
    ctx = _app()
    _call(server, ctx, "tools.call", name="test.list")
    assert "tools.call" in seen
    assert clients.current_tool.get() is None  # the inner call's context was reset


def test_fields_trim_and_say_what_went(server):
    ctx = _app()
    out = _json(_call(server, ctx, "tools.call", name="test.list", fields=["items[].name", "nope"]))
    assert out["items"][0] == {"name": "n0"} and len(out["items"]) == 30
    assert out["_dropped"] > 0 and out["_missing"] == ["nope"]


def test_a_queued_reply_is_never_trimmed(server):
    ctx = _app()
    out = _json(_call(server, ctx, "tools.call", name="test.queue", fields=["summary"]))
    assert out["request_id"] == "r1" and out["queued"] is True and "_dropped" not in out


def test_tools_call_has_no_output_schema(server):
    info = server.mcp._tool_manager.get_tool("tools.call")
    assert not info.output_schema


# --- stored results ---------------------------------------------------------------------------------------------------

def test_to_file_stores_and_tools_result_pages_it_for_that_app_only(server):
    ctx = _app()
    handle = _json(_call(server, ctx, "tools.call", name="test.list", to_file=True))
    assert handle["result_id"].startswith("r_") and handle["shape"]["items"] == "list of 30"
    tok = clients.current_client.set("app1")
    try:
        page = frontdoor.read(handle["result_id"], "app1", ["name"], "", 25, 10)
    finally:
        clients.current_client.reset(tok)
    assert page["total"] == 30 and page["next_offset"] is None and page["items"][0] == {"name": "n25", "id": 25}
    assert frontdoor.read(handle["result_id"], "app1", query="n29")["items"][0]["name"] == "n29"
    _app("app2")
    with pytest.raises(frontdoor.FrontDoorError):
        frontdoor.read(handle["result_id"], "app2")


def test_the_oldest_stored_result_goes_at_the_cap_and_the_reply_says_so(monkeypatch):
    monkeypatch.setattr(frontdoor, "RESULTS_PER_CLIENT", 2)
    a = frontdoor.store({"v": 1}, "app1", "t")
    frontdoor.store({"v": 2}, "app1", "t")
    third = frontdoor.store({"v": 3}, "app1", "t")
    assert "evicted" in third
    with pytest.raises(frontdoor.FrontDoorError):
        frontdoor.read(a["result_id"], "app1")


def test_stored_results_have_key_shaped_secrets_hidden_but_ids_kept():
    sha = "a" * 64
    h = frontdoor.store({"token": "hlc_" + "x" * 30, "line": "API_KEY=supersecret1", "sha256": sha}, "app1", "t")
    back = frontdoor.read(h["result_id"], "app1")["result"]
    assert back["token"] == "[hidden]" and "supersecret1" not in back["line"] and back["sha256"] == sha


# --- trimming ---------------------------------------------------------------------------------------------------------

def test_trim_paths():
    value = {"a": {"b": 1, "c": 2}, "items": [{"x": 1, "y": 2}, {"x": 3}], "id": "k"}
    out = frontdoor.trim(value, ["a.b", "items[].x"])
    assert out["a"] == {"b": 1} and out["items"] == [{"x": 1}, {"x": 3}] and out["id"] == "k"
    with pytest.raises(frontdoor.FrontDoorError):
        frontdoor.trim(value, ["a..b"])


# --- pictures ---------------------------------------------------------------------------------------------------------

def test_content_images_go_out_as_image_blocks(server):
    ctx = _app()  # new apps: images as content
    out = _call(server, ctx, "test.picture")
    assert [c.type for c in out.content] == ["text", "image"]
    assert out.content[1].data == "QUJD" and out.content[1].mime_type == "image/jpeg"
    body = _json(out)
    assert "image_b64" not in body and body["image"] == "sent as an image block" and body["status"] == "done"
    via = _call(server, ctx, "tools.call", name="test.picture")
    assert [c.type for c in via.content] == ["text", "image"]


def test_inline_apps_and_the_owner_keep_base64(server):
    assert _json(_call(server, _app(images="inline"), "test.picture"))["image_b64"] == "QUJD"
    assert _json(_call(server, _ctx("Bearer owner-secret"), "test.picture"))["image_b64"] == "QUJD"


def test_a_list_of_pictures_is_capped():
    value = {"items": [{"image_b64": f"I{i}", "n": i} for i in range(6)]}
    out, images = frontdoor.take_images(value)
    assert len(images) == frontdoor.MAX_IMAGES and out["_images_left_out"] == 2
    assert all("image_b64" not in i for i in out["items"])


def test_a_tool_with_an_output_schema_is_left_alone(server):
    info = server.mcp._tool_manager.get_tool("test.picture")
    info.output_schema = {"type": "object", "required": ["image_b64"]}
    out = _call(server, _app(), "test.picture")
    assert _json(out)["image_b64"] == "QUJD"


# --- find / groups / describe -----------------------------------------------------------------------------------------

def test_find_ranks_granted_tools_and_hides_the_rest(server, monkeypatch):
    from tools.frontdoor import front
    clients.create("app1", preset="read")
    tok = clients.current_client.set("app1")
    try:
        hits = front.find("list my etsy orders")["tools"]
        assert hits and hits[0]["id"].startswith("etsy.order")
        assert all(h["risk"] == "read" for h in hits)
        assert not front.find("restart a docker container")["tools"] or all(
            h["id"] != "docker.container.restart" for h in front.find("restart a docker container")["tools"])
        assert all(h["id"] not in frontdoor.FRONT_TOOLS for h in front.find("tools find call")["tools"])
        groups = {g["group"] for g in front.groups()["groups"]}
        assert "etsy" in groups  # tools.* shows only Pro's tools.savings, never the five
        with pytest.raises(ValueError):
            front.describe("docker.container.restart")
    finally:
        clients.current_client.reset(tok)


def test_describe_matches_the_tool_list(server):
    from tools.frontdoor import front
    listed = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    d = front.describe("etsy.orders.list")
    assert d["inputSchema"] == listed["etsy.orders.list"].input_schema
    assert d["description"] == listed["etsy.orders.list"].description


def test_a_pro_tool_set_narrows_find_and_a_broken_one_never_hides_everything(server, monkeypatch):
    from tools.frontdoor import front
    monkeypatch.setattr(frontdoor, "toolset_filter", lambda client, tid: tid.startswith("etsy."))
    assert {g["group"] for g in front.groups()["groups"]} == {"etsy"}

    def broken(client, tid):
        raise RuntimeError("boom")
    monkeypatch.setattr(frontdoor, "toolset_filter", broken)
    assert len(front.groups()["groups"]) > 1
