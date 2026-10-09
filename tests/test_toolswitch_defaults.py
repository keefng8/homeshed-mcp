import json

import pytest

import toolswitch as ts


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ts, "SWITCH_FILE", tmp_path / "disabled_tools.json")
    monkeypatch.setattr(ts, "_cache", (None, ts._EMPTY))
    monkeypatch.delenv("ENABLE_TOOLS", raising=False)
    ts._warned.clear()
    return tmp_path / "disabled_tools.json"


def test_the_13_write_and_code_tools_are_off_by_default():
    assert len(ts.DEFAULT_OFF) == 13
    for tool_id in ts.DEFAULT_OFF:
        assert ts.is_disabled(tool_id), tool_id


def test_read_and_other_tools_are_unaffected():
    for tool_id in ("git.status", "git.diff", "docker.container.list", "network.port_check", "memory.remember_fact"):
        assert not ts.is_disabled(tool_id)


def test_refusal_names_the_exact_setting_for_that_tool():
    msg = ts.why_disabled("git.push")
    assert "ENABLE_TOOLS=git.push" in msg and "git.commit" not in msg


def test_dev_refusal_warns_that_it_runs_code():
    assert "runs code" in ts.why_disabled("dev.python")


@pytest.mark.parametrize("value,on,off", [
    ("git.commit", ["git.commit"], ["git.push"]),
    ("git.*", ["git.commit", "git.push", "git.clone"], ["docker.container.stop", "dev.test"]),
    ("*", ["git.push", "docker.container.restart"], ["dev.python", "dev.test"]),       # * never includes dev.*
    ("*,dev.*", ["git.push", "dev.python"], []),
    ("dev.test", ["dev.test"], ["dev.python"]),
    ("  , git.commit ,, ", ["git.commit"], ["git.push"]),                                 # whitespace and empties
])
def test_enable_tools(monkeypatch, value, on, off):
    monkeypatch.setenv("ENABLE_TOOLS", value)
    for t in on:
        assert not ts.is_disabled(t), t
    for t in off:
        assert ts.is_disabled(t), t


def test_unknown_enable_tools_entry_is_ignored_with_one_warning(monkeypatch, caplog):
    monkeypatch.setenv("ENABLE_TOOLS", "not.a_tool,git.commit")
    assert not ts.is_disabled("git.commit")
    assert ts.is_disabled("git.push")
    ts.is_disabled("git.push")
    assert sum("not.a_tool" in r.message for r in caplog.records) == 1


def test_owner_switch_off_wins_over_enable_tools(isolated, monkeypatch):
    monkeypatch.setenv("ENABLE_TOOLS", "git.*")
    isolated.write_text(json.dumps({"disabled": ["git.push"]}), encoding="utf-8")
    assert ts.is_disabled("git.push") and "switched off by the owner" in ts.why_disabled("git.push")
    assert not ts.is_disabled("git.commit")


def test_set_enabled_turns_a_default_off_tool_on_and_back_off(isolated):
    assert ts.set_enabled("git.commit", True) is True
    assert not ts.is_disabled("git.commit")
    assert json.loads(isolated.read_text()) == {"disabled": [], "enabled": ["git.commit"]}
    assert ts.set_enabled("git.commit", True) is False                     # no change, no write
    assert ts.set_enabled("git.commit", False) is True
    assert ts.is_disabled("git.commit")
    assert json.loads(isolated.read_text()) == {"disabled": ["git.commit"], "enabled": []}


def test_old_file_without_enabled_key_keeps_working(isolated):
    isolated.write_text(json.dumps({"disabled": ["web.read"]}), encoding="utf-8")
    assert ts.is_disabled("web.read") and ts.is_disabled("git.commit") and not ts.is_disabled("git.status")


def test_corrupt_file_fails_open_but_never_enables_a_default_off_tool(isolated):
    isolated.write_text("{not json", encoding="utf-8")
    assert not ts.is_disabled("web.read")
    assert ts.is_disabled("git.commit")
