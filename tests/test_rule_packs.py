"""rule_packs + `homeshed-mcp packs` / `pro` (2026-10-01): installing a pack never touches Claude Code's settings;
`packs on` does, after asking, with a backup (the first one kept), changing only its own entry, and never a file it
can't read. Every pack is validated before it's saved. Claude Code's settings here are always a temp file
(CLAUDE_SETTINGS); nothing touches the real ones."""
import copy
import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

import guard_engine
import mavis_pro
import rule_packs as rp
import vault
from test_guard_engine import PACK


@pytest.fixture(autouse=True)
def place(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "PACKS", tmp_path / "data" / "packs")
    monkeypatch.setattr(rp, "MODES", tmp_path / "data" / "guard-modes.json")
    monkeypatch.setattr(rp, "ENGINE_COPY", tmp_path / "data" / "guard_engine.py")
    monkeypatch.setenv("CLAUDE_SETTINGS", str(tmp_path / "claude" / "settings.json"))
    return tmp_path


def settings(place) -> dict:
    return json.loads((place / "claude" / "settings.json").read_text(encoding="utf-8"))


def ours(data, event="PreToolUse") -> list:
    return [e for e in data.get("hooks", {}).get(event, []) if rp._ours(e)]


def ours_everywhere(data) -> dict:
    return {event: ours(data, event) for event in rp.EVENTS}


# --- packs -------------------------------------------------------------------------------------------------------------
def test_a_pro_pack_is_checked_then_saved_and_installing_never_touches_claude_code(place, monkeypatch):
    monkeypatch.setattr(mavis_pro, "pack", lambda slug: copy.deepcopy(PACK))
    out = rp.install("test-pack")
    assert out["installed"]["slug"] == "test-pack" and out["hook_on"] is False
    assert [p["slug"] for p in rp.installed()] == ["test-pack"]
    assert not (place / "claude" / "settings.json").exists()


@pytest.mark.parametrize("served, words", [
    ({**PACK, "guards": [{**PACK["guards"][0], "kind": "hook", "script": "x.py"}]}, "failed its checks"),
    ({**PACK, "slug": "another-pack"}, "doesn't match"),
    (None, "arrived empty"),
])
def test_a_pack_that_fails_its_checks_is_never_saved(place, monkeypatch, served, words):
    monkeypatch.setattr(mavis_pro, "pack", lambda slug: copy.deepcopy(served))
    with pytest.raises(rp.PackError, match=words):
        rp.install("test-pack")
    assert rp.installed() == []


def test_pro_refusals_and_bad_names_come_back_as_plain_messages(monkeypatch):
    def refuse(slug):
        raise mavis_pro.MavisProError("HomeShed Pro isn't connected here: paste your Pro key on the Pro page.")
    monkeypatch.setattr(mavis_pro, "pack", refuse)
    with pytest.raises(rp.PackError, match="isn't connected"):
        rp.install("test-pack")
    with pytest.raises(rp.PackError, match="isn't a pack name"):
        rp.install("../etc")


def test_a_docker_install_goes_through_its_server(place, monkeypatch):
    seen = []

    def handler(req):
        seen.append((req.url.path, req.headers.get("authorization")))
        return httpx.Response(200, json={"pack": PACK} if req.url.path.endswith("test-pack") else
                              {"configured": True, "available": [{"slug": "test-pack", "title": "Test pack"}], "error": None})
    real = httpx.Client
    monkeypatch.setattr(httpx, "get", lambda url, **k: real(transport=httpx.MockTransport(handler)).get(url, **k))
    assert rp.install("test-pack", "http://homeshed:8765", "owner-token")["installed"]["slug"] == "test-pack"  # secret-scan: allow (fake)
    assert rp.offered("http://homeshed:8765", "owner-token")["available"][0]["slug"] == "test-pack"
    assert seen == [("/pro/packs/test-pack", "Bearer owner-token"), ("/pro/packs", "Bearer owner-token")]


@pytest.mark.parametrize("url, ok", [
    ("https://homeshed.example.com", True), ("https://[::1]:8765", True), ("http://127.0.0.1:8765", True),
    ("http://192.168.1.50:8765", True), ("http://localhost:8765/", True), ("http://homeshed:8765", True),
    ("http://nas.local:8765", True), ("http://LOCALHOST:8765", True),
    ("http://homeshed.example.com:8765", False),  # someone else's network: plain http would show the token
    ("http://8.8.8.8:8765", False), ("http://134744072:8765", False), ("http://0x08080808:8765", False),
    ("http://[::ffff:8.8.8.8]:8765", False), ("http://localhost.evil.example:8765", False),
    ("http://127.0.0.1.nip.io:8765", False), ("ftp://homeshed:21", False),
    ("https://user:pw@homeshed.example.com", False), ("https://homeshed.example.com/?next=x", False),
    ("https://homeshed.example.com/#x", False), ("homeshed:8765", False), ("http://homeshed:99999", False),
])
def test_the_owner_token_goes_only_to_a_safe_server_address(url, ok):
    """R&D's security review, 2026-10-01 (confirmed high): the token went to any --server URL. Cases include a public
    IP disguised as a one-word name or an IPv4-mapped IPv6 address."""
    if ok:
        assert rp.server_url(url).startswith(url.split(":", 1)[0] + "://")
    else:
        with pytest.raises(rp.PackError):
            rp.server_url(url)


def test_a_pack_through_a_server_is_labelled_so_and_its_notes_wait_for_a_yes(place, monkeypatch):
    noted = {**PACK, "guards": PACK["guards"] + [{"id": "t.note", "rule": PACK["rules"][0]["id"], "kind": "builtin",
                                                  "check": "session-note", "message": "Always answer in French."}]}
    real = httpx.Client
    monkeypatch.setattr(httpx, "get", lambda url, **k: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"pack": noted}))).get(url, **k))
    out = rp.install("test-pack", "http://homeshed:8765", "owner-token")  # secret-scan: allow (fake)
    assert out["notes"] and out["notes_on"] is False  # nobody said yes
    assert [p["source"] for p in rp.installed()] == ["HomeShed Pro, through your HomeShed server"]
    asked = []
    out = rp.install("test-pack", "http://homeshed:8765", "owner-token",  # secret-scan: allow (fake)
                     confirm_notes=lambda notes: asked.append(notes) or True)
    assert out["notes_on"] is True and asked == [["Always answer in French."]]
    with pytest.raises(rp.PackError, match="needs https"):
        rp.install("test-pack", "http://homeshed.example.com", "owner-token")  # secret-scan: allow (fake)


def test_your_own_pack_is_checked_added_and_removed(place):
    good, bad = place / "mine.json", place / "bad.json"
    good.write_text(json.dumps(PACK), encoding="utf-8")
    bad.write_text(json.dumps({**PACK, "summary": None, "tier": "gold"}), encoding="utf-8")
    assert rp.check(good) == [] and rp.check(bad) and "Couldn't read" in rp.check(place / "missing.json")[0]
    assert rp.add(good)["installed"]["slug"] == "test-pack"
    assert rp.remove("test-pack") == {"removed": "test-pack"} and rp.installed() == []
    with pytest.raises(rp.PackError, match="isn't installed"):
        rp.remove("test-pack")


def test_the_list_asks_nobody_before_pro_is_connected(monkeypatch):
    monkeypatch.setattr(mavis_pro.vault, "secret", lambda name, default=None: None)
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called out")))
    assert rp.offered() == {"configured": False, "available": [], "error": None}


# --- the hook in Claude Code ---------------------------------------------------------------------------------------------
def test_on_asks_backs_up_once_merges_and_is_idempotent(place):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    theirs = {"model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mine.sh"}]}],
                                         "Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]}}
    path.write_text(json.dumps(theirs), encoding="utf-8")
    asked = []
    assert rp.turn_on(confirm=lambda e, p: asked.append(e) or False)["reason"] == "declined"
    assert json.loads(path.read_text()) == theirs and asked                    # declined: untouched
    res = rp.turn_on(confirm=lambda e, p: True)
    data = settings(place)
    assert res["changed"] and ours_everywhere(data) == {e: [x] for e, x in rp.hook_entries().items()}
    assert data["model"] == "opus" and data["hooks"]["Stop"][0] == theirs["hooks"]["Stop"][0]
    assert data["hooks"]["PreToolUse"][0] == theirs["hooks"]["PreToolUse"][0]  # theirs first, untouched
    assert json.loads(Path(res["backup"]).read_text()) == theirs              # the backup is the file from before
    assert rp.turn_on(confirm=lambda e, p: True)["reason"] == "already on" and len(ours(settings(place))) == 1
    stale = settings(place)
    stale["hooks"]["PreToolUse"][-1]["hooks"][0]["command"] = '"/old/python" -I -S "/old/guard_engine.py"'
    path.write_text(json.dumps(stale), encoding="utf-8")
    rp.turn_on(confirm=lambda e, p: True)                                       # an old entry is replaced, not doubled
    assert ours(settings(place)) == [rp.hook_entry()]
    assert json.loads(Path(res["backup"]).read_text()) == theirs              # and the first backup is kept


def test_on_makes_the_settings_file_when_there_is_none_and_copies_the_engine(place):
    assert rp.turn_on()["changed"] and ours(settings(place)) == [rp.hook_entry()]
    assert rp.ENGINE_COPY.read_text(encoding="utf-8") == Path(guard_engine.__file__).read_text(encoding="utf-8")
    entries = rp.hook_entries()
    assert list(entries) == ["PreToolUse", "UserPromptSubmit", "SessionStart", "Stop"]
    assert all(e["hooks"][0]["timeout"] == rp.HOOK_TIMEOUT_S and e["hooks"][0]["command"] == rp.hook_command()
               for e in entries.values())
    assert [e for e, x in entries.items() if "matcher" in x] == ["PreToolUse"]  # the others fire on every prompt/stop
    command = entries["PreToolUse"]["hooks"][0]["command"]
    assert "\\" not in command and " -I -S " in command  # forward slashes, isolated and site-free


@pytest.mark.parametrize("tool, watched", [
    ("Bash", True), ("PowerShell", True), ("Edit", True), ("NotebookEdit", True), ("SendMessage", True),
    ("Agent", True), ("mcp__homeshed__git_push", True), ("BashOutput", False), ("ListAgents", False), ("Read", True), ("ReadMcpResourceTool", False),
])
def test_the_matcher_watches_only_the_tools_a_guard_can(tool, watched):
    """Claude Code tests it as an unanchored JavaScript regex (its hooks docs); Python's re.search behaves the same
    for this pattern."""
    import re
    assert bool(re.search(rp.MATCHER, tool)) is watched


@pytest.mark.parametrize("text", ["{not json", "[]", '{"hooks": []}', '{"hooks": {"PreToolUse": {}}}',
                                  '{"hooks": {"Stop": "x"}}'])
def test_a_settings_file_it_cant_read_safely_is_never_edited(place, text):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(text, encoding="utf-8")
    res = rp.turn_on(confirm=lambda e, p: True)
    assert res["reason"] == "unreadable" and res["entries"] == rp.hook_entries() and path.read_text() == text
    assert rp.turn_off()["reason"] == "unreadable" and path.read_text() == text


def test_an_older_hook_with_only_the_tool_call_entry_is_brought_up_to_date(place):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"PreToolUse": [rp.hook_entry()]}}), encoding="utf-8")
    rp.copy_engine()
    assert rp.hook_state() == {"on": True, "current": False, "why": "the hook is from an older HomeShed or another install"}
    assert rp.turn_on()["changed"] and rp.hook_state()["current"] is True
    assert ours_everywhere(settings(place)) == {e: [x] for e, x in rp.hook_entries().items()}


def test_off_removes_only_ours_and_tidies_up(place):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]}}), encoding="utf-8")
    rp.turn_on()
    assert rp.turn_off()["changed"] and settings(place) == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}]}}
    assert rp.turn_off()["reason"] == "already off"
    rp.turn_on()
    path.write_text(json.dumps({"hooks": {"PreToolUse": [rp.hook_entry()]}}), encoding="utf-8")
    rp.turn_off()
    assert settings(place) == {}


def test_hook_state_says_when_packs_on_is_needed_again(place, monkeypatch):
    assert rp.hook_state() == {"on": False, "current": False, "why": "off"}
    rp.turn_on()
    assert rp.hook_state()["current"] is True
    rp.ENGINE_COPY.write_text('ENGINE_VERSION = "0"\n', encoding="utf-8")  # an older copy, after an upgrade
    assert "older" in rp.hook_state()["why"]
    rp.copy_engine()
    monkeypatch.setattr(rp, "python_for_hook", lambda: "/gone/python")
    data = settings(place)
    data["hooks"]["PreToolUse"][-1] = rp.hook_entry()
    (place / "claude" / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    assert "no longer exists" in rp.hook_state()["why"]


# --- the command line --------------------------------------------------------------------------------------------------
@pytest.fixture
def cli(place, monkeypatch):
    import cli as c
    monkeypatch.setattr(vault, "VAULT_FILE", place / "data" / "vault" / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", place / "data" / "vault" / "audit.jsonl")
    monkeypatch.setattr(vault, "KEY_FILE", place / "data" / "vault" / "vault.key")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.delenv("HOMESHED_URL", raising=False)
    monkeypatch.setattr(mavis_pro, "STATE_FILE", str(place / "data" / "mavis_pro.json"))
    mavis_pro._cache.clear()
    return c


def test_packs_on_yes_then_list_then_off(cli, place, capsys):
    assert cli.main(["packs", "on", "--yes"]) == 0 and ours(settings(place))
    assert "The hook is on" in capsys.readouterr().out
    assert cli.main(["packs"]) == 0
    out = capsys.readouterr().out
    assert "No rule packs installed yet." in out and "The hook is on in Claude Code." in out and "pro connect" in out
    assert cli.main(["packs", "off"]) == 0 and not ours(settings(place))


def test_packs_install_without_pro_says_how_to_connect_and_calls_nobody(cli, capsys, monkeypatch):
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called out")))
    assert cli.main(["packs", "install", "token-saver"]) == 1
    assert "homeshed-mcp pro connect" in capsys.readouterr().out and rp.installed() == []


def test_packs_on_without_a_keyboard_asks_and_changes_nothing(cli, place, capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # a script or a pipe: the question can't be answered
    assert cli.main(["packs", "on"]) == 0 and not (place / "claude" / "settings.json").exists()
    assert "Nothing was changed." in capsys.readouterr().out


def test_pro_connect_makes_the_local_vault_key_and_keeps_the_key_encrypted(cli, place, capsys, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda url, **k: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"client": {"name": "Laptop"}}))).get(url, **k))
    key = "mav_" + "Qx7" * 10
    monkeypatch.setattr(sys, "stdin", io.StringIO(key + "\n"))
    assert cli.main(["pro", "connect", "--key-stdin"]) == 0
    assert 'Connected with the key "Laptop". Pro is active.' in capsys.readouterr().out
    assert vault.KEY_FILE.is_file() and vault.secret("MAVIS_PRO_TOKEN") == key
    assert key not in (place / "data" / "vault" / "vault.json").read_text(encoding="utf-8")
    assert cli.main(["pro", "disconnect"]) == 0 and vault.secret("MAVIS_PRO_TOKEN") is None


def test_pro_status_without_a_key_says_how_to_connect_and_calls_nobody(cli, capsys, monkeypatch):
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called out")))
    assert cli.main(["pro"]) == 1 and "pro connect" in capsys.readouterr().out


# --- R&D's security review ---------------------------------------------------------------------------------------------
def test_a_runaway_pattern_the_lint_cant_see_is_refused_by_the_timed_trial(place, monkeypatch):
    """^(a|a)*$ has no repeat inside its group, so the lint passes it; on "aaaa...!" it backtracks for ever."""
    monkeypatch.setattr(rp, "TRIAL_S", 1.0)
    monkeypatch.setattr(rp, "TRIAL_EACH_S", 0.2)
    slow = copy.deepcopy(PACK)
    slow["guards"][0]["match_any"] = [r"^(a|a)*$"]
    slow["guards"][0]["examples"] = {"block": ["aaaa"], "allow": ["bbbb"]}
    assert guard_engine.validate_pack(slow) == []  # the lint alone lets it through
    file = place / "slow.json"
    file.write_text(json.dumps(slow), encoding="utf-8")
    assert any("can run for ever" in p for p in rp.check(file))
    with pytest.raises(rp.PackError, match="can run for ever"):
        rp.add(file)
    assert rp.installed() == []


def test_where_a_pack_came_from_is_kept_by_the_installer_never_in_the_pack(place, monkeypatch):
    """R&D's ENGINE 3 review: the index (packs/.installed.json) holds each pack's source and hash."""
    monkeypatch.setattr(mavis_pro, "pack", lambda slug: {**copy.deepcopy(PACK), "_source": "pro", "_x": 1})
    rp.install("test-pack")
    saved = (rp.PACKS / "test-pack.json").read_bytes()
    assert not any(k.startswith("_") for k in json.loads(saved))                    # nothing of the engine's is saved
    assert guard_engine.read_index(rp.PACKS)["test-pack"] == {"source": "pro", "sha256": guard_engine.file_hash(saved)}
    assert rp.installed()[0]["source"] == "HomeShed Pro"
    file = place / "mine.json"
    file.write_text(json.dumps(PACK), encoding="utf-8")
    rp.add(file)
    assert guard_engine.read_index(rp.PACKS)["test-pack"]["source"] == "file"
    assert rp.installed()[0]["source"] == "added from a file"
    (rp.PACKS / "test-pack.json").write_text(json.dumps({**PACK, "summary": "edited by hand"}), encoding="utf-8")
    assert rp.installed()[0]["source"] == "unverified"
    rp.remove("test-pack")
    assert "test-pack" not in guard_engine.read_index(rp.PACKS)


NOTE_PACK = {**PACK, "guards": PACK["guards"] + [{"id": "a.note", "rule": "R1", "kind": "builtin", "check": "session-note",
                                                  "message": "Explain each step in plain words."}],
             "rules": [{**PACK["rules"][0], "enforced_by": PACK["rules"][0]["enforced_by"] + ["a.note"]}]}


@pytest.mark.parametrize("answer, on", [("y\n", True), ("\n", False)])
def test_adding_a_pack_with_a_session_note_shows_it_and_asks(cli, place, capsys, monkeypatch, answer, on):
    file = place / "noted.json"
    file.write_text(json.dumps(NOTE_PACK), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", io.StringIO(answer))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    assert cli.main(["packs", "add", str(file)]) == 0
    out = capsys.readouterr().out
    assert '"Explain each step in plain words."' in out and ("stays off" in out) is not on
    assert rp.installed()[0]["notes_on"] is on
    cli.main(["packs"])
    assert "Tells Claude each session" in capsys.readouterr().out


@pytest.mark.parametrize("text, words", [('{"a": NaN}', "Couldn't read"), ("x" * (guard_engine.MAX_PACK_BYTES + 1), "too big")],
                         ids=["nan", "too-big"])  # short ids: pytest puts the id in an environment variable
def test_pack_files_are_strict_json_and_size_capped(place, text, words):
    file = place / "pack.json"
    file.write_text(text, encoding="utf-8")
    assert words in rp.check(file)[0]


def test_on_and_off_touch_only_the_marked_entry(place):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    lookalike = {"hooks": [{"type": "command", "command": "python /my/own/guard_engine.py --strict"}]}  # not ours
    path.write_text(json.dumps({"hooks": {"PreToolUse": [lookalike]}}), encoding="utf-8")
    rp.turn_on()
    assert rp.MARKER in rp.hook_command().split() and lookalike in settings(place)["hooks"]["PreToolUse"]
    rp.turn_off()
    assert settings(place)["hooks"]["PreToolUse"] == [lookalike]


def test_a_settings_file_that_changes_meanwhile_is_left_alone(place, monkeypatch):
    path = place / "claude" / "settings.json"
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")
    real = rp._read_settings

    def then_claude_code_writes(p):
        data, digest = real(p)
        p.write_text('{"model": "sonnet"}', encoding="utf-8")  # changed between our read and our write
        return data, digest
    monkeypatch.setattr(rp, "_read_settings", then_claude_code_writes)
    assert rp.turn_on()["reason"] == "changed meanwhile"
    assert json.loads(path.read_text()) == {"model": "sonnet"}


def test_windows_without_git_bash_gets_powershell_syntax(monkeypatch):
    monkeypatch.setattr(rp.sys, "platform", "win32")
    monkeypatch.setattr(rp, "git_bash", lambda: None)
    hook = rp.hook_entry()["hooks"][0]
    assert hook["shell"] == "powershell" and hook["command"].startswith('& "')
    monkeypatch.setattr(rp, "git_bash", lambda: "C:/Program Files/Git/bin/bash.exe")
    hook = rp.hook_entry()["hooks"][0]
    assert "shell" not in hook and hook["command"].startswith('"')


def test_the_exact_hook_command_runs_from_a_folder_with_a_space(tmp_path, monkeypatch):
    """A user name with a space (R&D's review): every path is quoted, and the command works in bash as Claude Code
    runs it (Git Bash on Windows)."""
    import shutil as sh
    import subprocess
    bash = rp.git_bash() if sys.platform == "win32" else sh.which("bash")
    if not bash:
        pytest.skip("no bash here")
    home = tmp_path / "Jane Doe" / "data"
    monkeypatch.setattr(rp, "PACKS", home / "packs")
    monkeypatch.setattr(rp, "MODES", home / "guard-modes.json")
    monkeypatch.setattr(rp, "ENGINE_COPY", home / "guard_engine.py")
    monkeypatch.setattr(rp, "_powershell", lambda: False)
    rp.copy_engine()
    (home / "packs" / "test-pack.json").write_text(json.dumps(PACK), encoding="utf-8")
    event = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}})
    r = subprocess.run([bash, "-c", rp.hook_command()], input=event, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pro_disconnect_deletes_the_machine_key_once_the_vault_is_empty(cli, capsys, monkeypatch):
    real = httpx.Client
    monkeypatch.setattr(mavis_pro.httpx, "get", lambda url, **k: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"client": {"name": "Laptop"}}))).get(url, **k))
    monkeypatch.setattr(sys, "stdin", io.StringIO("mav_" + "Qx7" * 10 + "\n"))
    cli.main(["pro", "connect", "--key-stdin"])
    assert vault.KEY_FILE.is_file()
    cli.main(["pro", "disconnect"])
    assert not vault.KEY_FILE.exists() and "vault key is deleted" in capsys.readouterr().out


def test_doctor_reports_rule_packs(cli, place, capsys):
    report = cli.diagnose()
    assert report["packs"]["state"] == "optional" and report["packs"]["installed"] == 0
    rp.turn_on()
    rp.ENGINE_COPY.write_text('ENGINE_VERSION = "0"\n', encoding="utf-8")
    report = cli.diagnose()
    assert report["packs"]["state"] == "broken" and report["broken"] >= 1
