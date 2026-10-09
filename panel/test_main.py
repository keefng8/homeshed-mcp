"""Tests for the dashboard backend (main.py). First test coverage this project has had --
built fast under time pressure earlier without it (explicitly a minimal stopgap), closing that
gap now. Covers the pure logic with real complexity (metric regex parsing, session-id mapping)
and the auth middleware; deliberately does not test the simple async httpx-calling endpoints
(/api/capabilities etc.) against mocked responses -- would need mocking an async context manager
for marginal extra coverage over what's already exercised live via deploy-time verification each
redeploy. _mcp_call_tool (added 2026-09-23) is complex enough (multi-step protocol handshake,
SSE-vs-JSON branching, several error paths) to earn real coverage -- done with httpx.MockTransport
instead, which exercises the real httpx.AsyncClient code path against a fake transport rather than
patching methods, avoiding the awkwardness that ruled out testing the simpler endpoints.
"""
import base64
import json
import re
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolated_state_files(tmp_path, monkeypatch):
    """No test may touch the real state files. On Windows the container path /data/... is D:\\data\\...,
    and a test that changed settings once leaked into the next run (2026-09-28)."""
    import main

    monkeypatch.setattr(main, "PANEL_SETTINGS_FILE", tmp_path / "panel-settings.json")
    monkeypatch.setattr(main, "_panel_cache", {"sig": None, "data": {}})
    monkeypatch.setattr(main, "VIEWERS_FILE", tmp_path / "viewers.json")
    monkeypatch.setattr(main, "_viewers_cache", {"sig": None, "data": None})
    monkeypatch.setattr(main, "LIFETIME_FILE", tmp_path / "local_ai_lifetime.json")
    monkeypatch.setattr(main, "OWNER_FILE", tmp_path / "owner.json")
    monkeypatch.setattr(main, "_owner_cache", {"key": None, "data": None})
    monkeypatch.setattr(main, "REVOKED_FILE", tmp_path / "revoked-sessions.json")
    monkeypatch.setattr(main, "TODO_SEEN_FILE", tmp_path / "todo-replies-seen.json")
    monkeypatch.setattr(main, "SAVED_RUNS_FILE", tmp_path / "saved-runs.json")
    monkeypatch.setattr(main, "_revoked_cache", {"key": None, "data": {}})
    monkeypatch.setattr(main, "SESSION_SECRET_FILE", tmp_path / "session-secret")
    monkeypatch.setattr(main, "_server_secret_cache", {"value": None})
    monkeypatch.setattr(main, "_failures", {})       # the lockout counters: per address, and every address together
    monkeypatch.setattr(main, "_all_failures", [])
    # A set-up install unless a test says otherwise: since 2026-10-01 these have no personal defaults, and empty means
    # "not set up" (release gate B)
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu.test")
    monkeypatch.setattr(main, "NTFY_HEALTH_URL", "http://ntfy.test/v1/health")


def _basic_auth_header(password: str) -> dict:
    token = base64.b64encode(f"anyuser:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# The owner, signed in the way scripts do (Basic auth: no cookie, so no CSRF check). There's no open mode since
# 2026-10-01 (release gate C7), so a test that reaches a page signs in.
OWNER_PW = "test-owner-pw"
OWNER = _basic_auth_header(OWNER_PW)


# ---------------------------------------------------------------------------
# _facts_session_id -- category-to-session-id mapping
# ---------------------------------------------------------------------------

def test_general_category_maps_to_bare_facts_session():
    import main

    assert main._facts_session_id("general") == "facts"


def test_other_categories_get_prefixed_session_id():
    import main

    assert main._facts_session_id("bugs-fixed") == "facts:bugs-fixed"
    assert main._facts_session_id("mcp-server") == "facts:mcp-server"


def test_memory_category_route_rejects_bad_names(monkeypatch):
    """Any category in use opens (an install's own, 2026-10-01); a name the tool server could never have written, or
    the category index itself, is refused before anything is asked."""
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    for bad in ("Not_A_Category", "-starts-with-a-dash", "x" * 41, "fact-index"):
        assert client.get(f"/api/memory/{bad}").status_code == 404


# ---------------------------------------------------------------------------
# _parse_metric -- llama.cpp's colon-separated metric names
# ---------------------------------------------------------------------------

def test_parse_metric_colon_separator():
    import main

    text = "llamacpp:prompt_tokens_total 412510\nother_metric 1"
    assert main._parse_metric(text, ["llamacpp:prompt_tokens_total"]) == 412510.0


def test_parse_metric_falls_back_through_name_list():
    """Real bug history: the underscore-separated name never matched anything on a real
    llama.cpp server (colon is the real separator) -- this fallback list exists specifically
    because that was discovered live, not assumed upfront."""
    import main

    text = "llamacpp_prompt_tokens_total 999"
    assert main._parse_metric(text, ["llamacpp:prompt_tokens_total", "llamacpp_prompt_tokens_total"]) == 999.0


def test_parse_metric_returns_none_when_absent():
    import main

    assert main._parse_metric("unrelated_metric 5", ["llamacpp:prompt_tokens_total"]) is None


def test_parse_metric_ignores_labels_on_unlabeled_metric():
    import main

    text = 'llamacpp:requests_processing{pid="123"} 2'
    assert main._parse_metric(text, ["llamacpp:requests_processing"]) == 2.0


# ---------------------------------------------------------------------------
# _parse_labeled_metric -- gateway metrics, repeated metric name per model
# ---------------------------------------------------------------------------

def test_parse_labeled_metric_picks_correct_model():
    import main

    text = (
        'litellm_input_tokens_metric_total{model="nvidia/nemotron-3-ultra-550b-a55b"} 100\n'
        'litellm_input_tokens_metric_total{model="nvidia/nemotron-3-super-120b-a12b"} 250\n'
    )
    result = main._parse_labeled_metric(
        text, "litellm_input_tokens_metric_total", "model", "nvidia/nemotron-3-super-120b-a12b"
    )
    assert result == 250.0


def test_parse_labeled_metric_different_label_key():
    """The gateway isn't fully consistent about label keys -- request-count metrics use
    litellm_model_name, token metrics use model. Both must work."""
    import main

    text = 'litellm_deployment_total_requests_total{litellm_model_name="nvidia/nemotron-3-ultra-550b-a55b"} 7\n'
    result = main._parse_labeled_metric(
        text, "litellm_deployment_total_requests_total", "litellm_model_name", "nvidia/nemotron-3-ultra-550b-a55b"
    )
    assert result == 7.0


def test_parse_labeled_metric_returns_none_when_model_absent():
    import main

    text = 'litellm_input_tokens_metric_total{model="some-other-model"} 100\n'
    result = main._parse_labeled_metric(text, "litellm_input_tokens_metric_total", "model", "nvidia/nemotron-3-ultra-550b-a55b")
    assert result is None


# ---------------------------------------------------------------------------
# _mcp_call_tool -- real MCP Streamable HTTP handshake (initialize -> tools/call)
# ---------------------------------------------------------------------------

def _mock_mcp_transport(tools_call_response: httpx.Response):
    """Build an httpx.MockTransport that answers the real 3-request handshake:
    initialize (returns a session id header), notifications/initialized (no
    response body needed), tools/call (the response under test).
    """
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        body = _json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "test-session-123"}, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/call":
            return tools_call_response
        raise AssertionError(f"unexpected method: {method}")

    return httpx.MockTransport(handler)


def _patch_mcp_client(monkeypatch, main, transport):
    real_async_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(main.httpx, "AsyncClient", fake_client)


def _rpc_result_response(result: dict) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 2, "result": result})


@pytest.mark.asyncio
async def test_mcp_call_tool_happy_path_json_response(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    result = {"content": [{"type": "text", "text": '{"features": [], "total": 0}'}]}
    _patch_mcp_client(monkeypatch, main, _mock_mcp_transport(_rpc_result_response(result)))

    out = await main._mcp_call_tool("web.read", {"limit": 5})

    assert out == {"features": [], "total": 0}


@pytest.mark.asyncio
async def test_mcp_call_tool_parses_sse_response(monkeypatch):
    """mcp-server's Streamable HTTP can answer with a single-event SSE stream instead of plain
    JSON -- confirmed live against the real deployment, not assumed."""
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    result = {"content": [{"type": "text", "text": '{"feature_id": 1}'}]}
    sse_body = 'event: message\ndata: {"jsonrpc": "2.0", "id": 2, "result": ' + \
        __import__("json").dumps(result) + '}\n\n'
    sse_response = httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse_body)
    _patch_mcp_client(monkeypatch, main, _mock_mcp_transport(sse_response))

    out = await main._mcp_call_tool("mavis_appstore.manifest", {"id": 1})

    assert out == {"feature_id": 1}


@pytest.mark.asyncio
async def test_mcp_call_tool_prefers_structured_content(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    # structuredContent present alongside content -- must win, content is ignored.
    result = {
        "content": [{"type": "text", "text": '{"id": 1}'}, {"type": "text", "text": '{"id": 2}'}],
        "structuredContent": {"result": [{"id": 1}, {"id": 2}]},
    }
    _patch_mcp_client(monkeypatch, main, _mock_mcp_transport(_rpc_result_response(result)))

    out = await main._mcp_call_tool("docker.container.list", {"all": True})

    assert out == {"result": [{"id": 1}, {"id": 2}]}


@pytest.mark.asyncio
async def test_mcp_call_tool_reconstructs_list_from_multiple_text_blocks(monkeypatch):
    """Real bug found live 2026-09-23: docker.container.list's Python return type is a top-level
    list, and this SDK version serializes that as one text content block PER ELEMENT with no
    structuredContent at all -- naively concatenating the blocks (the old behavior) produced
    invalid JSON ("Extra data") the moment there was more than one container. Confirmed against
    the real mcp-server deployment before fixing, not assumed."""
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    result = {
        "content": [
            {"type": "text", "text": '{"id": "abc", "name": "one"}'},
            {"type": "text", "text": '{"id": "def", "name": "two"}'},
        ],
    }
    _patch_mcp_client(monkeypatch, main, _mock_mcp_transport(_rpc_result_response(result)))

    out = await main._mcp_call_tool("docker.container.list", {"all": True})

    assert out == {"result": [{"id": "abc", "name": "one"}, {"id": "def", "name": "two"}]}


@pytest.mark.asyncio
async def test_mcp_call_tool_raises_on_capability_error(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    result = {"isError": True, "content": [{"type": "text", "text": "no feature with id 999999"}]}
    _patch_mcp_client(monkeypatch, main, _mock_mcp_transport(_rpc_result_response(result)))

    with pytest.raises(RuntimeError, match="no feature with id"):
        await main._mcp_call_tool("mavis_appstore.manifest", {"id": 999999})


@pytest.mark.asyncio
async def test_mcp_call_tool_raises_on_non_200_initialize(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="initialize returned http 401"):
        await main._mcp_call_tool("web.read", {})


@pytest.mark.asyncio
async def test_mcp_call_tool_raises_when_no_session_id_header(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})  # no mcp-session-id header

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="no mcp-session-id header"):
        await main._mcp_call_tool("web.read", {})


def test_monitors_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("could not read the Kuma database: boom")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.get("/api/monitors")

    assert resp.status_code == 502
    assert "could not read" in resp.json()["error"]


# ---------------------------------------------------------------------------
# /api/docker/* routes
# ---------------------------------------------------------------------------

def test_docker_containers_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("mcp-server unreachable: boom")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.get("/api/docker/containers")

    assert resp.status_code == 502
    assert "unreachable" in resp.json()["error"]


def test_docker_action_rejects_unknown_action(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/docker/containers/delete", json={"container": "foo"})

    assert resp.status_code == 404


def test_docker_action_requires_container(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/docker/containers/restart", json={})

    assert resp.status_code == 400


def test_docker_action_defaults_dry_run_true_and_calls_correct_capability(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"container": args["container"], "dry_run": args["dry_run"], "restarted": False}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/docker/containers/restart", json={"container": "dashboard-dashboard-1"})

    assert resp.status_code == 200
    assert calls == [("docker.container.restart", {"container": "dashboard-dashboard-1", "dry_run": True})]


def test_docker_action_passes_dry_run_false_when_explicitly_requested(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"container": args["container"], "dry_run": args["dry_run"], "started": True}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/docker/containers/start", json={"container": "ntfy-ntfy-1", "dry_run": False})

    assert resp.status_code == 200
    assert calls == [("docker.container.start", {"container": "ntfy-ntfy-1", "dry_run": False})]


def test_docker_action_forwards_restart_timeout(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    client.post("/api/docker/containers/restart", json={"container": "caddy", "dry_run": False, "timeout": 30})

    assert calls == [("docker.container.restart", {"container": "caddy", "dry_run": False, "timeout": 30})]


def test_docker_action_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("docker.errors.NotFound: no such container")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/docker/containers/stop", json={"container": "nope"})

    assert resp.status_code == 502
    assert "NotFound" in resp.json()["error"]


# ---------------------------------------------------------------------------
# /api/reasoning/* routes
# ---------------------------------------------------------------------------

def test_reasoning_solve_requires_smt_lib2(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/solve", json={})

    assert resp.status_code == 400


def test_reasoning_solve_forwards_args_and_returns_result(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"result": "sat", "model": {"x": "1"}, "timeout_ms": 5000}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/solve", json={"smt_lib2": "(check-sat)", "timeout_ms": 1000})

    assert resp.status_code == 200
    assert resp.json() == {"result": "sat", "model": {"x": "1"}, "timeout_ms": 5000}
    assert calls == [("reasoning.solve", {"smt_lib2": "(check-sat)", "timeout_ms": 1000})]


def test_reasoning_solve_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("invalid SMT-LIB2: parse error")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/solve", json={"smt_lib2": "bad"})

    assert resp.status_code == 502
    assert "invalid SMT-LIB2" in resp.json()["error"]


def test_reasoning_decompose_requires_task(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/decompose", json={})

    assert resp.status_code == 400


def test_reasoning_decompose_forwards_args_and_returns_result(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"task": "do it", "subtasks": ["a", "b"], "model": "qwen-coder"}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/decompose", json={"task": "do it", "max_subtasks": 3})

    assert resp.status_code == 200
    assert resp.json()["subtasks"] == ["a", "b"]
    assert calls == [("reasoning.decompose_task", {"task": "do it", "max_subtasks": 3})]


def test_reasoning_decompose_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("local AI backend unavailable for decomposition: timed out")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/decompose", json={"task": "do it"})

    assert resp.status_code == 502
    assert "unavailable" in resp.json()["error"]


def test_reasoning_route_requires_text(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/route", json={})

    assert resp.status_code == 400


def test_reasoning_route_forwards_args_and_returns_result(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"recommendation": "claude", "confidence": "high", "score": 76, "reasoning": "..."}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/route", json={"text": "security audit", "token_estimate": 500, "tool_count": 3})

    assert resp.status_code == 200
    assert resp.json()["recommendation"] == "claude"
    assert calls == [("reasoning.route", {"text": "security audit", "token_estimate": 500, "tool_count": 3})]


def test_reasoning_route_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/route", json={"text": "x"})

    assert resp.status_code == 502


def test_reasoning_delegate_requires_task(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/delegate", json={})

    assert resp.status_code == 400


def test_reasoning_delegate_forwards_args_and_returns_result(monkeypatch):
    import main

    calls = []

    async def fake_call(name, args, **_):
        calls.append((name, args))
        return {"handled_by": "local", "answer": "Paris.", "model": "qwen-coder", "route": {}}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/delegate", json={"task": "capital of France"})

    assert resp.status_code == 200
    assert resp.json()["handled_by"] == "local"
    assert calls == [("reasoning.delegate", {"task": "capital of France"})]


def test_reasoning_delegate_route_returns_502_on_backend_error(monkeypatch):
    import main

    async def fake_call(name, args, **_):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)

    resp = client.post("/api/reasoning/delegate", json={"task": "x"})

    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def test_no_password_configured_allows_all_requests(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    # "/" (the static file mount) deliberately, not an /api/* route: those make real outbound
    # httpx calls to mcp-server/memory-core, which don't exist in a test environment and would
    # make every "should succeed" auth test slow on a real connect timeout for no reason -- the
    # auth middleware runs before the route handler either way, so this exercises the same code.
    resp = client.get("/")
    assert resp.status_code != 401


def test_wrong_password_rejected(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "correct-horse")
    client = TestClient(main.app)
    resp = client.get("/api/health", headers=_basic_auth_header("wrong-password"))
    assert resp.status_code == 401


def test_no_auth_header_rejected_when_password_set(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "correct-horse")
    client = TestClient(main.app)
    resp = client.get("/api/health")
    assert resp.status_code == 401


def test_correct_password_allowed(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "correct-horse")
    client = TestClient(main.app)
    resp = client.get("/", headers=_basic_auth_header("correct-horse"))
    assert resp.status_code != 401


def test_username_is_ignored_only_password_checked(monkeypatch):
    """Documented main.py behavior: split(':', 1) discards whatever username was sent --
    only the password half is checked. Any username works."""
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "correct-horse")
    client = TestClient(main.app)
    token = base64.b64encode(b"literally-anything:correct-horse").decode()
    resp = client.get("/", headers={"Authorization": f"Basic {token}"})
    assert resp.status_code != 401


def test_malformed_auth_header_rejected_not_crashed(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "correct-horse")
    client = TestClient(main.app)
    resp = client.get("/api/health", headers={"Authorization": "Basic not-valid-base64!!!"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Observations routes -- thin proxies to mcp-server observe.* (2026-09-25).
# Validate locally before any MCP call; the dashboard forces source="owner" on log.
# ---------------------------------------------------------------------------

def _obs_client(monkeypatch):
    import main

    calls = []

    async def fake_call(tool_name, arguments, timeout=15, **_):
        calls.append((tool_name, arguments))
        return {"ok": True}

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    return TestClient(main.app, headers=OWNER), calls


def test_observations_list_rejects_unknown_status(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    assert client.get("/api/observations?status=bogus").status_code == 400
    assert calls == []


def test_observations_list_passes_status(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    assert client.get("/api/observations?status=open").status_code == 200
    assert calls == [("observe.list", {"status": "open", "limit": 200})]


def test_observations_log_requires_title_and_issue(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    assert client.post("/api/observations", json={"title": "t", "issue": "  "}).status_code == 400
    assert calls == []


def test_observations_log_forces_source_owner(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    resp = client.post("/api/observations", json={"title": "t", "issue": "i", "rule": "R", "source": "claude"})
    assert resp.status_code == 200
    assert calls == [("observe.log", {"title": "t", "issue": "i", "source": "owner", "rule": "R"})]


def test_observations_update_validates_status(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    assert client.post("/api/observations/0001", json={"status": "all"}).status_code == 400
    assert client.post("/api/observations/0001", json={"status": "actioned", "resolution": "hook"}).status_code == 200
    assert calls == [("observe.update", {"id": "0001", "status": "actioned", "resolution": "hook"})]


# ---------------------------------------------------------------------------
# Image route -- proxies to mcp-server image.generate with include_image=True (2026-09-25).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Lifetime token totals survive backend restarts (llama.cpp counters reset to 0) -- 2026-09-25.
# ---------------------------------------------------------------------------

def test_lifetime_accumulates_across_counter_resets(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "LIFETIME_FILE", tmp_path / "life.json")
    monkeypatch.setattr(main, "_lifetime", {})
    main._accumulate_lifetime("m", 100, 10)   # first sight: whole counter counts
    main._accumulate_lifetime("m", 150, 15)   # +50 / +5
    main._accumulate_lifetime("m", 20, 2)     # restart: counter dropped -> +20 / +2
    main._accumulate_lifetime("m", None, 5)   # missing prompt metric ignored; +3 output
    assert main._lifetime["m"]["prompt"] == 170 and main._lifetime["m"]["output"] == 20
    assert '"prompt": 170' in (tmp_path / "life.json").read_text()


def test_observations_badge_route_does_not_call_a_capability(monkeypatch):
    client, calls = _obs_client(monkeypatch)
    client.get("/api/observations/summary")  # mcp-server unreachable in tests -> 502 is fine
    assert calls == []


# ---------------------------------------------------------------------------
# Login page + session cookie (replaced the HTTP Basic popup, 2026-09-25)
# ---------------------------------------------------------------------------

@pytest.fixture
def pw_client(monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "hunter2-test")  # secret-scan: allow (fake test password)
    monkeypatch.setattr(main, "_failures", {})
    return TestClient(main.app), main


def test_browser_page_redirects_to_login_without_popup(pw_client):
    client, _ = pw_client
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=/")
    assert "www-authenticate" not in {k.lower() for k in r.headers}


def test_api_gets_plain_401_no_basic_challenge(pw_client):
    client, _ = pw_client
    r = client.get("/api/health")
    assert r.status_code == 401 and "www-authenticate" not in {k.lower() for k in r.headers}


def test_login_page_is_public(pw_client):
    client, _ = pw_client
    r = client.get("/login")
    assert r.status_code == 200 and "Sign in" in r.text


def test_good_login_sets_httponly_cookie_and_grants_access(pw_client):
    client, _ = pw_client
    r = client.post("/login", json={"password": "hunter2-test"})  # secret-scan: allow (fake test password)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "cp_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/observations/summary").status_code != 401


def test_wrong_password_then_lockout(pw_client):
    client, _ = pw_client
    for _ in range(5):
        assert client.post("/login", json={"password": "nope"}).status_code == 401
    r = client.post("/login", json={"password": "hunter2-test"})  # even the right one is locked out  # secret-scan: allow (fake test password)
    assert r.status_code == 429


def test_a_stolen_cookie_cant_test_password_guesses_offline(pw_client):
    """R&D's security review, 2026-10-01: with the password in the .env, the owner cookie was signed with a key made
    from the password alone, so a stolen cookie let anyone try guesses offline. The key now mixes in this panel's own
    random secret (made once, kept), and a new password still signs everyone out."""
    import hashlib
    import hmac as hmac_mod
    _, main = pw_client
    cookie = main._make_session(1)
    expiry, sig = cookie.split(".")
    guess = hmac_mod.new(hashlib.sha256(b"session:hunter2-test").digest(), expiry.encode(), hashlib.sha256).hexdigest()  # secret-scan: allow (fake test password)
    assert sig != guess  # the right password alone no longer reproduces the signature
    assert main.SESSION_SECRET_FILE.read_text(encoding="ascii") == main._server_secret()  # made once, kept
    main._server_secret_cache["value"] = None
    assert main._session_identity(cookie)[0] == "owner"  # read back after a restart: sessions survive it
    main.DASHBOARD_PASSWORD = "a-new-password-entirely"  # secret-scan: allow (fake test password)
    assert main._session_identity(cookie)[0] is None  # a new password signs everyone out


def test_basic_auth_guesses_count_toward_the_same_lockout(pw_client):
    """R&D's security review, 2026-10-01 (confirmed high): Basic auth checked the password but never counted a wrong
    one, so a script could guess for ever while the sign-in page locked after 5."""
    client, main = pw_client
    basic = lambda pw: {"authorization": "Basic " + base64.b64encode(f"x:{pw}".encode()).decode()}  # noqa: E731
    for _ in range(5):
        assert client.get("/api/health", headers=basic("nope")).status_code == 401
    assert client.get("/api/health", headers=basic("hunter2-test")).status_code == 429  # secret-scan: allow (fake test password)
    assert client.post("/login", json={"password": "hunter2-test"}).status_code == 429  # one lockout for both  # secret-scan: allow (fake test password)
    main._failures.clear()
    main._all_failures.clear()
    assert client.get("/api/health", headers=basic("hunter2-test")).status_code != 401  # secret-scan: allow (fake test password)


def test_tampered_or_expired_session_rejected(pw_client):
    _, main = pw_client
    good = main._make_session(1)
    exp, sig = good.split(".")
    assert main._valid_session(good)
    assert not main._valid_session(f"{int(exp) + 999}.{sig}")   # tampered expiry
    assert not main._valid_session(main._make_session(-1))       # expired
    assert not main._valid_session("garbage")


def test_basic_auth_still_works_for_scripts(pw_client):
    client, _ = pw_client
    r = client.get("/api/observations/summary", headers=_basic_auth_header("hunter2-test"))  # secret-scan: allow (fake test password)
    assert r.status_code != 401


def test_logout_clears_cookie(pw_client):
    client, _ = pw_client
    r = client.get("/logout", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login"
    assert 'cp_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]


# ---------------------------------------------------------------------------
# Models tab (2026-09-26)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# A decision server (2026-09-28): polled like a model, but it counts decisions, not tokens
# ---------------------------------------------------------------------------

def _decider_only(monkeypatch, main):
    entry = {"name": "Decider", "label": "local", "type": "decider", "url": "http://mcp/decider/stats", "headers": {}}
    monkeypatch.setattr(main, "LOCAL_AI_SERVERS", [entry])
    monkeypatch.setattr(main, "_local_ai_history", {"Decider": []})


def test_a_decision_server_poll_records_decisions_not_tokens(monkeypatch):
    import asyncio

    import main
    _decider_only(monkeypatch, main)
    stats = {"status": "ok", "served": 12, "busy": 1, "avg_ms": 640, "recent": []}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=stats))

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await main._poll_local_ai_once(client)

    asyncio.run(run())
    sample = main._local_ai_history["Decider"][-1]
    assert sample["online"] and sample["requests_total"] == 12 and sample["requests_processing"] == 1
    assert sample["avg_ms"] == 640 and "total_tokens" not in sample

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    row = client.get("/api/local-ai").json()["servers"][0]
    assert row["name"] == "Decider" and row["sparkline"] == [12] and row["avg_ms"] == 640


def test_a_decision_server_poll_marks_offline_when_down(monkeypatch):
    import asyncio

    import main
    _decider_only(monkeypatch, main)
    transport = httpx.MockTransport(lambda req: httpx.Response(503, json={"status": "down"}))

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await main._poll_local_ai_once(client)

    asyncio.run(run())
    assert main._local_ai_history["Decider"][-1]["online"] is False


# ---------------------------------------------------------------------------
# Delegator explainability + feature health (2026-09-28)
# ---------------------------------------------------------------------------

def test_delegator_routes_proxy_to_mcp(monkeypatch):
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/reset"):
            return httpx.Response(200, json={"name": "qwen3-coder", "was_benched": True})
        return httpx.Response(200, json={"decisions": [{"chosen": "qwen3-coder"}], "bench_s": 60})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    assert client.get("/api/delegator/decisions").json()["decisions"][0]["chosen"] == "qwen3-coder"
    assert client.post("/api/delegator/qwen3-coder/reset").json()["was_benched"] is True
    assert client.post("/api/delegator/bad%20name/reset").status_code == 400
    assert ("POST", "/local-ai/backends/qwen3-coder/reset") in seen


def test_a_first_run_shows_what_isnt_set_up_in_grey_never_red(monkeypatch):
    """Release gate B (2026-10-01): nothing set up but HomeShed itself, and nothing is "down": each part is "off", with
    a sentence saying how to add it."""
    import main

    async def fake_health():
        return {"mcp_server": "ok", "memory_core": main.BUILT_IN}

    monkeypatch.setattr(main, "health", fake_health)
    for name, value in (("LOCAL_AI_SERVERS", []), ("_local_ai_history", {}), ("GPU_SERVICE_URL", ""),
                        ("NTFY_HEALTH_URL", ""), ("DASHBOARD_PASSWORD", OWNER_PW)):
        monkeypatch.setattr(main, name, value)
    feats = TestClient(main.app, headers=OWNER).get("/api/features").json()["features"]
    got = {f["label"]: f["status"] for f in feats}
    assert {k: got.pop(k) for k in ("Tools", "Ask", "Memory", "Alerts")} == {
        "Tools": "ok", "Ask": "off", "Memory": "ok", "Alerts": "off"}  # memory: HomeShed's own, built in
    assert set(got.values()) <= {"off"}  # the parts a setup adds of its own (private_routes.py) are off too
    assert all(f["detail"] and "down" not in f["detail"].lower() for f in feats)


def test_with_no_helper_pc_the_runner_and_rtk_say_not_set_up(monkeypatch):
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    runner = client.get("/api/todo/runner").json()
    assert runner["not_set_up"] is True and runner["on"] is False and "helper PC" in runner["why_not"]
    savings = client.get("/api/savings").json()
    assert savings["daily"] == {"available": False, "not_set_up": True} == savings["discover"]


def test_with_no_memory_core_memory_is_read_through_homeshed(monkeypatch):
    """The prepper's clean-VM test (2026-10-01): a new install's built-in memory looked missing, because the panel
    read memory-core only."""
    import main

    monkeypatch.setattr(main, "MEMORY_CORE_BASE_URL", "")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    seen = []

    def handler(req):
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path == "/memory/facts":
            got = [{"content": "a fact", "timestamp": "t"}] * (3 if req.url.params["category"] == "platform" else 0)
            return httpx.Response(200, json={"messages": got, "total": len(got)})
        return httpx.Response(200, json={})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    client = TestClient(main.app, headers=OWNER)
    cats = {c["id"]: c for c in client.get("/api/memory").json()["categories"]}
    assert cats["platform"]["count"] == 3 and cats["platform"]["latest"][0]["content"] == "a fact"
    assert cats["general"]["count"] == 0 and len(cats) == len(main.FACT_CATEGORIES)
    one = client.get("/api/memory/platform?limit=5").json()
    assert one["count"] == 3 and seen[-1] == ("/memory/facts", {"category": "platform", "limit": "5", "max_chars": "0"})
    assert client.get("/api/memory/projects").json() == {"projects": []}
    assert client.get("/api/health").json()["memory_core"] == main.BUILT_IN


def test_health_names_the_pages_version_so_an_old_tab_notices_a_redeploy(monkeypatch):
    """2026-10-01: a tab opened before a redeploy showed "Busy" through a whole compact until the owner refreshed."""
    import hashlib
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    page = (Path(main.__file__).parent / "static" / "index.html").read_bytes()
    got = TestClient(main.app, headers=OWNER).get("/api/health").json()["panel"]
    assert got == hashlib.sha256(page).hexdigest()[:12]
    html = page.decode("utf-8")
    assert 'id="updatePill"' in html and "function panelUpdated()" in html


def test_the_memory_card_counts_an_installs_own_categories(monkeypatch):
    """The prepper's clean-VM run (2026-10-01): facts in "decisions", "general" and "bugs-fixed" showed as 2, because
    "decisions" isn't one of the homelab's FACT_CATEGORIES. The store's fact index names the others."""
    import main

    monkeypatch.setattr(main, "MEMORY_CORE_BASE_URL", "")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    stored = {"decisions": 1, "general": 1, "bugs-fixed": 1}
    index = ["decisions", "bugs-fixed", "../etc", "fact-index", "decisions"]  # newest first; bad and repeated names

    def handler(req):
        cat = req.url.params["category"]
        got = ([{"content": n} for n in index] if cat == "fact-index"
               else [{"content": f"a {cat} fact", "timestamp": "t"}] * stored.get(cat, 0))
        return httpx.Response(200, json={"messages": got, "total": len(got)})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    client = TestClient(main.app, headers=OWNER)
    cats = client.get("/api/memory").json()["categories"]
    assert sum(c["count"] for c in cats) == 3
    ids = [c["id"] for c in cats]
    assert ids[:len(main.FACT_CATEGORIES)] == [c["id"] for c in main.FACT_CATEGORIES]  # the known ones first
    assert ids[len(main.FACT_CATEGORIES):] == ["decisions"]  # then the install's own, once; never the index itself
    assert next(c for c in cats if c["id"] == "decisions")["label"] == "Decisions"
    assert client.get("/api/memory/decisions").json()["count"] == 1  # its page opens too
    assert client.get("/api/memory/fact-index").status_code == 404
    assert client.get("/api/memory/Bad_Name").status_code == 404


def test_without_a_helper_pc_its_pages_say_not_set_up(monkeypatch):
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    for path in ("/api/context", "/api/claude/sessions", "/api/rules", "/api/projects"):
        r = client.get(path)
        assert r.status_code == 200 and r.json()["not_set_up"] is True, path  # not a failure: no failed request
    s = client.get("/api/settings").json()
    assert "main-pc" not in s["errors"] and all(it["source"] != "main-pc" for it in s["items"])


@pytest.mark.parametrize("path, message", [
    ("/api/docker/containers", "The Docker socket isn't mounted here, so the docker tools can't reach Docker: see docs."),
    ("/api/monitors", "Kuma database not found at /data/kuma/kuma.db -- is the read-only volume mounted?"),
])
def test_a_companion_that_isnt_connected_says_what_to_connect(monkeypatch, path, message):
    import main

    async def not_there(name, args, **_):
        raise RuntimeError(f"Error executing tool {name}: {message}")

    monkeypatch.setattr(main, "_mcp_call_tool", not_there)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    r = TestClient(main.app, headers=OWNER).get(path)
    assert r.status_code == 200 and r.json()["not_set_up"] is True and "Error executing" not in r.json()["error"]


def test_features_alerts_follow_ntfy_health(monkeypatch):
    import main

    async def fake_health():
        return {"mcp_server": "ok", "memory_core": "ok"}

    monkeypatch.setattr(main, "health", fake_health)
    monkeypatch.setattr(main, "LOCAL_AI_SERVERS", [])
    monkeypatch.setattr(main, "_local_ai_history", {})
    monkeypatch.setattr(main, "NTFY_HEALTH_URL", "http://ntfy.test/v1/health")
    real = httpx.AsyncClient
    healthy = {"value": True}

    def handler(req):
        if req.url.host == "ntfy.test":
            return httpx.Response(200, json={"healthy": healthy["value"]})
        return httpx.Response(200, json={})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    status = lambda: {f["label"]: f["status"] for f in client.get("/api/features").json()["features"]}["Alerts"]
    assert status() == "ok"
    healthy["value"] = False
    assert status() == "down"


def test_savings_route_combines_sources_and_survives_failures(monkeypatch):
    import main

    real = httpx.AsyncClient

    def handler(req):
        if req.url.path == "/stats/rtk/daily":
            return httpx.Response(200, json={"available": True, "daily": [{"date": "2026-09-28", "saved_tokens": 5}]})
        if req.url.path == "/stats/rtk/discover":
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"by_rule": {"generation": 10}})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    body = TestClient(main.app, headers=OWNER).get("/api/savings").json()
    assert body["daily"]["daily"][0]["saved_tokens"] == 5
    assert body["discover"] == {"available": False, "error": "GPU service unreachable"}
    assert body["estimate"] == {"by_rule": {"generation": 10}}


def test_tools_routes_proxy_and_validate(monkeypatch):
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.method == "POST":
            return httpx.Response(200, json={"id": "docker.container.stop", "enabled": False, "changed": True})
        return httpx.Response(200, json={"tools": [{"id": "memory.recall", "enabled": True}], "switched_off": []})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    assert client.get("/api/tools").json()["tools"][0]["id"] == "memory.recall"
    assert client.post("/api/tools/docker.container.stop/disable").json()["changed"] is True
    assert client.post("/api/tools/docker.container.stop/explode").status_code == 400
    assert client.post("/api/tools/BAD NAME/disable").status_code in (400, 404)
    assert ("POST", "/tools/docker.container.stop/disable") in seen


def test_gateway_metrics_fetched_once_per_cycle(monkeypatch):
    import asyncio

    import main
    servers = [{"name": "A", "type": "gateway", "url": "http://gw/metrics/", "model_label": "a"},
               {"name": "B", "type": "gateway", "url": "http://gw/metrics/", "model_label": "b"}]
    monkeypatch.setattr(main, "LOCAL_AI_SERVERS", servers)
    monkeypatch.setattr(main, "_local_ai_history", {"A": [], "B": []})
    hits = []

    def handler(req):
        hits.append(str(req.url))
        return httpx.Response(200, text="")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await main._poll_local_ai_once(client)

    asyncio.run(run())
    assert len(hits) == 1
    assert main._local_ai_history["A"][-1]["online"] and main._local_ai_history["B"][-1]["online"]


def test_client_admin_proxies_validate_and_forward(monkeypatch):
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path, req.content.decode() if req.content else ""))
        if req.url.path == "/clients" and req.method == "POST":
            return httpx.Response(200, json={"name": "my-app", "token": "hlc_x"})
        return httpx.Response(200, json={"ok": True, "clients": []})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/clients").status_code == 200
    r = c.post("/api/clients", json={"name": "my-app", "preset": "read", "extra": "dropped"})
    assert r.json()["token"] == "hlc_x" and "extra" not in seen[-1][2]
    assert c.post("/api/clients/my-app/rotate").status_code == 200
    assert c.post("/api/clients/my-app/limits", json={"rate_per_min": 30, "expires_days": None}).status_code == 200
    assert c.post("/api/clients/my-app/tools/memory.recall/grant").status_code == 200
    assert c.delete("/api/clients/my-app").status_code == 200
    assert c.post("/api/clients/my-app/explode").status_code == 400
    assert c.post("/api/clients/BAD/rotate").status_code == 400
    assert c.post("/api/clients/my-app/tools/memory.recall/steal").status_code == 400
    assert ("POST", "/clients/my-app/tools/memory.recall/grant", "") in seen
    for action in ("shared-read-off", "shared-read-on"):  # reading the shared memory, per app (2026-10-01)
        assert c.post(f"/api/clients/my-app/{action}").status_code == 200
        assert ("POST", f"/clients/my-app/{action}", "") in seen


def test_shared_memory_waiting_writes_are_the_owners_to_decide(monkeypatch):
    """An app's write to the shared memory waits for the owner (2026-10-01). The panel lists and decides them; ids and
    actions are checked before anything reaches the tool server, and a view-only login gets neither route."""
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path == "/memory/pending":
            return httpx.Response(200, json={"items": [{"id": "0a1b2c3d", "client": "my-app"}]})
        if req.url.path == "/memory/pending/0a1b2c3d/approve":
            return httpx.Response(200, json={"id": "0a1b2c3d", "approved": True})
        return httpx.Response(404, json={"error": "no write 9f9f9f9f is waiting (already decided?)"})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/shared-memory/waiting").json()["items"][0]["client"] == "my-app"
    assert c.post("/api/shared-memory/waiting/0a1b2c3d/approve").json()["approved"] is True
    r = c.post("/api/shared-memory/waiting/9f9f9f9f/decline")
    assert r.status_code == 404 and "already decided" in r.json()["error"]
    for bad in ("/api/shared-memory/waiting/../x/approve", "/api/shared-memory/waiting/0a1b2c3d/publish",
                "/api/shared-memory/waiting/0A1B2C3D/approve"):
        assert c.post(bad).status_code in (400, 404, 405)  # refused before the tool server, whichever way
    assert ("POST", "/memory/pending/0a1b2c3d/approve") in seen and len(seen) == 3
    assert "/api/shared-memory/waiting" not in main.VIEWER_ROUTES


def _approval_sources(monkeypatch, todo=None, memory=None, proxmox=None, printify=()):
    """The places /api/approvals reads; a value of None makes that source fail (printify: empty unless given)."""
    import main

    async def fake_tool(name, args, timeout=15, quiet=False):
        assert name == "todo.list" and quiet
        if todo is None:
            raise RuntimeError("tool server unreachable")
        return {"items": todo}

    async def fake_admin(method, path, body=None):
        got = {"/memory/pending": memory, "/proxmox/pending": proxmox,
               "/printify/pending": list(printify) if printify is not None else None}.get(path)
        return main.JSONResponse({"items": got} if got is not None else {"error": "down"},
                                 status_code=200 if got is not None else 502)
    monkeypatch.setattr(main, "_mcp_call_tool", fake_tool)
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    return TestClient(main.app, headers=OWNER), main


def test_approvals_gather_everything_waiting_for_the_owner(monkeypatch):
    """The owner, 2026-10-06 (to-do #51): one list of what waits for his yes, newest first: to-do tasks waiting for an
    OK (not the others), apps' shared-memory writes and queued Proxmox changes."""
    c, main = _approval_sources(
        monkeypatch,
        todo=[{"id": 57, "text": "Safety check", "assignee": "shop-bot", "waiting_on_ok": True},
              {"id": 2, "text": "Nightly check", "assignee": "my-app", "waiting_on_ok": False}],
        memory=[{"id": "0a1b2c3d", "client": "my-app", "content": "a fact", "at": 200.0}],
        proxmox=[{"id": "9f9f9f9f", "client": "shop-bot", "summary": "stop VM 115", "at": 300.0}])
    d = c.get("/api/approvals").json()
    assert d["count"] == 3 and d["unavailable"] == []
    assert [(i["kind"], i["id"], i["title"], i["from"]) for i in d["items"]] == [
        ("proxmox", "9f9f9f9f", "stop VM 115", "shop-bot"), ("memory", "0a1b2c3d", "a fact", "my-app"),
        ("todo", "57", "Safety check", "shop-bot")]
    assert "/api/approvals" not in main.VIEWER_ROUTES


def test_a_source_that_cant_be_read_is_named_and_the_rest_still_show(monkeypatch):
    c, _ = _approval_sources(monkeypatch, todo=None, memory=[], proxmox=None)
    d = c.get("/api/approvals").json()
    assert d["count"] == 0 and d["unavailable"] == ["to-do list", "Proxmox"]


def test_the_free_plan_without_a_todo_list_is_not_an_error(monkeypatch):
    import main
    c, _ = _approval_sources(monkeypatch, memory=[], proxmox=[])

    async def no_todo(name, args, timeout=15, quiet=False):
        raise RuntimeError("Unknown tool: todo.list")
    monkeypatch.setattr(main, "_mcp_call_tool", no_todo)
    assert c.get("/api/approvals").json()["unavailable"] == []


def test_connect_etsy_routes_pass_through_and_the_callback_lands_on_settings(monkeypatch):
    """The owner, 2026-10-06: a Connect Etsy button, public too. The tool server holds the tokens; the panel relays."""
    import main
    calls = []

    async def fake_admin(method, path, body=None):
        calls.append((method, path, body))
        if path == "/etsy/finish" and body.get("state") == "bad":
            return main.JSONResponse({"error": "press Connect again"}, status_code=400)
        return main.JSONResponse({"url": "https://www.etsy.com/oauth/connect?x=1"} if path == "/etsy/connect" and method == "POST"
                                 else {"connected": True, "shop_name": "Shed Prints"})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/etsy").json()["connected"] is True
    assert c.post("/api/etsy/connect", json={"redirect_uri": "https://p.example/api/etsy/callback"}).json()["url"].startswith("https://www.etsy.com/")
    assert c.post("/api/etsy/finish", json={"url": "https://x.example/?code=a&state=b"}).status_code == 200
    r = c.get("/api/etsy/callback", params={"code": "abc", "state": "s1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/#settings?etsy=ok&shop=Shed")
    r = c.get("/api/etsy/callback", params={"code": "abc", "state": "bad"}, follow_redirects=False)
    assert "etsy=failed" in r.headers["location"] and "Connect" in r.headers["location"]
    r = c.get("/api/etsy/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert "etsy=failed&why=access_denied" in r.headers["location"]
    assert ("POST", "/etsy/finish", {"code": "abc", "state": "s1"}) in calls
    assert not any(p.startswith("/api/etsy") for p in main.VIEWER_ROUTES)


def test_connect_threads_routes_and_callback(monkeypatch):
    """The owner, 2026-10-06: a Connect Threads card like Etsy's, for a posting agent."""
    import main
    calls = []

    async def fake_admin(method, path, body=None):
        calls.append((method, path, body))
        if path == "/threads/finish" and body.get("state") == "bad":
            return main.JSONResponse({"error": "press Connect again"}, status_code=400)
        return main.JSONResponse({"url": "https://threads.com/oauth/authorize?x=1"} if method == "POST" and path == "/threads/connect"
                                 else {"connected": True, "username": "myshop"})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/threads").json()["connected"] is True
    assert c.post("/api/threads/connect", json={"redirect_uri": "https://p.example/api/threads/callback"}).json()["url"].startswith("https://threads.com/")
    r = c.get("/api/threads/callback", params={"code": "abc#_", "state": "s1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#settings?threads=ok&account=myshop"
    r = c.get("/api/threads/callback", params={"code": "abc", "state": "bad"}, follow_redirects=False)
    assert "threads=failed" in r.headers["location"]
    assert not any(p.startswith("/api/threads") for p in main.VIEWER_ROUTES)


def test_agent_messages_list_reply_and_done_through_the_tool_server(monkeypatch):
    """The owner, 2026-10-06: agents leave him messages; he replies or marks them done from the panel."""
    import main
    calls = []

    async def fake_admin(method, path, body=None):
        calls.append((method, path, body))
        return main.JSONResponse({"items": [{"id": "0a0a0a0a", "sender": "R&D", "need": "info", "text": "hi"}]}
                                 if method == "GET" else {"id": "0a0a0a0a"})
    told = []

    async def fake_tell(resp, text, done):
        told.append((text, done))
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    monkeypatch.setattr(main, "_tell_sender", fake_tell)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/inbox").json()["items"][0]["sender"] == "R&D"
    assert c.post("/api/inbox/0a0a0a0a/reply", json={"text": "ran it", "done": True}).status_code == 200
    assert c.post("/api/inbox/0a0a0a0a/done").status_code == 200
    for bad, body in (("/api/inbox/0A0A0A0A/done", None), ("/api/inbox/0a0a0a0a/delete", None),
                      ("/api/inbox/0a0a0a0a/reply", {"text": "  "}), ("/api/inbox/0a0a0a0a/reply", {"text": "x" * 2001})):
        assert c.post(bad, json=body).status_code == 400
    assert calls[1:] == [("POST", "/inbox/0a0a0a0a/reply", {"text": "ran it", "done": True}),
                         ("POST", "/inbox/0a0a0a0a/done", None)]
    assert told == [("ran it", True)]  # the sender's session is told about a reply, not about a bare "done"
    assert "/api/inbox" not in main.VIEWER_ROUTES


def test_a_proxmox_change_is_decided_through_the_tool_server_and_checked_first(monkeypatch):
    import main
    calls = []

    async def fake_admin(method, path, body=None):
        calls.append((method, path))
        return main.JSONResponse({"id": "9f9f9f9f", "approved": True})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.post("/api/proxmox/waiting/9f9f9f9f/approve").json()["approved"] is True
    for bad in ("/api/proxmox/waiting/9F9F9F9F/approve", "/api/proxmox/waiting/9f9f9f9f/delete",
                "/api/proxmox/waiting/12345/decline"):
        assert c.post(bad).status_code == 400
    assert calls == [("POST", "/proxmox/pending/9f9f9f9f/approve")]
    assert "/api/proxmox/waiting" not in main.VIEWER_ROUTES


def test_replies_are_helpers_comments_since_the_read_mark(monkeypatch):
    """The owner, 2026-10-06: "i need to know when i have replies on todo list too". Helpers' comments, newest first;
    the owner's own never count; Mark all read moves the mark for every device; first use counts the last two days."""
    import main
    now = time.time()
    items = [{"id": 7, "text": "Build the thing", "status": "done", "comments": [
                {"by": "owner", "text": "please", "at": now - 50},
                {"by": "my-app", "text": "Done: it works", "at": now - 10}]},
             {"id": 8, "text": "Old task", "status": "new", "comments": [
                {"by": "Research and Development", "text": "ancient", "at": now - 5 * 86400}]}]

    async def fake_tool(name, args, timeout=15, quiet=False):
        assert name == "todo.list" and args["status"] == "all" and quiet
        return {"items": items}
    monkeypatch.setattr(main, "_mcp_call_tool", fake_tool)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    d = c.get("/api/todo/replies").json()
    assert d["count"] == 1 and d["replies"][0]["id"] == 7 and d["replies"][0]["text"] == "Done: it works"
    assert c.post("/api/todo/replies/seen").status_code == 200
    assert c.get("/api/todo/replies").json()["count"] == 0
    assert "/api/todo/replies" not in main.VIEWER_ROUTES
    # Replying on a task reads it: a helper's comment before the owner's own reply there isn't new any more
    c.post("/api/todo/replies/seen")  # (kept as is: the mark is "now")
    main.TODO_SEEN_FILE.unlink()
    items[0]["comments"].append({"by": "owner", "text": "thanks", "at": now - 5})
    assert c.get("/api/todo/replies").json()["count"] == 0


def test_the_approvals_bar_is_on_every_page_and_never_for_view_only_logins():
    page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    assert '<div class="approvals" id="approvals" hidden>' in page
    assert ".viewer .approvals" in page
    assert "setInterval(() => { if (!document.hidden && !_viewer) { loadApprovals(); loadReplies(); loadMessages(); } }" in page
    for route in ("/api/todo/", "/api/shared-memory/waiting/", "/api/proxmox/waiting/"):
        assert route in page.split("const APPROVAL_KINDS")[1].split("};")[0], route


@pytest.mark.asyncio
async def test_connections_route_proxies_the_tool_server(monkeypatch):
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    asked = {}

    def handler(request: httpx.Request) -> httpx.Response:
        asked["path"] = request.url.path
        return httpx.Response(200, json={"connections": [{"tag": "abc123", "caller": "owner", "calls": 2}], "window_s": 900})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    r = await main.connections_list()
    assert asked["path"] == "/connections" and r.status_code == 200 and b'"abc123"' in r.body


@pytest.mark.asyncio
async def test_context_route_proxies_the_gpu_service(monkeypatch):
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sessions": [{"session": "s-1", "used_pct": 62}]})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    assert (await main.claude_context())["sessions"][0]["used_pct"] == 62


# --- Settings page: gathered from the helper PC, the tool server and the Control Panel ----------------

@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    monkeypatch.setattr(main, "PANEL_SETTINGS_FILE", tmp_path / "panel.json")
    monkeypatch.setattr(main, "_panel_cache", {"sig": None, "data": {}})
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")
    monkeypatch.setattr(main, "MCP_BASE_URL", "http://mcp")
    monkeypatch.setattr(main, "_GPU_AUTH", {"X-GPU-Token": "t"})
    seen, down = [], set()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.host, request.url.path, request.headers.get("x-gpu-token")))
        if request.url.host in down:
            raise httpx.ConnectError("down", request=request)
        if request.method == "GET" and request.url.host not in ("gpu", "mcp"):
            return httpx.Response(200, json={})
        if request.url.host == "gpu" and request.method == "GET":
            return httpx.Response(200, json={"values": {"context_alarm_pct": 90}, "info": [{"group": "Storage on your PC", "label": "Settings file", "value": "x"}],
                                              "schema": {"context_alarm_pct": {"type": "int", "min": 50, "max": 99, "group": "Claude context", "label": "Alarm"}}})
        if request.url.host == "gpu":
            body = json.loads(request.content)
            if body.get("context_alarm_pct") == 100:
                return httpx.Response(400, json={"detail": "Alarm: use a whole number from 50 to 99."})
            return httpx.Response(200, json={"values": body})
        if request.url.host == "mcp" and request.method == "GET":
            return httpx.Response(200, json={"values": {"local_ai_allow_cloud": True},
                                              "schema": {"local_ai_allow_cloud": {"type": "bool", "group": "AI models", "label": "Cloud"}}})
        return httpx.Response(200, json={"values": json.loads(request.content)})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    return TestClient(main.app, headers=OWNER), main, seen, down


def test_settings_gathers_every_source(settings_env):
    client, main, _, _ = settings_env
    d = client.get("/api/settings").json()
    by_key = {i["key"]: i for i in d["items"]}
    assert by_key["context_alarm_pct"]["source"] == "main-pc" and by_key["context_alarm_pct"]["value"] == 90
    assert by_key["local_ai_allow_cloud"]["source"] == "tool-server"
    assert by_key["refresh_seconds"]["source"] == "panel" and by_key["refresh_seconds"]["value"] == 4
    assert d["info"][0]["label"] == "Settings file" and d["errors"] == {}


def test_each_change_goes_to_its_owner(settings_env):
    client, main, seen, _ = settings_env
    assert client.put("/api/settings", json={"source": "main-pc", "key": "context_alarm_pct", "value": 85}).json()["value"] == 85
    assert ("PUT", "gpu", "/settings", "t") in seen                      # the GPU service needs its token
    assert client.put("/api/settings", json={"source": "tool-server", "key": "local_ai_allow_cloud", "value": False}).json()["value"] is False
    assert client.put("/api/settings", json={"source": "panel", "key": "refresh_seconds", "value": 10}).json()["value"] == 10
    assert json.loads(main.PANEL_SETTINGS_FILE.read_text())["refresh_seconds"] == 10


@pytest.mark.parametrize("body, status, says", [
    ({"source": "main-pc", "key": "context_alarm_pct", "value": 100}, 400, "50 to 99"),
    ({"source": "panel", "key": "refresh_seconds", "value": 1}, 400, "from 2 to 60"),
    ({"source": "panel", "key": "public_url", "value": "not a url"}, 400, "web address"),
    ({"source": "panel", "key": "nope", "value": 1}, 400, "Unknown setting"),
    ({"source": "mars", "key": "x", "value": 1}, 400, "Unknown place"),
    ({"key": "x"}, 400, "source, key, value"),
])
def test_bad_changes_are_refused_clearly(settings_env, body, status, says):
    client, _, _, _ = settings_env
    r = client.put("/api/settings", json=body)
    assert r.status_code == status and says in r.json()["error"]


def test_a_source_that_is_down_is_reported_not_fatal(settings_env):
    client, main, _, down = settings_env
    down.add("gpu")
    d = client.get("/api/settings").json()
    assert "your PC" in d["errors"]["main-pc"] and any(i["source"] == "panel" for i in d["items"])


def test_connections_show_addresses_only(settings_env, monkeypatch):
    client, main, _, down = settings_env
    down.add("ntfy")
    monkeypatch.setattr(main, "LOCAL_AI_SERVERS", [])
    monkeypatch.setattr(main, "MEMORY_CORE_BASE_URL", "http://memory:8420")
    monkeypatch.setattr(main, "NTFY_HEALTH_URL", "http://ntfy:8080/v1/health?token=secret")
    rows = client.get("/api/settings/connections").json()["connections"]
    assert {r["name"] for r in rows} >= {"HomeShed", "Shared memory", "Phone alerts (ntfy)"}
    assert all("secret" not in r["address"] and "/v1/health" not in r["address"] for r in rows)
    assert next(r for r in rows if r["name"] == "Shared memory") == {"name": "Shared memory", "address": "http://memory:8420", "ok": True}
    assert next(r for r in rows if r["name"] == "Phone alerts (ntfy)")["ok"] is False


# --- view-only logins --------------------------------------------------------------------------------

@pytest.fixture
def owner(tmp_path, monkeypatch):
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "hunter2-test")  # secret-scan: allow (fake test password)
    monkeypatch.setattr(main, "DASHBOARD_VIEWER_PASSWORD", "")
    monkeypatch.setattr(main, "VIEWERS_FILE", tmp_path / "viewers.json")
    monkeypatch.setattr(main, "_viewers_cache", {"sig": None, "data": None})
    monkeypatch.setattr(main, "_failures", {})
    # A browser on the panel's own page: its changes carry the page's Origin (the CSRF check, gate C9)
    client = TestClient(main.app, headers={"Origin": "http://testserver"})
    assert client.post("/login", json={"password": "hunter2-test"}).json()["role"] == "owner"  # secret-scan: allow (fake test password)
    return client, main


@pytest.fixture
def viewer(owner):
    client, main = owner
    created = client.post("/api/viewers", json={"name": "dave", "note": "Dave from work"}).json()
    friend = TestClient(main.app)
    r = friend.post("/login", json={"password": created["password"]})
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    return friend, main, created["password"]


def test_viewer_cookie_is_marked_and_me_names_them(viewer):
    friend, main, _ = viewer
    assert ".v.dave." in friend.cookies.get("cp_session")
    assert friend.get("/api/me").json() == {"role": "viewer", "name": "dave"}


def test_passwords_are_stored_hashed_only(owner, viewer):
    _, main, password = viewer
    raw = main.VIEWERS_FILE.read_text()
    assert password not in raw and '"hash"' in raw and '"salt"' in raw


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/clients"),
    ("GET", "/api/connections"),
    ("GET", "/api/memory/platform"),
    ("GET", "/api/settings"),
    ("GET", "/api/docker/containers"),
    ("GET", "/api/monitors"),
    ("GET", "/api/observations"),
    ("GET", "/api/models"),
    ("GET", "/api/delegator/decisions"),
    ("GET", "/api/tools"),
    ("GET", "/api/viewers"),
    ("POST", "/api/viewers"),
    ("DELETE", "/api/viewers/dave"),
    ("POST", "/api/tools/memory.recall/disable"),
    ("PUT", "/api/settings"),
    ("POST", "/api/clients"),
    ("POST", "/api/todo/runner/run-now"),
])
def test_viewer_is_refused_everything_not_shared(viewer, method, path):
    friend, _, _ = viewer
    assert friend.request(method, path).status_code == 403


def test_viewer_gets_memory_counts_but_never_text(viewer, monkeypatch):
    friend, main, _ = viewer

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"messages": [{"content": "a private fact", "timestamp": 1}]}})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    body = friend.get("/api/memory").json()
    assert body["categories"] and all(c["count"] == 1 and c["latest"] == [] for c in body["categories"])
    assert "a private fact" not in friend.get("/api/memory").text


def test_viewer_context_drops_paths_owner_keeps_them(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sessions": [{"session": "s-1", "cwd": "/home/me/site", "project": "site", "used_pct": 40}]})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    assert friend.get("/api/context").json()["sessions"][0] == {"project": "Session 1", "used_pct": 40}
    assert client.get("/api/context").json()["sessions"][0]["cwd"] == "/home/me/site"


# The feeds behind the viewer routes grow; anything the allow-lists don't name must stay hidden (2026-09-29 review by
# the Research and Development session: settings, project folders and API client names had started coming through).
_EXTRA = "secret_new_field"
_UPSTREAM = {
    "/activity": [{"id": "web.read", "timestamp": 1.0, "duration_ms": 3.0, "ok": True, "client": "my-app",
                   "conn": "c-9f", _EXTRA: 1}],
    "/usage": {"since": 1.0, "estimated_tokens_saved": 10, "calls": 2, "by_rule": {"reading": 5}, _EXTRA: 1,
               "capabilities": {"web.read": {"calls": 2, "saved_tokens": 5, _EXTRA: 1}},
               "clients": {"my-app": {"calls": 2, "tools": {"web.read": 2}}}},
    "/stats/rtk": {"available": True, "total_saved": 99, _EXTRA: 1},
    "/capabilities": [{"id": "web.read", "name": "Web read", "category": "web", "risk": "read", "description": "Fetch a page.",
                       "execution": {"handler": "tools.web.read.read"}, "inputs": {"url": "str"}, "aliases": ["s-1"],
                       _EXTRA: 1}],
    "/stats/rtk/daily": {"available": True, "summary": {"total_saved": 99, _EXTRA: 1},
                         "daily": [{"date": "2026-09-29", "saved_tokens": 9, _EXTRA: 1}]},
    "/stats/rtk/discover": {"available": True, "commands": ["git log --all"]},
    "/stats/context": {"sessions": [{"used_pct": 40, "project": "Website", "cwd": "/home/me/website", "session": "s-1",
                                     "model": "opus", "compactions": [{"pct": 88, "at": 5.0, _EXTRA: 1}], _EXTRA: 1}],
                       "settings": {"context_alarm_pct": 90, "voice_quiet_from": 23, _EXTRA: 1}, _EXTRA: 1},
}


def _upstream(monkeypatch, main):
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_UPSTREAM.get(request.url.path, {}))
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))


@pytest.mark.parametrize("path", [
    "/api/activity",
    "/api/tokens",
    "/api/savings",
    "/api/context",
    "/api/capabilities",
])
def test_viewer_routes_only_pass_the_fields_they_name(owner, viewer, monkeypatch, path):
    client, main = owner
    friend, _, _ = viewer
    _upstream(monkeypatch, main)
    seen = friend.get(path)
    assert seen.status_code == 200
    for hidden in (_EXTRA, "my-app", "Website", "/home/me/website", "c-9f", "voice_quiet_from", "git log", "s-1"):
        assert hidden not in seen.text, (path, hidden)
    assert _EXTRA in client.get(path).text  # the owner still gets everything


def test_viewer_allow_lists_keep_the_shape_each_panel_draws():
    """Memory keeps counts and an empty list of facts; local AI and features keep today's fields and drop new ones."""
    mem = main_module().VIEWER_ROUTES["/api/memory"]({"categories": [
        {"id": "tasks", "label": "Tasks", "legacy": False, "count": 3, "count_capped": False,
         "latest": [{"content": "a private fact", "timestamp": "t"}], _EXTRA: 1}], _EXTRA: 1})
    assert mem == {"categories": [{"id": "tasks", "label": "Tasks", "legacy": False, "count": 3, "count_capped": False,
                                   "latest": []}]}
    ai = main_module().VIEWER_ROUTES["/api/local-ai"]({"servers": [{"name": "qwen3", "online": True, "total_tokens": 9,
                                                                    "sparkline": [1, 2], "url": "http://10.0.0.5", _EXTRA: 1}]})
    assert ai == {"servers": [{"name": "qwen3", "online": True, "total_tokens": 9, "sparkline": [1, 2]}]}
    feats = main_module().VIEWER_ROUTES["/api/features"]({"features": [{"label": "Tools", "status": "ok", "detail": "Up.",
                                                                        _EXTRA: 1}]})
    assert feats == {"features": [{"label": "Tools", "status": "ok", "detail": "Up."}]}


def main_module():
    import main
    return main


def test_viewers_keep_what_the_page_draws(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    _upstream(monkeypatch, main)
    assert friend.get("/api/activity").json() == [{"id": "web.read", "timestamp": 1.0, "duration_ms": 3.0, "ok": True}]
    ctx = friend.get("/api/context").json()
    assert ctx["settings"] == {"context_alarm_pct": 90} and ctx["sessions"][0]["compactions"] == [{"pct": 88, "at": 5.0}]
    tokens = friend.get("/api/tokens").json()
    assert tokens["rtk"]["total_saved"] == 99 and tokens["estimate"]["capabilities"]["web.read"]["saved_tokens"] == 5
    assert "clients" not in tokens["estimate"]
    savings = friend.get("/api/savings").json()
    assert "discover" not in savings and savings["daily"]["daily"][0]["saved_tokens"] == 9


def test_viewers_get_an_errors_status_but_not_its_words(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "MCP_BASE_URL", "http://127.0.0.1:9")  # nothing listens: the route's error names it
    assert "mcp-server" in client.get("/api/activity").text  # the owner sees what broke
    seen = friend.get("/api/activity")
    assert seen.status_code == 502 and seen.json() == {"detail": "Not available right now."}


SID = "11111111-2222-3333-4444-555555555555"


def test_past_sessions_are_the_owners_only_and_carry_the_pc_token(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")
    monkeypatch.setattr(main, "_GPU_AUTH", {"X-GPU-Token": "t0ken"})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("x-gpu-token")))
        if request.method == "GET":
            return httpx.Response(200, json={"sessions": [{"id": SID, "name": "Website"}], "resume_on": True})
        return httpx.Response(409, json={"detail": "That session is open now. Switch to its window."})
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    assert client.get("/api/claude/sessions").json()["sessions"][0]["name"] == "Website"
    r = client.post(f"/api/claude/sessions/{SID}/resume")
    assert r.status_code == 409 and r.json()["error"] == "That session is open now. Switch to its window."
    assert calls == [("GET", "/sessions", "t0ken"), ("POST", f"/sessions/{SID}/resume", "t0ken")]
    assert friend.get("/api/claude/sessions").status_code == 403
    assert friend.post(f"/api/claude/sessions/{SID}/resume").status_code == 403
    assert len(calls) == 2  # the viewer's requests never reached the PC


def test_projects_are_the_owners_only_and_carry_the_pc_token(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")
    monkeypatch.setattr(main, "_GPU_AUTH", {"X-GPU-Token": "t0ken"})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("x-gpu-token"), request.content))
        if request.method == "GET":
            return httpx.Response(200, json={"projects": [{"folder": "D:\\work", "owner": "builder"}]})
        if request.url.path == "/projects/pins":
            return httpx.Response(400, json={"detail": "Pin something inside the project's folder."})
        return httpx.Response(200, json={"pins": []})
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    assert client.get("/api/projects").json()["projects"][0]["owner"] == "builder"
    r = client.post("/api/projects/pins", json={"folder": "D:\\work", "path": "../x", "note": "n", "extra": "dropped"})
    assert r.status_code == 400 and r.json()["error"] == "Pin something inside the project's folder."
    assert client.post("/api/projects/pins/remove", json={"folder": "D:\\work", "path": "a.md"}).json() == {"pins": []}
    assert [c[:3] for c in calls] == [("GET", "/projects", "t0ken"), ("POST", "/projects/pins", "t0ken"),
                                      ("POST", "/projects/pins/remove", "t0ken")]
    assert b"extra" not in calls[1][3]
    for method, path in (("get", "/api/projects"), ("post", "/api/projects/pins"), ("post", "/api/projects/pins/remove")):
        assert getattr(friend, method)(path).status_code == 403
    assert len(calls) == 3  # the viewer's requests never reached the PC


def test_reopening_checks_the_id_before_asking_the_pc(owner, monkeypatch):
    client, main = owner
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(lambda req: pytest.fail("the PC was asked")))
    assert client.post("/api/claude/sessions/not-an-id/resume").status_code == 400
    assert client.post("/api/claude/sessions/AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE/resume").status_code == 400


def test_hiding_savings_hides_the_token_numbers_too(owner, viewer, monkeypatch):
    client, main = owner
    friend, _, _ = viewer
    _upstream(monkeypatch, main)
    assert friend.get("/api/tokens").status_code == 200
    main.panel_update({"viewers_see_savings": False})
    assert friend.get("/api/tokens").status_code == 403 and client.get("/api/tokens").status_code == 200


def test_new_password_or_removal_signs_the_viewer_out(owner, viewer):
    client, main = owner
    friend, _, old = viewer
    cookie = friend.cookies.get("cp_session")
    expiry, v, name, sig = cookie.split(".")
    assert main._session_role(f"{expiry}.{sig}") is None                      # can't pass as the owner
    new = client.post("/api/viewers/dave/reset", json={}).json()["password"]
    assert main._session_role(cookie) is None and new != old
    assert TestClient(main.app).post("/login", json={"password": old}).status_code == 401
    again = TestClient(main.app)
    assert again.post("/login", json={"password": new}).json()["role"] == "viewer"
    assert client.delete("/api/viewers/dave").status_code == 200
    assert main._session_role(again.cookies.get("cp_session")) is None


def test_several_viewers_each_with_their_own_password(owner):
    client, main = owner
    a = client.post("/api/viewers", json={"name": "amy"}).json()["password"]
    b = client.post("/api/viewers", json={"name": "ben", "expires_days": 7}).json()["password"]
    rows = {v["name"]: v for v in client.get("/api/viewers").json()["viewers"]}
    assert set(rows) == {"amy", "ben"} and rows["ben"]["expires"] and not rows["amy"]["expires"]
    assert TestClient(main.app).post("/login", json={"password": a}).status_code == 200
    login_b = TestClient(main.app)
    login_b.post("/login", json={"password": b})
    assert login_b.get("/api/me").json()["name"] == "ben"
    assert client.get("/api/viewers").json()["viewers"][1]["last_login"]


@pytest.mark.parametrize("body, says", [
    ({"name": "Dave!"}, "Name"), ({"name": "d"}, "Name"), ({"name": "dave", "expires_days": 0.5}, "Expires"),
    ({"name": "dave", "expires_days": 999}, "Expires"),
])
def test_bad_new_logins_are_refused_clearly(owner, body, says):
    client, _ = owner
    r = client.post("/api/viewers", json=body)
    assert r.status_code == 400 and says in r.json()["error"]


def test_duplicate_name_refused(owner):
    client, _ = owner
    client.post("/api/viewers", json={"name": "dave"})
    assert "already" in client.post("/api/viewers", json={"name": "dave"}).json()["error"]


def test_expired_login_stops_working(owner, monkeypatch):
    client, main = owner
    pw = client.post("/api/viewers", json={"name": "temp", "expires_days": 1}).json()["password"]
    data = json.loads(main.VIEWERS_FILE.read_text())
    data["viewers"]["temp"]["expires"] = time.time() - 1
    main.VIEWERS_FILE.write_text(json.dumps(data))
    assert TestClient(main.app).post("/login", json={"password": pw}).status_code == 401
    assert client.get("/api/viewers").json()["viewers"][0]["expired"] is True


def test_unreadable_file_fails_closed_and_is_never_overwritten(owner):
    client, main = owner
    pw = client.post("/api/viewers", json={"name": "dave"}).json()["password"]
    main.VIEWERS_FILE.write_text("{corrupt")
    assert TestClient(main.app).post("/login", json={"password": pw}).status_code == 401
    r = client.post("/api/viewers", json={"name": "amy"})
    assert r.status_code == 503 and main.VIEWERS_FILE.read_text() == "{corrupt"
    assert client.get("/api/viewers").json()["unreadable"] is True


def test_old_env_password_moves_into_the_list_once(owner, monkeypatch):
    client, main = owner
    monkeypatch.setattr(main, "DASHBOARD_VIEWER_PASSWORD", "legacy-pass-1")  # secret-scan: allow (fake test password)
    monkeypatch.setattr(main, "_viewers_cache", {"sig": None, "data": None})
    friend = TestClient(main.app)
    assert friend.post("/login", json={"password": "legacy-pass-1"}).json()["role"] == "viewer"  # secret-scan: allow (fake test password)
    assert friend.get("/api/me").json()["name"] == "friend"
    raw = main.VIEWERS_FILE.read_text()
    assert "legacy-pass-1" not in raw and '"env_moved": true' in raw  # secret-scan: allow (fake test password)
    client.delete("/api/viewers/friend")                       # removed stays removed
    monkeypatch.setattr(main, "_viewers_cache", {"sig": None, "data": None})
    assert "friend" not in {v["name"] for v in client.get("/api/viewers").json()["viewers"]}


def test_send_to_phone(owner, monkeypatch):
    client, main = owner
    sent = []

    async def fake_call(tool, args, timeout=15, **_):
        sent.append((tool, args))
        return {}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PUBLIC_URL", "https://panel.example.com")
    d = client.post("/api/viewers", json={"name": "dave", "send_to_phone": True}).json()
    assert d["sent"] is True and sent[0][0] == "notify.send"
    assert d["password"] in sent[0][1]["message"] and "https://panel.example.com" in sent[0][1]["message"]


def test_no_viewers_means_no_viewer_login(owner):
    _, main = owner
    fresh = TestClient(main.app)
    assert fresh.post("/login", json={"password": ""}).status_code == 401
    assert fresh.post("/login", json={"password": "anything-at-all"}).status_code == 401  # secret-scan: allow (fake test password)


def test_owner_login_unchanged(pw_client):
    client, main = pw_client
    r = client.post("/login", json={"password": "hunter2-test"})  # secret-scan: allow (fake test password)
    assert r.json()["role"] == "owner" and client.get("/api/me").json()["role"] == "owner"
    assert client.get("/api/clients").status_code != 403


def test_owner_can_hide_savings_and_context_from_viewers(owner, viewer):
    client, main = owner
    friend, _, _ = viewer
    main.panel_update({"viewers_see_savings": False, "viewers_see_context": False})
    assert friend.get("/api/savings").status_code == 403 and friend.get("/api/context").status_code == 403
    ui = friend.get("/api/ui-settings").json()
    assert ui["viewers_see_savings"] is False and ui["refresh_seconds"] == 4


def test_panel_name_setting_reaches_every_login(owner, viewer):
    client, main = owner
    assert client.get("/api/ui-settings").json()["panel_name"] == ""  # no default name: the page shows "Control Panel"
    main.panel_update({"panel_name": "Shed 2 (garage)"})
    assert client.get("/api/ui-settings").json()["panel_name"] == "Shed 2 (garage)"
    friend, _, _ = viewer
    assert friend.get("/api/ui-settings").json()["panel_name"] == "Shed 2 (garage)"
    for bad in ("Shed\\One", "<b>x</b>", "x" * 41):
        try:
            main.panel_update({"panel_name": bad})
            raise AssertionError(f"accepted {bad!r}")
        except ValueError as exc:
            assert "Name under the title" in str(exc)


def test_hidden_always_hides():
    """A panel's own display beat the hidden attribute twice (KB-0040, then Past sessions): one global rule stops it.
    Drafted by the local model; reads the page as UTF-8."""
    import re
    from pathlib import Path
    css = (Path(__file__).with_name("static") / "index.html").read_text(encoding="utf-8")
    assert re.search(r"^\s*\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important\s*;?[^}]*\}", css, re.MULTILINE)


def test_page_script_parses(tmp_path):
    """The page's script must parse: a wording change once put an apostrophe inside a single-quoted string, and the
    live page stuck on "Connecting" (2026-09-29). Needs Node, so it's skipped where Node isn't installed."""
    import re
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    assert blocks
    for i, js in enumerate(blocks):
        f = tmp_path / f"script_{i}.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"script {i}: {r.stderr}"


def test_login_length_follows_the_setting(owner):
    client, main = owner
    main.panel_update({"session_days": 3})
    fresh = TestClient(main.app)
    r = fresh.post("/login", json={"password": "hunter2-test"})  # secret-scan: allow (fake test password)
    assert "Max-Age=259200" in r.headers["set-cookie"]


@pytest.mark.asyncio
async def test_rules_route_proxies_the_gpu_service(monkeypatch):
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://gpu")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stats/rules"
        return httpx.Response(200, json={"rules": [{"id": "RULE-D-PROJECTS-010", "hits": 2}], "requests": {"total": 3}})

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    assert (await main.rules_monitor())["rules"][0]["hits"] == 2


def test_viewers_cannot_see_the_rules(viewer):
    friend, _, _ = viewer
    assert friend.get("/api/rules").status_code == 403


# --- Guides: central library with the bundled copy as fallback ------------------------------------

GUIDE = """---
title: The Control Panel, page by page
slug: control-panel
audience: everyone
updated: 2026-09-28
---

# The Control Panel

Hello.
"""


@pytest.fixture
def guides(tmp_path, monkeypatch):
    import main

    folder = tmp_path / "guides"
    folder.mkdir()
    (folder / "control-panel.md").write_text(GUIDE, encoding="utf-8")
    (folder / "owner-notes.md").write_text(GUIDE.replace("control-panel", "owner-notes").replace("everyone", "owner"), encoding="utf-8")
    monkeypatch.setattr(main, "GUIDES_DIR", folder)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    return TestClient(main.app, headers=OWNER), main


def test_bundled_guides_when_no_library_is_set(guides):
    client, _ = guides
    d = client.get("/api/guides").json()
    assert {g["slug"] for g in d["guides"]} == {"control-panel", "owner-notes"} and d["note"] is None
    g = client.get("/api/guides/control-panel").json()
    assert g["title"] == "The Control Panel, page by page" and g["body"].startswith("# The Control Panel") and g["source"] == "bundled"
    assert client.get("/api/guides/nope").status_code == 404


@pytest.mark.parametrize("payload", [
    {"data": [{"id": 1, "documentId": "abc", "title": "From the library", "slug": "control-panel", "body": "# Hi", "audience": "everyone", "order": 1}]},
    {"data": [{"id": 1, "attributes": {"title": "From the library", "slug": "control-panel", "body": "# Hi", "audience": "everyone", "order": 1}}]},
])
def test_library_guides_in_strapi_5_and_4_shapes(guides, monkeypatch, payload):
    client, main = guides
    main.panel_update({"guide_library_url": "https://library.test/api/guides"})
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    d = client.get("/api/guides").json()
    assert [g["title"] for g in d["guides"]][0] == "From the library" and d["guides"][0]["source"] == "library"
    # the install's own guides stay, unless the library has one of the same name (2026-10-02)
    assert [(g["slug"], g["source"]) for g in d["guides"]] == [("control-panel", "library"), ("owner-notes", "bundled")]


def test_library_down_falls_back_with_a_note(guides, monkeypatch):
    client, main = guides
    main.panel_update({"guide_library_url": "https://library.test/api/guides"})

    def boom(request):
        raise httpx.ConnectError("down", request=request)

    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(boom))
    d = client.get("/api/guides").json()
    assert {g["source"] for g in d["guides"]} == {"bundled"} and "isn't reachable" in d["note"]


def test_viewers_can_read_guides_but_not_owner_ones(guides, monkeypatch):
    client, main = guides
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", "hunter2-test")  # secret-scan: allow (fake test password)
    monkeypatch.setattr(main, "_failures", {})
    owner = TestClient(main.app, headers={"Origin": "http://testserver"})  # the panel's own page (CSRF check)
    owner.post("/login", json={"password": "hunter2-test"})  # secret-scan: allow (fake test password)
    pw = owner.post("/api/viewers", json={"name": "dave"}).json()["password"]
    friend = TestClient(main.app)
    friend.post("/login", json={"password": pw})
    assert [g["slug"] for g in friend.get("/api/guides").json()["guides"]] == ["control-panel"]
    assert friend.get("/api/guides/control-panel").status_code == 200
    assert friend.get("/api/guides/owner-notes").status_code == 404
    assert friend.get("/api/guides-admin").status_code == 403       # the prefix is exact: nothing else opens up


# --- every project's memory (2026-09-28: the Memory panel only showed this project's agent) --------------

def _memory_mock(monkeypatch, seen):
    import main

    real = httpx.AsyncClient
    facts = {"agt-mine": 3, "agt-quiz": 2}

    def handler(req):
        body = json.loads(req.content or b"{}")
        seen.append((req.url.path, body.get("agent_id"), body.get("limit")))
        if req.url.path == "/v3/meta/agent/list":
            return httpx.Response(200, json={"code": 0, "data": {"items": [
                {"agent_id": "agt-mine", "name": "claude-code"}, {"agent_id": "agt-quiz", "name": "quizroom"},
                {"agent_id": "agt-empty", "name": "website"}]}})
        n = facts.get(body.get("agent_id"), 0) if body.get("session_id") == "facts:platform" else 0
        return httpx.Response(200, json={"data": {"messages": [{"content": f"fact {i}", "timestamp": 1} for i in range(n)]}})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "MEMORY_AGENT_ID", "agt-mine")
    monkeypatch.setattr(main, "_agents_cache", {"at": 0.0, "agents": None, "totals": {}, "totals_at": 0.0})
    return main


def test_memory_projects_lists_every_agent_with_counts(monkeypatch):
    seen = []
    main = _memory_mock(monkeypatch, seen)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    rows = TestClient(main.app, headers=OWNER).get("/api/memory/projects").json()["projects"]
    assert [(r["name"], r["facts"], r["mine"]) for r in rows] == [("claude-code", 3, True), ("quizroom", 2, False), ("website", 0, False)]
    assert all(limit is None or limit <= main.MEMORY_QUERY_MAX for _, _, limit in seen)


def test_memory_reads_the_chosen_project_and_refuses_unknown_ones(monkeypatch):
    seen = []
    main = _memory_mock(monkeypatch, seen)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    cats = client.get("/api/memory?agent=agt-quiz").json()["categories"]
    assert sum(c["count"] for c in cats) == 2 and {a for p, a, _ in seen if p.endswith("/query")} == {"agt-quiz"}
    assert client.get("/api/memory?agent=agt-nope").status_code == 404
    assert client.get("/api/memory/platform?agent=agt-quiz&limit=500").json()["count"] == 2
    assert seen[-1][2] == main.MEMORY_QUERY_MAX                 # never over memory-core's limit


def test_viewers_always_get_this_projects_memory(viewer, monkeypatch):
    friend, main, _ = viewer
    seen = []
    _memory_mock(monkeypatch, seen)
    friend.get("/api/memory?agent=agt-quiz")
    assert {a for p, a, _ in seen if p.endswith("/query")} == {"agt-mine"}
    assert friend.get("/api/memory/projects").status_code == 403


def test_guard_modes_forward_to_the_main_pc_and_validate(monkeypatch):
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"guard_modes": json.loads(req.content)})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    assert client.put("/api/rules/guard-modes", json={"shell.grep-command": "remind"}).json() == {"guard_modes": {"shell.grep-command": "remind"}}
    assert seen == [("PUT", "/settings/guard-modes", {"shell.grep-command": "remind"})]
    assert client.put("/api/rules/guard-modes", json={}).status_code == 400


def test_pack_routes_forward_to_the_main_pc(monkeypatch):
    import main

    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path, req.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    assert client.get("/api/packs").json() == {"ok": True}
    assert client.post("/api/packs/install", json={"slug": "token-saver", "extra": 1}).json() == {"ok": True}
    posted = [s for s in seen if s[:2] == ("POST", "/packs/install")]
    assert len(posted) == 1 and json.loads(posted[0][2]) == {"slug": "token-saver"}  # no pack from the tool server
    assert client.post("/api/packs/delete-everything", json={"slug": "x"}).status_code == 404


# --- voice on the Control Panel (2026-09-28) ------------------------------------------------------------


# --- Credentials: owner only, names checked, a value shown only after the password, and never by a viewer ---

class _FakeVaultHttp:
    """Stands in for httpx.AsyncClient on the reveal route: records the call, answers like mcp-server."""
    seen: list = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeVaultHttp.seen.append((url, dict(headers or {})))
        import httpx
        return httpx.Response(200, json={"name": url.rsplit("/", 2)[-2], "value": "the-value"})


def test_viewers_cannot_reach_credentials(viewer):
    friend, _, _ = viewer
    for method, path in (("GET", "/api/vault"), ("PUT", "/api/vault/X_KEY"), ("POST", "/api/vault/X_KEY/reveal"),
                         ("DELETE", "/api/vault/X_KEY"), ("POST", "/api/vault-test"), ("GET", "/api/apis")):
        assert friend.request(method, path, json={}).status_code == 403, path


def test_a_crafted_name_never_reaches_another_tool_server_route(owner, monkeypatch):
    client, main = owner
    called = []

    async def fake_admin(method, path, body=None):
        called.append(path)
        return main.JSONResponse({})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    for bad in ("lower", "..%2Fclients", "X-Y"):
        assert client.put(f"/api/vault/{bad}", json={"value": "v"}).status_code in (400, 404, 405)
    assert client.put("/api/vault/GOOD_KEY", json={"value": "v"}).status_code == 200
    assert called == ["/vault/GOOD_KEY"]


def test_showing_a_value_needs_the_password_and_sends_the_reveal_key(owner, monkeypatch):
    client, main = owner
    monkeypatch.setattr(main, "VAULT_REVEAL_KEY", "reveal-test")  # secret-scan: allow (fake)
    monkeypatch.setattr(main, "_reveal_failures", [])
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeVaultHttp)
    _FakeVaultHttp.seen = []
    assert client.post("/api/vault/NVIDIA_API_KEY/reveal", json={"password": "wrong"}).status_code == 403
    assert _FakeVaultHttp.seen == []                       # a wrong password never reaches the tool server
    r = client.post("/api/vault/NVIDIA_API_KEY/reveal", json={"password": "hunter2-test"})  # secret-scan: allow (fake)
    assert r.status_code == 200 and r.json()["value"] == "the-value" and r.headers["cache-control"] == "no-store"
    url, headers = _FakeVaultHttp.seen[0]
    assert url.endswith("/vault/NVIDIA_API_KEY/reveal") and headers["X-Vault-Reveal"] == "reveal-test"


def test_wrong_passwords_lock_showing_for_a_while(owner, monkeypatch):
    client, main = owner
    monkeypatch.setattr(main, "VAULT_REVEAL_KEY", "reveal-test")  # secret-scan: allow (fake)
    monkeypatch.setattr(main, "_reveal_failures", [])
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeVaultHttp)
    for _ in range(main.REVEAL_LOCK_AFTER):
        client.post("/api/vault/NVIDIA_API_KEY/reveal", json={"password": "wrong"})
    r = client.post("/api/vault/NVIDIA_API_KEY/reveal", json={"password": "hunter2-test"})  # secret-scan: allow (fake)
    assert r.status_code == 429


# --- only show what's installed (Release UX, 2026-09-29) ---

class _InstalledHttp:
    """httpx.AsyncClient stand-in: answers the agent audit and voice status like the real services."""
    agent_available = True
    voice_status = "ok"

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, headers=None):
        import httpx
        if url.endswith("/local/agent-audit"):
            return httpx.Response(200, json={"available": _InstalledHttp.agent_available})
        return httpx.Response(200, json={"status": _InstalledHttp.voice_status})


def test_viewers_can_ask_what_is_installed(viewer):
    friend, _, _ = viewer
    assert friend.get("/api/installed").status_code == 200


def test_the_heading_font_is_served_without_sign_in(pw_client):
    """Self-hosted Space Grotesk (static/fonts): the sign-in page uses it, so it must load before sign-in."""
    client, _main = pw_client
    r = client.get("/fonts/space-grotesk-latin-wght.woff2")
    assert r.status_code == 200 and r.content[:4] == b"wOF2"
    assert client.get("/fonts/SpaceGrotesk-OFL.txt").status_code == 200
    assert client.get("/api/health", follow_redirects=False).status_code in (302, 401)  # the rest stays behind sign-in
    # "/fonts/../index.html" (dots encoded, so the client doesn't resolve them) must not skip sign-in (review fix):
    # a path with dot segments or "\" (a separator to the static files under Windows) is refused before any allow-list
    for path in ("/fonts/%2e%2e/index.html", "/fonts/%2e%2e/%2e%2e/api/health", "/fonts/..%5cindex.html",
                 "/fonts/%2e/space-grotesk-latin-wght.woff2", "/fonts/..%2findex.html"):
        assert client.get(path, follow_redirects=False).status_code == 404, path


def test_a_view_only_login_cant_step_out_of_its_prefixes(viewer):
    """The same dot-segment trick against the viewer allow-list (R&D's follow-up, 2026-09-30)."""
    friend, _main, _ = viewer
    assert friend.get("/fonts/space-grotesk-latin-wght.woff2").status_code == 200
    for path in ("/fonts/%2e%2e/api/memory", "/api/guides/%2e%2e/%2e%2e/api/viewers", "/fonts/%2e%2e/index.html"):
        assert friend.get(path, follow_redirects=False).status_code == 404, path


def test_release_progress_comes_from_the_tool_server(monkeypatch):
    """The Overview's Release progress card (repo.progress, planned with the Github prepper 2026-09-30)."""
    import main
    seen = {}

    async def fake_call(name, args, **_):
        seen["call"] = (name, args)
        return {"projects": [{"project": "homeshed", "title": "HomeShed public release", "percent": 83, "done": 10,
                              "total": 12, "metrics": {"readiness": "27 of 29"}, "waiting_on_owner": [], "agent": "x",
                              "updated": 1.0, "note": ""}]}
    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    r = TestClient(main.app, headers=OWNER).get("/api/progress")
    assert r.status_code == 200 and r.json()["projects"][0]["percent"] == 83 and seen["call"] == ("repo.progress", {})

    async def down(name, args, **_):
        raise RuntimeError("mcp-server unreachable")
    monkeypatch.setattr(main, "_mcp_call_tool", down)
    assert TestClient(main.app, headers=OWNER).get("/api/progress").status_code == 502


def test_a_view_only_login_cannot_read_release_progress(viewer):
    friend, _main, _ = viewer
    assert friend.get("/api/progress").status_code == 403
    assert friend.get("/api/progress/homeshed").status_code == 403


def test_release_progress_shows_the_folder_from_the_project_list_only(monkeypatch):
    """The owner, 2026-10-01: "where the project is being made (dir location)". Progress data names a workspace, never a
    path; the owner's panel looks the folder up in the tool server's project list."""
    import main

    async def fake_call(name, args, **_):
        assert name == "repo.progress"
        if args.get("project"):
            return {"project": args["project"], "steps": [{"id": "scan", "title": "Scan", "status": "done", "evidence": "0"}]}
        return {"projects": [{"project": "homeshed", "title": "HomeShed", "workspace": "release-prep"},
                             {"project": "other", "title": "Named nowhere", "workspace": "gone"},
                             {"project": "plain", "title": "No workspace"}]}

    async def fake_json(path):
        assert path == "/projects"
        return 200, {"projects": [{"path": "C:/Users/you/release-prep", "name": "release-prep", "agent": "agt-x"}]}

    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "_tool_json", fake_json)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    client = TestClient(main.app, headers=OWNER)
    rows = client.get("/api/progress").json()["projects"]
    assert rows[0]["folder"] == "C:/Users/you/release-prep" and "folder" not in rows[1] and "folder" not in rows[2]
    assert client.get("/api/progress/homeshed").json()["steps"][0]["id"] == "scan"
    assert client.get("/api/progress/Bad%20Name").status_code == 404


def test_the_todo_page_relays_the_tools_and_says_when_its_pro_only(monkeypatch):
    """The To-do page (todo.*, the owner, 2026-09-30): Pro gets the list; the free plan gets {pro: false} and the example."""
    import main
    seen = []

    async def fake_call(name, args, **_):
        seen.append((name, args))
        return {"items": [], "total": 0, "open": 0, "waiting_on_ok": 0} if name == "todo.list" else {"id": 1}
    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/todo?status=closed").json()["total"] == 0
    assert seen[-1] == ("todo.list", {"status": "closed", "detail": "full", "limit": 100})
    assert c.get("/api/todo?status=everything").status_code == 400
    c.post("/api/todo", json={"text": "Tidy the README", "assignee": "Docs", "priority": "high", "sneaky": "x"})
    assert seen[-1] == ("todo.add", {"text": "Tidy the README", "assignee": "Docs", "priority": "high"})
    c.post("/api/todo/7", json={"approve": True, "comment": "Go ahead", "id": 99})
    assert seen[-1] == ("todo.update", {"id": 7, "comment": "Go ahead", "approve": True})
    c.post("/api/todo/7", json={"approve": "yes", "status": "cancelled"})
    assert seen[-1] == ("todo.update", {"id": 7, "status": "cancelled"})  # only a real true approves
    c.post("/api/todo", json={"text": "Check the backup", "assignee": "ops", "repeat": "daily"})
    assert seen[-1] == ("todo.add", {"text": "Check the backup", "assignee": "ops", "repeat": "daily"})
    c.post("/api/todo/7", json={"repeat": "never"})
    assert seen[-1] == ("todo.update", {"id": 7, "repeat": "never"})  # the owner: repeat tasks (and stopping one)
    c.post("/api/todo", json={"text": "Tidy the code", "assignee": "ops", "auto": "yes"})
    assert "auto" not in seen[-1][1]  # only a real true runs without asking
    c.post("/api/todo", json={"text": "Tidy the code", "assignee": "ops", "auto": True})
    assert seen[-1][1]["auto"] is True
    c.post("/api/todo/7", json={"auto": False})
    assert seen[-1] == ("todo.update", {"id": 7, "auto": False})

    async def free(name, args, **_):
        raise RuntimeError("Error executing tool list: The to-do list comes with HomeShed Pro: your agents work "
                           "through the tasks you add, overnight too. Connect a Pro account in Settings to use it.")
    monkeypatch.setattr(main, "_mcp_call_tool", free)
    r = c.get("/api/todo")
    assert r.status_code == 200 and r.json()["pro"] is False and r.json()["note"].startswith("The to-do list comes with")

    async def down(name, args, **_):
        raise RuntimeError("mcp-server unreachable")
    monkeypatch.setattr(main, "_mcp_call_tool", down)
    assert c.get("/api/todo").status_code == 502


def test_the_runner_pill_shows_only_what_it_needs(monkeypatch):
    """GET /api/todo/runner: the PC helper's /todo/jobs, trimmed (no folders, results or costs)."""
    import main
    real, seen = httpx.AsyncClient, []
    body = {"on": True, "in_hours": False, "hours": [23, 7], "can_start": False, "why_not": "it's outside the hours set",
            "tonight": 1, "most": 6, "plan_pct": 48.0, "finished": [{"job": "x", "result": "private words"}],
            "running": [{"job": "j", "id": 4, "assignee": "Website", "folder": "D:\\work\\site",
                         "started": 1.0, "result": "", "cost_usd": 0.3}]}

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(200, json=body)
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    d = TestClient(main.app, headers=OWNER).get("/api/todo/runner").json()
    assert seen[0].endswith("/todo/jobs") and d["on"] is True and d["hours"] == [23, 7] and d["most"] == 6
    assert d["running"] == [{"id": 4, "assignee": "Website", "started": 1.0}] and "finished" not in d
    monkeypatch.setattr(main.httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(lambda req: httpx.Response(500))))
    assert TestClient(main.app, headers=OWNER).get("/api/todo/runner").status_code == 502
    seen.clear()
    main_update = []

    async def fake_call(name, args, **_):
        main_update.append((name, args))
        return {}
    monkeypatch.setattr(main, "_mcp_call_tool", fake_call)
    TestClient(main.app, headers=OWNER).post("/api/todo/3", json={"priority": "high"})
    assert main_update == [("todo.update", {"id": 3, "priority": "high"})]  # the canvas changes priority in place


def test_a_view_only_login_gets_the_example_todo_list_never_the_real_one(viewer):
    friend, main, _ = viewer
    assert friend.get("/api/todo").status_code == 403
    assert friend.get("/api/todo/runner").status_code == 403
    assert friend.post("/api/todo", json={"text": "x"}).status_code == 403
    assert friend.post("/api/todo/1", json={"approve": True}).status_code == 403
    page = (Path(main.__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "'todo'" in page[page.index("const VIEWER_TABS"):][:120]  # the page is there for them...
    body = page[page.index("async function loadTodo()"):][:1800]
    assert "if (!_viewer)" in body and body.index("if (!_viewer)") < body.index("/api/todo")  # ...but never asks
    assert "if (!_todo.example) await todoAgents()" in body  # nor for the owner's agents (their projects)


def test_run_now_asks_the_tool_server_for_one_pass(owner, monkeypatch):
    """The To-do page's "Run now" (R&D C25): the tool server's runner does the pass; this only passes the click on."""
    client, main = owner
    called = []

    async def fake_admin(method, path, body=None):
        called.append((method, path))
        return main.JSONResponse({"start": {"id": 4, "outcome": "started"}})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    assert client.post("/api/todo/runner/run-now").json()["start"]["id"] == 4
    assert called == [("POST", "/todo/run-now")]


ICONS = ["/icons/icon-32.png", "/icons/icon-64.png", "/icons/icon-192.png", "/icons/icon-512.png",
         "/icons/icon-maskable-512.png", "/icons/apple-touch-icon.png", "/favicon.ico", "/manifest.webmanifest"]


def test_the_app_icons_load_before_signing_in(monkeypatch):
    """The owner, 2026-10-01: an icon for the panel (the logo in brand/live-core). A browser fetches them from the
    sign-in page, and the manifest without cookies at all, so they're public: nothing private is in any of them."""
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    anon = TestClient(main.app)
    types = {".png": "image/png", ".ico": "image/", ".webmanifest": "application/manifest+json"}
    for path in ICONS:
        r = anon.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(next(v for k, v in types.items() if path.endswith(k))), path
    assert anon.get("/", follow_redirects=False).status_code in (302, 303, 307, 401)  # the panel still needs a sign-in
    # no way round sign-in through the icons folder (dots encoded, so the client doesn't resolve them)
    for path in ("/icons/%2e%2e/index.html", "/icons/..%5cindex.html", "/icons/%2e%2e/api/health"):
        assert anon.get(path, follow_redirects=False).status_code == 404, path


def test_a_view_only_login_gets_the_icons_and_no_more(viewer):
    friend, _main, _ = viewer
    for path in ICONS:
        assert friend.get(path).status_code == 200, path
    assert friend.get("/icons/%2e%2e/api/viewers", follow_redirects=False).status_code == 404


BACKGROUNDS = ["/media/bg/nebula.webm", "/media/bg/nebula.mp4", "/media/bg/nebula.webp", "/media/bg/bright.webp",
               "/media/bg/blue.webp"]


def test_the_background_art_loads_before_signing_in(monkeypatch):
    """The owner's round4 nebula (2026-10-01): the sign-in page shows it too, so static/media/bg is public like the
    icons. It holds art only, its own types are sent (a slim image's OS table may lack them), and it's no way round
    sign-in."""
    import main

    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    anon = TestClient(main.app)
    types = {".webm": "video/webm", ".mp4": "video/mp4", ".webp": "image/webp"}
    for path in BACKGROUNDS:
        r = anon.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"] == next(v for k, v in types.items() if path.endswith(k)), path
    assert anon.get("/media/x.txt", follow_redirects=False).status_code in (302, 303, 307, 401)  # only bg/ is open
    for path in ("/media/bg/%2e%2e/%2e%2e/index.html", "/media/bg/..%5c..%5cindex.html",
                 "/media/bg/%2e%2e/%2e%2e/api/health"):
        assert anon.get(path, follow_redirects=False).status_code == 404, path


def test_a_view_only_login_gets_the_background_art(viewer):
    friend, _main, _ = viewer
    for path in BACKGROUNDS:
        assert friend.get(path).status_code == 200, path


# Owner-only art checks (backgrounds named by PICS, menu icons by NavIcons) live in test_private_blocks.py.


def test_the_plan_limits_switch_sits_beside_auto_compact():
    """The owner, 2026-10-02: "add a toggle for this weekly limit thing so i can turn it off. and a toggle next to auto
    compact. clearly labeled". It saves plan_limits_on on the PC, and stays hidden from view-only logins and while the
    PC's helper doesn't know the setting yet."""
    page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    auto, limits = page.index('id="ctxAuto"'), page.index('id="ctxLimits"')
    assert 0 < limits - auto < 400  # the next control after the auto-compact switch
    assert 'aria-label="Plan limits: slow down and pause Claude near my limit"' in page
    setter = page.split("async function setPlanLimits(el) {")[1].split("\n}")[0]
    assert "source: 'main-pc', key: 'plan_limits_on', value: el.checked" in setter
    assert "limBox.hidden = _viewer || !('plan_limits_on' in st);" in page


def test_the_manifest_names_icons_that_exist_and_both_pages_link_them():
    static = Path(__file__).parent / "static"
    manifest = json.loads((static / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "HomeShed" and manifest["start_url"] == "/" and manifest["display"] == "standalone"
    assert {i["purpose"] for i in manifest["icons"]} == {"any", "maskable"}
    for icon in manifest["icons"]:
        assert (static / icon["src"].lstrip("/")).is_file(), icon["src"]
    for page in ("index.html", "login.html"):
        head = (static / page).read_text(encoding="utf-8").split("</head>")[0]
        for link in ('rel="icon" href="/icons/icon-32.png"', 'rel="apple-touch-icon" href="/icons/apple-touch-icon.png"',
                     'rel="manifest" href="/manifest.webmanifest"'):
            assert link in head, (page, link)


def test_compact_now_relays_to_the_helper_owner_only(monkeypatch):
    """The owner, 2026-10-01: "how do i compact sessions memory". The panel only relays; the helper on the PC does every
    check. Bad ids never reach it; its refusals come back in its own words; a view-only login can't press it."""
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://helper")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    sid = "1205eade-9ee8-4445-ae1a-be4b2124e8a3"
    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        if len(seen) == 1:
            return httpx.Response(200, json={"typed": True, "note": "Typed /compact into Projects."})
        return httpx.Response(409, json={"detail": "Projects is working on something right now."})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    c = TestClient(main.app, headers=OWNER)
    assert c.post(f"/api/claude/sessions/{sid}/compact").json()["typed"] is True
    assert seen[0] == ("POST", "/context/compact", {"session": sid})
    r = c.post(f"/api/claude/sessions/{sid}/compact")
    assert r.status_code == 409 and "working on something" in r.json()["error"]
    assert c.post("/api/claude/sessions/not-a-session/compact").status_code == 400 and len(seen) == 2
    assert not any("compact" in path for path in main.VIEWER_ROUTES)


def test_announcements_relay_to_the_helper_owner_only(monkeypatch):
    """The owner, 2026-10-01: "a small announcement area, where i can send an announcement to all active agents". The
    panel relays only the message; the helper posts it and refuses what's empty or too long, in its own words."""
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://helper")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.method, req.url.path, json.loads(req.content or b"{}")))
        if req.method == "GET":
            return httpx.Response(200, json={"announcements": [], "max": 500})
        if not seen[-1][2]["message"]:
            return httpx.Response(400, json={"detail": "Write the announcement first."})
        return httpx.Response(200, json={"sent": "ab12cd34", "announcements": []})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    c = TestClient(main.app, headers=OWNER)
    assert c.get("/api/announce").json() == {"announcements": [], "max": 500}
    assert c.post("/api/announce", json={"message": "Restart in 10 minutes", "extra": 1}).json()["sent"]
    assert seen[-1] == ("POST", "/announce", {"message": "Restart in 10 minutes"})  # only the message
    r = c.post("/api/announce", json={"message": ""})
    assert r.status_code == 400 and r.json()["error"] == "Write the announcement first."
    assert not any("announce" in path for path in main.VIEWER_ROUTES)


def test_announcements_can_be_removed_from_the_history(monkeypatch, viewer):
    """The owner, 2026-10-01: "removeable entries or clear". The panel relays only ids or all; viewers can't."""
    import main

    monkeypatch.setattr(main, "GPU_SERVICE_URL", "http://helper")
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    seen = []
    real = httpx.AsyncClient

    def handler(req):
        seen.append((req.url.path, json.loads(req.content or b"{}")))
        return httpx.Response(200, json={"announcements": [], "max": 500})

    monkeypatch.setattr(main.httpx, "AsyncClient", lambda *a, **k: real(transport=httpx.MockTransport(handler)))
    c = TestClient(main.app, headers=OWNER)
    assert c.post("/api/announce/hide", json={"ids": ["ab12cd34"], "extra": 1}).status_code == 200
    assert seen[-1] == ("/announce/hide", {"ids": ["ab12cd34"]})
    assert c.post("/api/announce/hide", json={"all": True}).status_code == 200 and seen[-1][1] == {"all": True}
    assert c.post("/api/announce/hide", json=["x"]).status_code == 400
    friend, _main, _ = viewer
    assert friend.post("/api/announce/hide", json={"all": True}).status_code == 403


def test_pro_takes_only_a_pasted_key_and_is_the_owners_only(owner, viewer, monkeypatch):
    """HomeShed Pro in the public panel (2026-10-01): a key made on the Pro website, never a password; the tool server
    checks and keeps it. Only the key goes on; a refusal never echoes it; view-only logins can't reach any of it."""
    client, main = owner
    friend, _, _ = viewer
    monkeypatch.setattr(main, "MCP_BASE_URL", "http://mcp")
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.method, request.url.path, json.loads(request.content or b"null")))
        if request.method == "POST":
            return httpx.Response(400, json={"error": "The Pro website doesn't know that key: it was mistyped, or revoked."})
        return httpx.Response(200, json={"connected": False, "pro": False})
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    key = "mav_" + "Qx7" * 10
    r = client.post("/api/pro/connect", json={"key": key, "email": "k@example.com", "password": "dropped"})
    assert r.status_code == 400 and key not in r.text and "doesn't know that key" in r.json()["error"]
    assert sent[-1] == ("POST", "/pro/connect", {"key": key})
    assert client.get("/api/pro?fresh=true").json() == {"connected": False, "pro": False}
    assert sent[-1][:2] == ("GET", "/pro") and client.delete("/api/pro").status_code == 200
    assert friend.get("/api/pro").status_code == 403
    assert friend.post("/api/pro/connect", json={"key": key}).status_code == 403
    assert friend.delete("/api/pro").status_code == 403
    assert len(sent) == 3  # the viewer's requests never reached the tool server


def test_the_pages_load_nothing_from_other_sites():
    """No call-home (the prepper's release gate; UK GDPR): opening the panel or its sign-in page must not fetch anything
    from another site. Orbitron came from Google Fonts on every view until 2026-10-01, which sent each viewer's address
    to Google. Links someone chooses to click are fine; loads the browser makes by itself are not."""
    import re
    static = Path(__file__).parent / "static"
    loads = re.compile(r"""<(?:link|script|img|iframe|source|video|audio|embed|object)\b[^>]*\b(?:src|href|data)=["']"""
                       r"""(?:https?:)?//|@import\s+(?:url\()?\s*["']?(?:https?:)?//|url\(\s*["']?(?:https?:)?//""", re.I)
    for page in ("index.html", "login.html"):
        text = (static / page).read_text(encoding="utf-8")
        hits = [text.count("\n", 0, m.start()) + 1 for m in loads.finditer(text)]
        assert not hits, f"{page} loads from another site on lines {hits}"
    for name in ("orbitron-wght.ttf", "Orbitron-OFL.txt", "space-grotesk-latin-wght.woff2", "SpaceGrotesk-OFL.txt"):
        assert (static / "fonts" / name).is_file(), f"fonts/{name} must ship with the panel"



@pytest.mark.asyncio
async def test_the_panels_own_refreshes_ask_to_stay_quiet(monkeypatch):
    """A card's refresh isn't activity (the owner, 2026-10-02: the Release progress card's 20 s refresh was 192 of the
    last 200 recent calls). Only calls marked quiet send the header; actions the owner takes are still logged."""
    import main

    monkeypatch.setattr(main, "MCP_BASE_URL", "http://fake")
    monkeypatch.setattr(main, "MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(main, "MCP_HOST_HEADER", "fake")
    seen = []
    inner = _mock_mcp_transport(_rpc_result_response({"content": [{"type": "text", "text": '{"projects": []}'}]}))

    def handler(request):
        seen.append(request.headers.get("x-homelab-quiet"))
        return inner.handle_request(request)
    _patch_mcp_client(monkeypatch, main, httpx.MockTransport(handler))
    await main._mcp_call_tool("repo.progress", {}, quiet=True)
    assert seen == ["1", "1", "1"]
    seen.clear()
    await main._mcp_call_tool("observe.log", {"title": "t", "issue": "i"})
    assert seen == [None, None, None]


# ---------------------------------------------------------------------------
# Run page (to-do #50): run any tool by hand, and saved runs
# ---------------------------------------------------------------------------

def test_run_tool_passes_the_body_and_times_it(monkeypatch):
    import main
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    seen = []

    async def fake(name, args, timeout=15, quiet=False):
        seen.append((name, args, timeout))
        if name == "bad.tool":
            raise RuntimeError("signal.collect: no such topic")
        return {"done": True}
    monkeypatch.setattr(main, "_mcp_call_tool", fake)
    c = TestClient(main.app, headers=OWNER)
    r = c.post("/api/run", json={"tool": "signal.trend_list", "args": {"limit": 5}}).json()
    assert r["ok"] and r["result"] == {"done": True} and isinstance(r["ms"], int)
    assert seen[0] == ("signal.trend_list", {"limit": 5}, main.RUN_TIMEOUT_S)
    r = c.post("/api/run", json={"tool": "bad.tool"}).json()
    assert r["ok"] is False and "no such topic" in r["error"]
    for bad in ({"tool": "Signal.List"}, {"tool": "x"}, {"tool": "a.b", "args": [1]},
                {"tool": "a.b", "args": {"x": "y" * 40000}}, {"tool": "../etc"}):
        assert c.post("/api/run", json=bad).status_code == 400
    assert len(seen) == 2  # nothing bad reached the tool server


def test_saved_runs_add_replace_remove_and_cap(monkeypatch):
    import main
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    c = TestClient(main.app, headers=OWNER)
    assert c.post("/api/run/saved", json={"name": "Trends now", "tool": "signal.collect", "args": {"wait": False}}).json()["ok"]
    assert c.post("/api/run/saved", json={"name": "Trends now", "tool": "signal.collect"}).status_code == 409
    assert c.post("/api/run/saved", json={"name": "Trends now", "tool": "signal.trend_list", "replace": True}).json()["ok"]
    runs = c.get("/api/run/saved").json()["runs"]
    assert [(r["name"], r["tool"]) for r in runs] == [("Trends now", "signal.trend_list")]
    assert c.post("/api/run/saved", json={"name": "", "tool": "a.b"}).status_code == 400
    assert c.delete("/api/run/saved/Trends now").json()["ok"] and c.get("/api/run/saved").json()["runs"] == []
    assert c.delete("/api/run/saved/nope").status_code == 404
    monkeypatch.setattr(main, "SAVED_RUNS_MAX", 1)
    c.post("/api/run/saved", json={"name": "one", "tool": "a.b"})
    assert c.post("/api/run/saved", json={"name": "two", "tool": "a.b"}).status_code == 409


def test_run_page_is_owner_only(monkeypatch):
    import main
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", OWNER_PW)
    anon = TestClient(main.app)
    assert anon.post("/api/run", json={"tool": "a.b"}).status_code in (401, 403)
    assert anon.get("/api/run/saved").status_code in (401, 403)



def test_a_printify_publish_waits_in_the_approvals_bar_and_is_decided_through_the_tool_server(monkeypatch):
    """To-do #59: an app's request to publish a draft shows as "Publish to Etsy"; approving goes to the tool server,
    which checks the draft again before publishing."""
    c, main = _approval_sources(monkeypatch, todo=[], memory=[], proxmox=[],
                                printify=[{"id": "ab12cd34", "client": "shop-bot", "at": 400.0,
                                           "summary": 'Publish "Tee" (18 variants, 24.99) to your Etsy shop',
                                           "last_error": "Printify is busy"}])
    d = c.get("/api/approvals").json()
    assert d["items"][0]["kind"] == "printify" and d["items"][0]["error"] == "Printify is busy"
    calls = []

    async def fake_admin(method, path, body=None, timeout=10):
        calls.append((method, path))
        return main.JSONResponse({"id": "ab12cd34", "approved": True})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    assert c.post("/api/printify/waiting/ab12cd34/approve").json()["approved"] is True
    assert c.post("/api/printify/waiting/ab12cd34/publish").status_code == 400
    assert calls == [("POST", "/printify/pending/ab12cd34/approve")]
    assert "/api/printify/waiting" not in main.VIEWER_ROUTES
    page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    assert "printify: { label: 'Publish to Etsy'" in page


def test_photos_an_app_asks_to_upload_show_as_thumbnails_on_its_card(monkeypatch):
    """etsy.listing.update(images): the card shows the small copies made when it was asked, served owner-only."""
    item = {"id": "ab12cd34", "client": "owner", "at": 400.0, "action": "etsy",
            "summary": 'Set Etsy details on "Tee": photos: art.png, size.png',
            "payload": {"send": {"images": [{"image": "listing/art.png"}, {"image": "listing/size.png"}]},
                        "thumbs": [base64.b64encode(b"\xff\xd8thumb").decode(), None]}}
    c, main = _approval_sources(monkeypatch, todo=[], memory=[], proxmox=[], printify=[item])
    [card] = c.get("/api/approvals").json()["items"]
    assert card["action"] == "etsy" and card["images"] == [
        {"label": "art.png", "thumb": "/api/printify/waiting/ab12cd34/thumb/0"}, {"label": "size.png", "thumb": None}]
    r = c.get("/api/printify/waiting/ab12cd34/thumb/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == b"\xff\xd8thumb"
    assert c.get("/api/printify/waiting/ab12cd34/thumb/1").status_code == 404
    assert c.get("/api/printify/waiting/ab12cd34/thumb/12").status_code == 400
    assert "/api/printify/waiting" not in main.VIEWER_ROUTES


@pytest.mark.parametrize("still_waiting, status, words", [
    (False, 200, "no longer waiting"), (True, 504, "may still be working"),
])
def test_a_slow_approval_never_shows_as_failed_when_it_went_through(monkeypatch, still_waiting, status, words):
    """2026-10-08: four approvals clicked at once queued on the tool server; two cards said "502" though both edits
    went through. A timed-out decision now asks whether it still waits before answering."""
    c, main = _approval_sources(monkeypatch, todo=[], memory=[], proxmox=[], printify=[])
    seen = []

    async def fake_admin(method, path, body=None, timeout=10):
        seen.append((method, path, timeout))
        if method == "POST":
            return main.JSONResponse({"error": main.MCP_UNREACHABLE}, status_code=502)
        return main.JSONResponse({"items": [{"id": "ab12cd34"}] if still_waiting else []})
    monkeypatch.setattr(main, "_mcp_admin", fake_admin)
    r = c.post("/api/printify/waiting/ab12cd34/approve")
    assert r.status_code == status and words in json.dumps(r.json())
    assert seen[0] == ("POST", "/printify/pending/ab12cd34/approve", 60)  # a long wait first
