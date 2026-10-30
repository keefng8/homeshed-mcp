"""Settings > System > Set up (setup_routes.py, 2026-10-30): the checks, the prompts (never a token) and the link of
another PC's folder to a project. The tool server is faked."""
import base64

import httpx
import pytest
from fastapi.testclient import TestClient

PW = "test-owner-pw"
PLAN = []  # what the fake tool server answers to /pro/helper-plan
OWNER = {"Authorization": "Basic " + base64.b64encode(f"x:{PW}".encode()).decode()}


@pytest.fixture
def panel(tmp_path, monkeypatch):
    import main
    for name, file in (("PANEL_SETTINGS_FILE", "s.json"), ("OWNER_FILE", "o.json"), ("VIEWERS_FILE", "v.json"),
                       ("REVOKED_FILE", "r.json"), ("SESSION_SECRET_FILE", "secret")):
        monkeypatch.setattr(main, name, tmp_path / file)
    monkeypatch.setattr(main, "DASHBOARD_PASSWORD", PW)
    monkeypatch.setattr(main, "_failures", {})
    monkeypatch.setattr(main, "PANEL_DATA_DIR", tmp_path / "data" / "panel")
    monkeypatch.setattr(main, "MEMORY_CORE_BASE_URL", "")
    monkeypatch.setattr(main, "GPU_SERVICE_URL", "")
    sent = []
    plan = PLAN
    plan[:] = [{"pro": False, "reason": "The helper PC plan comes with HomeShed Pro."}]  # a test may set the Pro answer

    def handler(req):
        sent.append((req.method, req.url.path, req.content))
        if req.url.path == "/projects" and req.method == "POST":
            return httpx.Response(200, json={"path": "/home/me/app", "name": "app", "agent": "dir:d:/work/app"})
        if req.url.path == "/projects":
            return httpx.Response(200, json={"projects": []})
        if req.url.path == "/pro/helper-plan":
            return httpx.Response(200, json=plan[0])
        if req.url.path == "/local-ai/backends":
            return httpx.Response(200, json={"backends": [{"name": "nvidia", "cloud": True, "configured": True}]})
        return httpx.Response(200, json={})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: real(*a, **{**kw, "transport": httpx.MockTransport(handler)}))
    return TestClient(main.app, headers=OWNER), sent, main


def test_the_checks_say_what_is_ready_and_give_prompts_without_a_token(panel):
    client, _, main = panel
    steps = {s["id"]: s for s in client.get("/api/setup").json()["steps"]}
    assert steps["memory"]["status"] == "ready" and "built-in" in steps["memory"]["detail"]
    assert steps["model"]["status"] == "optional" and "Install Ollama from https://ollama.com (ask me" in steps["model"]["prompt"]  # a cloud model isn't local
    # the helper plan is Pro's (the owner, 2026-10-30): on the free plan, no prompt, and it says so
    assert steps["helper"]["status"] == "pro" and "prompt" not in steps["helper"]
    assert "comes with HomeShed Pro" in steps["helper"]["detail"]
    other = steps["other-pc"]
    assert other["link"] and [p["label"] for p in other["prompts"]] == ["1. On this PC", "2. On the other PC"]
    every = " ".join([s.get("prompt", "") for s in steps.values()] + [p["text"] for p in other["prompts"]])
    assert main.MCP_AUTH_TOKEN not in every and "HOMESHED_TOKEN" in every and "never" in every.lower()


def test_a_pro_member_gets_the_plan_as_a_prompt_for_their_own_ai(panel):
    client, _, main = panel
    PLAN[0] = {"pro": True, "plan": "# The helper plan\nGET /health ...\n"}
    helper = {s["id"]: s for s in client.get("/api/setup").json()["steps"]}["helper"]
    assert helper["status"] == "optional" and helper["prompt"].endswith("# The helper plan\nGET /health ...\n")
    assert "Ask me before installing anything" in helper["prompt"]


def test_linking_another_pcs_folder_registers_it_on_the_tool_server(panel):
    client, sent, _ = panel
    r = client.post("/api/setup/link", json={"folder": "/home/me/app", "project": "d:/work/app", "name": "My App"})
    assert r.status_code == 200 and r.json()["linked"]["agent"] == "dir:d:/work/app"
    method, path, body = sent[-1]
    assert (method, path) == ("POST", "/projects") and b'"agent":"dir:d:/work/app"' in body.replace(b" ", b"")
    assert b'"name":"my-app"' in body.replace(b" ", b"")
    assert client.post("/api/setup/link", json={"folder": "", "project": "x"}).status_code == 400


def test_a_view_only_login_cannot_see_the_setup(panel):
    client, _, _ = panel
    assert TestClient(client.app).get("/api/setup").status_code in (401, 403)
