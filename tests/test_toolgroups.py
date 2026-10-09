"""toolgroups.py and setup's group picker (the owner's go for v0.1.0, 2026-10-06: the AI only sees the optional tool
groups this install uses). Case list drafted by local_ai.ask (qwen3-coder-30b), written against the real behaviour.
conftest.py points TOOL_GROUPS_FILE at a temp folder."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli
import manifest
import toolgroups


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_GROUPS_FILE", str(tmp_path / "tool_groups.json"))
    for var in ("ENABLE_TOOLS", "DOCKER_HOST", "NTFY_BASE_URL", "PROXMOX_API_TOKEN", "LOCAL_AI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "tool_groups.json"


def test_every_group_is_real_categories_and_the_core_is_in_none():
    categories = {m["id"].split(".", 1)[0] for m in manifest.load_manifests()}
    for name, (what, cats, _) in toolgroups.GROUPS.items():
        assert re.fullmatch(r"[a-z][a-z-]+", name) and what and set(cats) <= categories, name
    for core in ("memory", "reasoning", "git", "web", "files", "network", "bugs", "observe", "repo", "secrets", "system"):
        assert core not in toolgroups.CATEGORY_GROUP, core


def test_the_shipped_file_lists_only_public_groups():
    """The prepper (2026-10-06): an install's own groups and settings come from private_settings, never the public file."""
    extra = pytest.importorskip("private_settings").TOOL_GROUPS_EXTRA
    src = (Path(__file__).resolve().parents[1] / "toolgroups.py").read_text(encoding="utf-8")
    words = {n for n in extra if n not in toolgroups.PUBLIC} | {s for _, _, settings in extra.values() for s in settings}
    assert words and not [w for w in words if w in src]


def test_nothing_is_hidden_without_an_answer_or_with_a_broken_file(fresh):
    assert toolgroups.hidden() == set() and not toolgroups.is_hidden("docker")
    fresh.write_text("{broken", encoding="utf-8")
    assert toolgroups.hidden() == set()
    fresh.write_text(json.dumps({"hidden": "docker"}), encoding="utf-8")  # wrong shape: a string, not a list
    assert toolgroups.hidden() == {g for g in "docker" if g in toolgroups.GROUPS}  # i.e. nothing


def test_a_saved_answer_hides_its_categories_and_unknown_names_are_dropped(fresh):
    toolgroups.save({"shops", "docker", "no-such-group"})
    assert json.loads(fresh.read_text(encoding="utf-8")) == {"hidden": ["docker", "shops"]}
    assert toolgroups.is_hidden("etsy") and toolgroups.is_hidden("printify") and toolgroups.is_hidden("docker")
    assert not toolgroups.is_hidden("memory") and not toolgroups.is_hidden("proxmox")


def test_detected_groups_come_from_settings_and_probes():
    found = toolgroups.detected(lambda name: name in ("NTFY_BASE_URL", "PRINTIFY_API_TOKEN"), docker_ok=True,
                                model_found=True)
    assert found == {"notify", "shops", "docker", "local-ai"}
    assert toolgroups.detected(lambda name: False) == set()
    assert "dev" in toolgroups.detected(lambda name: False, dev_on=True)


def _args(**kw):
    return SimpleNamespace(**{"yes": False, "no": False, **kw})


def test_setup_without_a_keyboard_keeps_the_detected_groups(fresh, monkeypatch, capsys):
    monkeypatch.setenv("NTFY_BASE_URL", "https://ntfy.example")
    monkeypatch.setattr(cli, "_check", lambda req: ("optional", ""))  # no Docker socket here
    asked = []
    hidden = cli.pick_groups(_args(yes=True), model_found=False, answer=lambda q: asked.append(q) or "")
    assert asked == [] and "notify" not in hidden and "docker" in hidden and "dev" in hidden
    assert set(json.loads(fresh.read_text(encoding="utf-8"))["hidden"]) == hidden
    assert "Optional groups in use: notify" in capsys.readouterr().out


def test_at_a_keyboard_numbers_switch_groups_on_and_off(fresh, monkeypatch):
    monkeypatch.setattr(cli, "_check", lambda req: ("ready", ""))  # Docker found: ticked
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    names = list(toolgroups.GROUPS)
    docker, proxmox = names.index("docker") + 1, names.index("proxmox") + 1
    replies = iter([f"{docker} {proxmox} 99", "n"])  # untick Docker, tick Proxmox (99 isn't listed); a non-number keeps
    hidden = cli.pick_groups(_args(), model_found=True, answer=lambda q: next(replies))
    assert "docker" in hidden and "proxmox" not in hidden and "local-ai" not in hidden


def test_doctor_names_the_hidden_groups(fresh, monkeypatch, capsys):
    toolgroups.save({"proxmox"})
    report = cli.diagnose()
    assert report["hidden_groups"] == ["proxmox"] and report["hidden_tools"] == 9
    monkeypatch.setattr(cli, "diagnose", lambda: report)
    cli.cmd_doctor(SimpleNamespace(json=False))
    out = capsys.readouterr().out
    assert "9 tool(s) the AI doesn't see: proxmox" in out and "setup" in out


def test_a_hidden_group_is_missing_from_tools_list_over_stdio(tmp_path):
    """The real path (the prepper's VM round, 2026-10-06): the answer saved in DATA_DIR, then `serve` over stdio as an AI
    app starts it, then tools/list. The server gets DATA_DIR in its own environment, as an AI app's config passes it."""
    import os
    import subprocess
    import sys
    (tmp_path / "usage").mkdir()
    (tmp_path / "usage" / "tool_groups.json").write_text(json.dumps({"hidden": ["docker", "proxmox"]}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "TOOL_GROUPS"))}
    env["DATA_DIR"] = str(tmp_path)
    p = subprocess.Popen([sys.executable, str(Path(cli.__file__)), "serve"], cwd=str(tmp_path), env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8")
    try:
        def ask(msg):
            p.stdin.write(json.dumps(msg) + "\n")
            p.stdin.flush()
            return json.loads(p.stdout.readline()) if "id" in msg else None
        ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
        ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        names = [t["name"] for t in ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    finally:
        p.stdin.close()
        p.wait(timeout=30)
    assert names and "memory.recall_facts" in names
    assert not [n for n in names if n.split(".", 1)[0] in ("docker", "proxmox")]
