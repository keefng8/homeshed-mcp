"""runtime_settings (Settings page > AI models / Notifications) and where the tool server uses it.
Case list drafted by local_ai.ask (Qwen3-Coder-30B)."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import runtime_settings as rs

try:  # the owner's private_settings.py adds its own entries; the public copy has none (release v1)
    import private_settings
    PRIVATE = {key: spec["default"] for key, spec in private_settings.SCHEMA.items()}
except ImportError:
    PRIVATE = {}
try:  # the share tools bring their switch; a build without them has none (the public v1 copy leaves them out)
    from tools.share import SETTINGS as _SHARE_SETTINGS
    PRIVATE = {**PRIVATE, **{key: spec["default"] for key, spec in _SHARE_SETTINGS.items()}}
except ImportError:
    pass
try:  # the image provider switches come with the image tools
    from tools.image.providers import SETTINGS as _IMAGE_SETTINGS
    PRIVATE = {**PRIVATE, **{key: spec["default"] for key, spec in _IMAGE_SETTINGS.items()}}
except ImportError:
    pass
try:  # the language-model relay switches come with the llm tools
    from tools.llm.providers import SETTINGS as _LLM_SETTINGS
    PRIVATE = {**PRIVATE, **{key: spec["default"] for key, spec in _LLM_SETTINGS.items()}}
except ImportError:
    pass
try:  # the shop proxy switches come with the shop tools
    from tools.commerce import SETTINGS as _COMMERCE_SETTINGS
    PRIVATE = {**PRIVATE, **{key: spec["default"] for key, spec in _COMMERCE_SETTINGS.items()}}
except ImportError:
    pass
try:  # the to-do list's app route (off) comes with the to-do tools
    from tools.todo import SETTINGS as _TODO_SETTINGS
    PRIVATE = {**PRIVATE, **{key: spec["default"] for key, spec in _TODO_SETTINGS.items()}}
except ImportError:
    pass
PUBLIC = {"local_ai_allow_cloud": True, "ntfy_default_topic": "Alerts", "local_ai_base_url": "", "ntfy_base_url": "",
          "bugs_search_public": False}


@pytest.fixture(autouse=True)
def settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    monkeypatch.setitem(rs.SCHEMA["ntfy_default_topic"], "default", "Alerts")
    return tmp_path / "settings.json"


def test_defaults_when_missing_or_corrupt(settings_file):
    assert rs.values() == {**PUBLIC, **PRIVATE}
    settings_file.write_text("{corrupt")
    assert rs.get("local_ai_allow_cloud") is True


def test_update_saves_and_keeps_other_keys(settings_file):
    settings_file.write_text(json.dumps({"someone_else": 1}))
    assert rs.update({"local_ai_allow_cloud": False, "ntfy_default_topic": " Pings "})["ntfy_default_topic"] == "Pings"
    saved = json.loads(settings_file.read_text())
    assert saved == {"someone_else": 1, "local_ai_allow_cloud": False, "ntfy_default_topic": "Pings"}


@pytest.mark.parametrize("changes, says", [
    ({"nope": 1}, "Unknown setting"), ({"local_ai_allow_cloud": "no"}, "on or off"),
    ({"ntfy_default_topic": "has space"}, "letters, numbers"), ({"ntfy_default_topic": ""}, "letters, numbers"),
    ({}, "JSON object"),
])
def test_bad_changes_save_nothing(settings_file, changes, says):
    with pytest.raises(ValueError, match=says):
        rs.update(changes)
    assert not settings_file.exists()


def test_one_bad_value_blocks_the_whole_change(settings_file):
    with pytest.raises(ValueError):
        rs.update({"local_ai_allow_cloud": False, "ntfy_default_topic": "bad topic"})
    assert not settings_file.exists()


def test_a_saved_value_that_no_longer_fits_falls_back(settings_file):
    settings_file.write_text(json.dumps({"ntfy_default_topic": "bad topic!", "local_ai_allow_cloud": False}))
    assert rs.values() == {**PUBLIC, "local_ai_allow_cloud": False, **PRIVATE}


def test_file_is_reread_when_it_changes(settings_file):
    rs.update({"local_ai_allow_cloud": False})
    assert rs.get("local_ai_allow_cloud") is False
    settings_file.write_text(json.dumps({"local_ai_allow_cloud": True, "padding": "x" * 10}))
    assert rs.get("local_ai_allow_cloud") is True


def test_ask_keeps_everything_local_when_switched_off(monkeypatch):
    from tools.local_ai import ask as ask_mod

    rs.update({"local_ai_allow_cloud": False})
    cloud = {"name": "nemotron", "cloud": True, "configured": True}
    monkeypatch.setattr(ask_mod, "backends", lambda: [cloud])
    monkeypatch.setattr(ask_mod, "_find", lambda model, pool: cloud)
    with pytest.raises(ask_mod.LocalAIError, match="allow_cloud is False"):
        ask_mod.ask.__wrapped__("hi", model="nemotron") if hasattr(ask_mod.ask, "__wrapped__") else ask_mod.ask("hi", model="nemotron")


def test_notify_uses_the_setting_topic(monkeypatch):
    from tools.notify import send as send_mod

    rs.update({"ntfy_default_topic": "Pings"})
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"id": "x"}

    def fake_post(url, **kw):
        seen["url"] = url
        return Resp()

    monkeypatch.setenv("NTFY_BASE_URL", "http://ntfy.test")
    monkeypatch.setenv("NTFY_AUTH_TOKEN", "test-token")
    monkeypatch.setattr(send_mod.httpx, "post", fake_post)
    getattr(send_mod.send, "__wrapped__", send_mod.send)("hello")
    assert seen["url"] == "http://ntfy.test/Pings"
    getattr(send_mod.send, "__wrapped__", send_mod.send)("hello", topic="Other")
    assert seen["url"] == "http://ntfy.test/Other"   # a named topic still wins


@pytest.fixture
def server(monkeypatch):
    import importlib
    import sys

    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def test_settings_route(server):
    class Req:
        def __init__(self, method, body=None):
            self.method, self._body = method, body

        async def json(self):
            return self._body

    got = json.loads(asyncio.run(server.tool_settings(Req("GET"))).body)
    assert set(got["schema"]) == set(PUBLIC) | set(PRIVATE)
    ok = json.loads(asyncio.run(server.tool_settings(Req("PUT", {"local_ai_allow_cloud": False}))).body)
    assert ok["values"]["local_ai_allow_cloud"] is False
    bad = asyncio.run(server.tool_settings(Req("PUT", {"local_ai_allow_cloud": "maybe"})))
    assert bad.status_code == 400


# --- addresses an end user's setup changes (2026-09-29) ---

def test_address_uses_the_settings_page_first_then_env(monkeypatch, tmp_path):
    import runtime_settings as rs
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    monkeypatch.setenv("LOCAL_AI_BASE_URL", "http://env-host:8080/v1")
    assert rs.address("LOCAL_AI_BASE_URL") == "http://env-host:8080/v1"
    (tmp_path / "settings.json").write_text('{"local_ai_base_url": "http://my-pc:11434/v1"}', encoding="utf-8")
    assert rs.address("LOCAL_AI_BASE_URL") == "http://my-pc:11434/v1"
    assert rs.address("SOMETHING_ELSE") == ""


def test_address_settings_accept_empty_or_a_web_address_only():
    import pytest
    import runtime_settings as rs
    assert rs.validate("ntfy_base_url", "") == ""
    assert rs.validate("ntfy_base_url", " https://ntfy.sh ") == "https://ntfy.sh"
    for bad in ("ftp://x", "ntfy.sh", "http://", 5):
        with pytest.raises(ValueError):
            rs.validate("ntfy_base_url", bad)


def test_a_select_setting_only_takes_its_listed_options():
    pytest.importorskip("private_settings")  # the only select settings are the Studio's (not in the public copy)
    assert rs.validate("voice_natural_voice", "Puck") == "Puck"
    with pytest.raises(ValueError, match="choose one of the listed options"):
        rs.validate("voice_natural_voice", "Nobody")
    with pytest.raises(ValueError):
        rs.validate("voice_natural_model", "gpt-4")
