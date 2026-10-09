"""Image providers (tools/image/providers.py): the catalogue, selection rules, every remote adapter against a fake
API (httpx.MockTransport: no request leaves the process), the job model, and that no key ever shows up in a result,
an error or a saved file. Keys here are dummies."""
from __future__ import annotations

import asyncio
import base64
import importlib
import json
import sys

import httpx
import pytest

import runtime_settings as rs
import tools.image.generate as ig
import tools.image.providers as prov
import tools.image.providers_list as pl
import tools.image.status as ist

PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngbytes" * 10
B64 = base64.b64encode(PNG).decode()
KEYS = {"OPENAI_API_KEY": "sk-dummy-openai-0000", "STABILITY_API_KEY": "sk-dummy-stability-0000",  # secret-scan: allow
        "REPLICATE_API_TOKEN": "r8_dummyreplicate0000", "FAL_KEY": "dummy-fal-0000:0000",  # secret-scan: allow
        "TOGETHER_API_KEY": "dummy-together-0000", "XAI_API_KEY": "xai-dummy-0000"}  # secret-scan: allow


class SyncThread:
    def __init__(self, target, args=(), **_):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class FakeAPI:
    """Answers like each provider's real endpoint (shapes from their docs, 2026-10)."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.status: dict[str, int] = {}
        self.replicate_polls = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        host, path = req.url.host, req.url.path
        for k, code in self.status.items():
            if k in str(req.url):
                return httpx.Response(code, json={"error": {"message": f"bad thing https://x.example/?t=1 {KEYS['OPENAI_API_KEY']}"}})
        if host == "api.openai.com" and path == "/v1/images/generations":
            return httpx.Response(200, json={"created": 1, "data": [{"b64_json": B64}]})
        if host == "api.x.ai" and path == "/v1/images/generations":
            return httpx.Response(200, json={"data": [{"b64_json": B64}]})
        if host == "api.together.ai" and path == "/v1/images/generations":
            return httpx.Response(200, json={"id": "x", "model": "m", "object": "list",
                                             "data": [{"index": 0, "b64_json": B64, "type": "b64_json"}]})
        if host == "api.stability.ai" and path.startswith("/v2beta/stable-image/generate/"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if host == "api.replicate.com" and path.endswith("/predictions") and req.method == "POST":
            return httpx.Response(201, json={"id": "abc123xyz0", "status": "processing", "output": None})
        if host == "api.replicate.com" and path == "/v1/predictions/abc123xyz0":
            self.replicate_polls += 1
            return httpx.Response(200, json={"id": "abc123xyz0", "status": "succeeded",
                                             "output": ["https://replicate.delivery/xezq/out-0.png"]})
        if host == "replicate.delivery":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if host == "fal.run":
            return httpx.Response(200, json={"images": [{"url": f"data:image/png;base64,{B64}", "width": 1, "height": 1}],
                                             "seed": 7})
        return httpx.Response(404, json={"error": "no route"})


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.delenv("IMAGE_DEFAULT_PROVIDER", raising=False)
    monkeypatch.delenv("IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_AI_DECIDE_BASE_URL", raising=False)
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IMAGE_OUTPUT_DIR", str(tmp_path / "images"))
    monkeypatch.setenv("IMAGE_USAGE_FILE", str(tmp_path / "image_daily.json"))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    fake = FakeAPI()
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(prov.threading, "Thread", SyncThread)
    monkeypatch.setattr(prov, "_POLL_S", 0)
    monkeypatch.setattr(prov, "_jobs", {})
    monkeypatch.setattr(ig, "ask", lambda *a, **k: {"text": "x" * 5})  # too short: original prompt kept
    return fake


BY_KEY = {p["key"]: name for name, p in prov.PROVIDERS.items() if p["key"]}


def _on(provider, cap="10"):
    """What the owner does on the Settings page: switch a paid provider on and give it a daily cap."""
    rs.update({prov.provider_setting(provider): True, prov.cap_setting(provider): cap})


def _key(monkeypatch, *names, enable=True):
    for n in names:
        monkeypatch.setenv(n, KEYS[n])
        if enable:
            _on(BY_KEY[n])


def test_every_remote_provider_has_an_adapter_and_licence_metadata():
    assert set(prov.ADAPTERS) == set(prov.PROVIDERS) - {prov.LOCAL}
    for p in prov.PROVIDERS.values():
        assert p["default_model"] in p["models"]
        for m in p["models"].values():
            assert set(m["license"]) == {"name", "commercial_use", "terms_url"}
            assert m["license"]["commercial_use"] in (True, False, "check")
            assert m["license"]["terms_url"].startswith("https://")


def test_list_shows_configured_yes_no_only(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    _key(monkeypatch, "OPENAI_API_KEY", enable=False)
    out = pl.providers_list()
    by = {r["provider"]: r for r in out["providers"]}
    assert by["fal"]["configured"] is True and by["xai"]["configured"] is False
    assert by["openai"]["configured"] is True  # a key, but switched off: still not usable
    assert by["local-sdcpp"]["configured"] is True and by["local-sdcpp"]["paid"] is False
    assert by["fal"]["key_name"] == "FAL_KEY" and out["local_status"] == "unreachable"
    assert by["fal"]["enabled"] is True and by["fal"]["usable"] is True and by["fal"]["daily_cap"] == 10
    assert by["openai"]["enabled"] is False and by["openai"]["usable"] is False and "switched off" in by["openai"]["why_not"]
    qwen = next(m for m in by["local-sdcpp"]["models"] if m["model"] == "qwen-image-2.1")
    flux = next(m for m in by["local-sdcpp"]["models"] if m["model"] == "flux.1-schnell")
    assert qwen["commercial_use"] is False and flux["commercial_use"] is True and flux["installed"] is False
    assert KEYS["FAL_KEY"] not in json.dumps(out)


@pytest.mark.parametrize("data,want", [
    (PNG, "image/png"), (b"\xff\xd8\xff\xe0" + b"j" * 20, "image/jpeg"), (b"RIFF\0\0\0\0WEBPVP8 ", "image/webp"),
    (b"", None), (b"\x89PNG", None), (b"RIFF\0\0\0\0WAVEfmt ", None)])
def test_sniff_mime(data, want):
    assert prov.sniff_mime(data) == want


def test_jpeg_bytes_labelled_png_are_saved_as_jpg(api, monkeypatch, tmp_path):
    """KB-0066: xAI sent JPEG bytes while saying PNG; the file was named .png and a client refused it."""
    jpeg = b"\xff\xd8\xff\xe0" + b"j" * 50
    monkeypatch.setitem(prov.ADAPTERS, "xai", lambda *a: (jpeg, "image/png"))
    job = {"job_id": "abcdef012345", "provider": "xai", "model": "grok-imagine-image", "prompt": "p", "width": 512,
           "height": 512, "seed": 1, "steps": 0, "started_at": 0.0}
    prov._run(job, "k")
    assert job["status"] == "done" and job["saved_as"].endswith(".jpg") and job["mime"] == "image/jpeg"
    assert (tmp_path / "images" / job["saved_as"]).read_bytes() == jpeg


def test_list_commercial_only(api):
    out = pl.providers_list(commercial_only=True)
    models = [m for r in out["providers"] for m in r["models"]]
    assert models and all(m["commercial_use"] is True for m in models)
    assert "stability" not in {r["provider"] for r in out["providers"]}  # "check" terms aren't commercial-only
    assert "xai" in {r["provider"] for r in out["providers"]}  # Enterprise terms: the customer owns the output


def test_list_reads_local_install_state(api, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")

    class R:
        status_code = 200

        def json(self):
            return {"models": {"qwen-image-2.1": {"ready": True}, "flux.1-schnell": {"ready": True}}}
    monkeypatch.setattr(prov.httpx, "get", lambda *a, **k: R())
    by = {r["provider"]: r for r in pl.providers_list()["providers"]}
    assert all(m["installed"] for m in by["local-sdcpp"]["models"])
    assert "installed" not in by["fal"]["models"][0]


@pytest.mark.parametrize("provider,key,host", [
    ("openai", "OPENAI_API_KEY", "api.openai.com"), ("xai", "XAI_API_KEY", "api.x.ai"),
    ("together", "TOGETHER_API_KEY", "api.together.ai"), ("stability", "STABILITY_API_KEY", "api.stability.ai"),
    ("replicate", "REPLICATE_API_TOKEN", "api.replicate.com"), ("fal", "FAL_KEY", "fal.run")])
def test_each_adapter_saves_the_image_with_its_licence(api, monkeypatch, tmp_path, provider, key, host):
    _key(monkeypatch, key)
    out = ig.generate("a lighthouse at dusk", provider=provider, seed=5)
    assert out["provider"] == provider and out["paid"] is True and len(out["job_id"]) == 12
    done = ist.status(out["job_id"])
    assert done["status"] == "done", done.get("error")
    saved = tmp_path / "images" / done["saved_as"]
    assert saved.read_bytes() == PNG
    side = json.loads((tmp_path / "images" / f"{out['job_id']}.json").read_text())
    assert side["license_info"]["terms_url"].startswith("https://") and side["provider"] == provider
    first = api.calls[0]
    assert first.url.host == host
    auth = first.headers["authorization"]
    assert auth == (f"Key {KEYS[key]}" if provider == "fal" else f"Bearer {KEYS[key]}")
    blob = json.dumps(done) + json.dumps(side)
    assert KEYS[key] not in blob


def test_request_shapes(api, monkeypatch):
    _key(monkeypatch, *KEYS)
    for p in ("openai", "xai", "together", "fal", "replicate", "stability"):
        ig.generate("a cat", provider=p, width=1024, height=576, seed=3)
    bodies = {c.url.host: c for c in api.calls if c.method == "POST"}
    o = json.loads(bodies["api.openai.com"].content)
    assert o == {"model": "gpt-image-1-mini", "prompt": "a cat", "n": 1, "size": "1536x1024", "output_format": "png"}
    assert json.loads(bodies["api.x.ai"].content)["response_format"] == "b64_json"
    t = json.loads(bodies["api.together.ai"].content)
    assert t["model"] == "black-forest-labs/FLUX.1-schnell" and t["response_format"] == "base64" and t["seed"] == 3
    f = json.loads(bodies["fal.run"].content)
    assert bodies["fal.run"].url.path == "/fal-ai/flux/schnell" and f["sync_mode"] is True
    assert f["image_size"] == {"width": 1024, "height": 576}
    r = bodies["api.replicate.com"]
    assert r.url.path == "/v1/models/black-forest-labs/flux-schnell/predictions"
    assert r.headers["prefer"] == "wait=60" and json.loads(r.content)["input"]["aspect_ratio"] == "16:9"
    s = bodies["api.stability.ai"]
    assert s.url.path == "/v2beta/stable-image/generate/core" and s.headers["accept"] == "image/*"
    assert b'name="aspect_ratio"' in s.content and b"16:9" in s.content


def test_replicate_polls_until_done(api, monkeypatch):
    _key(monkeypatch, "REPLICATE_API_TOKEN")
    out = ig.generate("a cat", provider="replicate")
    assert ist.status(out["job_id"])["status"] == "done" and api.replicate_polls == 1


@pytest.mark.parametrize("code,hint", [(401, "check the API key"), (402, "out of credit"), (429, "rate limited")])
def test_provider_errors_are_scrubbed(api, monkeypatch, code, hint):
    _key(monkeypatch, "OPENAI_API_KEY")
    api.status["api.openai.com"] = code
    out = ig.generate("a cat", provider="openai")
    done = ist.status(out["job_id"])
    assert done["status"] == "failed" and f"http {code}" in done["error"] and hint in done["error"]
    assert KEYS["OPENAI_API_KEY"] not in done["error"] and "https://" not in done["error"]


def test_timeouts_and_network_errors(api, monkeypatch):
    _key(monkeypatch, "XAI_API_KEY")

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(boom))
    done = ist.status(ig.generate("a cat", provider="xai")["job_id"])
    assert done["error"] == "xai didn't answer in time"


def test_an_image_on_an_unexpected_host_is_not_fetched(api, monkeypatch):
    _key(monkeypatch, "XAI_API_KEY")

    def handler(req):
        if req.url.host == "api.x.ai":
            return httpx.Response(200, json={"data": [{"url": "https://evil.example/i.png"}]})
        raise AssertionError("fetched an unexpected host")
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(handler))
    done = ist.status(ig.generate("a cat", provider="xai")["job_id"])
    assert "unexpected host" in done["error"]


def test_paid_provider_without_key_is_refused_by_name(api):
    _on("stability")
    with pytest.raises(ig.ImageError, match="add STABILITY_API_KEY"):
        ig.generate("a cat", provider="stability")
    assert api.calls == []


def test_unknown_provider_and_model(api):
    with pytest.raises(ig.ImageError, match="unknown image provider"):
        ig.generate("a cat", provider="midjourney")
    with pytest.raises(ig.ImageError, match="has no model"):
        ig.generate("a cat", provider="local-sdcpp", model="sdxl")


def test_remote_is_never_used_unless_asked(api, monkeypatch):
    _key(monkeypatch, *KEYS)
    with pytest.raises(ig.ImageError, match="IMAGE_BASE_URL"):  # local default, which isn't configured here
        ig.generate("a cat")
    assert api.calls == []


def test_image_default_provider_setting(api, monkeypatch):
    _key(monkeypatch, "TOGETHER_API_KEY")
    monkeypatch.setenv("IMAGE_DEFAULT_PROVIDER", "together")
    assert ig.generate("a cat")["provider"] == "together"
    monkeypatch.setenv("IMAGE_DEFAULT_PROVIDER", "nope")
    with pytest.raises(ig.ImageError, match="IMAGE_DEFAULT_PROVIDER"):
        ig.generate("a cat")


def test_require_commercial_refuses_non_commercial_and_check(api, monkeypatch):
    _key(monkeypatch, "STABILITY_API_KEY")
    with pytest.raises(ig.ImageError, match="NON-COMMERCIAL"):
        ig.generate("a cat", provider="local-sdcpp", require_commercial=True)
    with pytest.raises(ig.ImageError, match="not confirmed for commercial use"):
        ig.generate("a cat", provider="stability", require_commercial=True)


def test_require_commercial_auto_picks_the_cheapest_configured(api, monkeypatch):
    _key(monkeypatch, "OPENAI_API_KEY", "FAL_KEY", "XAI_API_KEY")
    out = ig.generate("a cat", require_commercial=True)
    assert out["provider"] == "fal" and out["auto_selected"] is True and out["paid"] is True
    assert out["commercial_use"] is True


def test_require_commercial_prefers_free_local_flux_when_installed(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    monkeypatch.setattr(prov, "local_ready", lambda: {"flux.1-schnell": True})
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return {"job_id": "abcdef012345", "status": "running", "license": "Apache-2.0"}
    monkeypatch.setattr(ig.httpx, "post", lambda url, **kw: (sent.update(kw["json"]), R())[1])
    out = ig.generate("a cat", require_commercial=True)
    assert sent["model"] == "flux.1-schnell" and out["provider"] == "local-sdcpp" and out["paid"] is False
    assert out["auto_selected"] is True


def test_require_commercial_with_nothing_ready_names_the_keys(api):
    with pytest.raises(ig.ImageError, match="FAL_KEY"):
        ig.generate("a cat", require_commercial=True)


def test_busy_limit(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    monkeypatch.setattr(prov, "_jobs", {f"{i:012x}": {"status": "running", "started_at": 0} for i in range(3)})
    with pytest.raises(ig.ImageError, match="already running"):
        ig.generate("a cat", provider="fal")


def test_upscale_is_local_only(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    with pytest.raises(ig.ImageError, match="upscale"):
        ig.generate("a cat", provider="fal", upscale=True)


def test_status_survives_a_restart_via_the_sidecar(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    jid = ig.generate("a cat", provider="fal")["job_id"]
    monkeypatch.setattr(prov, "_jobs", {})
    out = ist.status(jid, include_image=True)
    assert out["status"] == "done" and base64.b64decode(out["image_b64"]) == PNG


def test_status_of_an_unknown_id_falls_through_to_the_gpu_service(api):
    with pytest.raises(ig.ImageError, match="IMAGE_BASE_URL"):
        ist.status("0123456789ab")


# --- switches (owner only, Control Panel > Settings > Image generation) --------------------------------------------

def test_defaults_local_on_every_paid_provider_off_with_cap_zero():
    for name, p in prov.PROVIDERS.items():
        assert prov.SETTINGS[prov.provider_setting(name)]["default"] is (not p["paid"])
        for mid in p["models"]:
            assert prov.SETTINGS[prov.model_setting(name, mid)]["default"] is True
        if p["paid"]:
            assert prov.SETTINGS[prov.cap_setting(name)]["default"] == "0"
    assert set(prov.SETTINGS) <= set(rs.SCHEMA)  # the Settings page draws them from /settings
    assert all(spec["group"] == "Image generation" for spec in prov.SETTINGS.values())


@pytest.mark.parametrize("provider,key", [(n, p["key"]) for n, p in prov.PROVIDERS.items() if p["key"]])
def test_a_stored_key_alone_never_enables_a_paid_provider(api, monkeypatch, provider, key):
    _key(monkeypatch, key, enable=False)
    with pytest.raises(ig.ImageError, match="switched off"):
        ig.generate("a cat", provider=provider)
    assert api.calls == []


def test_switched_on_but_cap_zero_is_refused(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY", enable=False)
    _on("fal", cap="0")
    with pytest.raises(ig.ImageError, match="daily image cap is 0"):
        ig.generate("a cat", provider="fal")
    assert api.calls == []


def test_daily_cap_stops_paid_calls(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY", enable=False)
    _on("fal", cap="5")
    for _ in range(5):
        ig.generate("a cat", provider="fal")
    with pytest.raises(ig.ImageError, match="reached today's cap of 5"):
        ig.generate("a cat", provider="fal")
    assert len([c for c in api.calls if c.url.host == "fal.run"]) == 5
    by = {r["provider"]: r for r in pl.providers_list()["providers"]}
    assert by["fal"]["used_today"] == 5 and by["fal"]["usable"] is False


def test_the_count_resets_on_a_new_day(api, monkeypatch, tmp_path):
    _key(monkeypatch, "FAL_KEY", enable=False)
    _on("fal", cap="5")
    (tmp_path / "image_daily.json").write_text(json.dumps({"date": "2000-01-01", "counts": {"fal": 99}}))
    assert prov.used_today("fal") == 0
    ig.generate("a cat", provider="fal")
    assert prov.used_today("fal") == 1


def test_a_switched_off_model_is_refused(api, monkeypatch):
    _key(monkeypatch, "OPENAI_API_KEY")
    rs.update({prov.model_setting("openai", "gpt-image-2"): False})
    with pytest.raises(ig.ImageError, match="openai/gpt-image-2 is switched off"):
        ig.generate("a cat", provider="openai", model="gpt-image-2")
    assert ig.generate("a cat", provider="openai")["model"] == "gpt-image-1-mini"


def test_switching_off_local_qwen_refuses_the_default(api, monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")
    rs.update({prov.model_setting("local-sdcpp", "qwen-image-2.1"): False})
    with pytest.raises(ig.ImageError, match="qwen-image-2.1 is switched off"):
        ig.generate("a cat")
    rs.update({prov.provider_setting("local-sdcpp"): False})
    with pytest.raises(ig.ImageError, match="local-sdcpp is switched off"):
        ig.generate("a cat", model="flux.1-schnell")


def test_a_disabled_default_provider_is_refused_not_replaced(api, monkeypatch):
    _key(monkeypatch, "TOGETHER_API_KEY", enable=False)
    monkeypatch.setenv("IMAGE_DEFAULT_PROVIDER", "together")
    with pytest.raises(ig.ImageError, match="switched off.*IMAGE_DEFAULT_PROVIDER"):
        ig.generate("a cat")
    assert api.calls == []


def test_auto_selection_skips_disabled_and_capped_providers(api, monkeypatch):
    _key(monkeypatch, "TOGETHER_API_KEY", enable=False)   # key, but switched off
    _key(monkeypatch, "FAL_KEY", enable=False)
    _on("fal", cap="0")                                     # on, but no daily allowance
    _key(monkeypatch, "REPLICATE_API_TOKEN")               # on, cap 10
    out = ig.generate("a cat", require_commercial=True)
    assert out["provider"] == "replicate" and out["auto_selected"] is True


def test_auto_selection_skips_a_switched_off_local_flux(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")
    monkeypatch.setattr(prov, "local_ready", lambda: {"flux.1-schnell": True})
    rs.update({prov.model_setting("local-sdcpp", "flux.1-schnell"): False})
    assert ig.generate("a cat", require_commercial=True)["provider"] == "fal"


def test_auto_selection_with_everything_off_names_the_switches(api, monkeypatch):
    _key(monkeypatch, *KEYS, enable=False)
    with pytest.raises(ig.ImageError, match="Settings > Image generation"):
        ig.generate("a cat", require_commercial=True)
    assert api.calls == []


def test_a_broken_settings_store_means_paid_stays_off(api, monkeypatch):
    _key(monkeypatch, "FAL_KEY")

    def broken(key):
        raise RuntimeError("store unreadable")
    monkeypatch.setattr(rs, "get", broken)
    assert prov.provider_enabled("fal") is False and prov.provider_enabled("local-sdcpp") is True


def test_bad_cap_values_are_rejected_by_the_store(api):
    with pytest.raises(ValueError):
        rs.update({prov.cap_setting("fal"): "1000000"})
    with pytest.raises(ValueError):
        rs.update({prov.provider_setting("fal"): "yes"})


def test_client_tokens_can_read_the_switches_but_not_change_them(monkeypatch):
    """The switches change only through /settings, which RequestGuard keeps to the owner token; a client token
    reaches /mcp (where image.providers.list shows them) and nothing else."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    server = importlib.import_module("server")
    monkeypatch.setattr(server.clients, "resolve", lambda auth: ("friend", "ok"))

    def status(path, method, auth):
        seen = {}

        async def app(scope, receive, send):
            seen["passed"] = True

        async def send(message):
            if message["type"] == "http.response.start":
                seen["status"] = message["status"]
        scope = {"type": "http", "path": path, "method": method,
                 "headers": [(b"host", b"localhost"), (b"authorization", auth.encode())]}
        asyncio.run(server.RequestGuard(app, "owner-secret", ["localhost"])(scope, None, send))
        return "passed" if seen.get("passed") else seen.get("status")

    assert status("/settings", "PUT", "Bearer hlc_client") == 403
    assert status("/settings", "GET", "Bearer hlc_client") == 403
    assert status("/mcp", "POST", "Bearer hlc_client") == "passed"
    assert status("/settings", "PUT", "Bearer owner-secret") == "passed"


@pytest.mark.parametrize("w,h,ratio", [(1024, 1024, "1:1"), (1344, 768, "16:9"), (576, 1024, "9:16"), (1024, 768, "4:3"),
                                       (768, 1152, "2:3"), (1024, 576, "16:9")])
def test_xai_gets_the_nearest_aspect_ratio(w, h, ratio):
    """R&D, 2026-10-07: square requests came back 16:9 because no shape was sent."""
    assert prov.xai_aspect(w, h) == ratio
