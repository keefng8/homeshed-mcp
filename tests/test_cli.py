"""cli.py, the homeshed-mcp command (release v1, 2026-09-29). Case list drafted by local_ai.ask (Qwen3-Coder-30B),
written against the real behaviour; model servers, the manifests and the server are faked."""
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # A temp folder as the working directory: `serve --http` reads ./.env, and the repo's own .env (real settings) must
    # never reach a test (2026-10-01).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # setup now saves the tool groups: its own file per test, so a saved answer can't hide tools in later tests
    monkeypatch.setenv("TOOL_GROUPS_FILE", str(tmp_path / "tool_groups.json"))
    # No test ever reaches this machine's real Claude Code: setup's tests give it a fake one.
    monkeypatch.setattr(cli, "_claude", lambda: None)
    for var in ("NTFY_BASE_URL", "ENABLE_TOOLS", "LOCAL_AI_BASE_URL", "LOCAL_AI_GATEWAY_URL"):
        monkeypatch.delenv(var, raising=False)
    # One pytest session at the public repo's root also loads panel/conftest.py, which sets these for the whole
    # process: doctor then saw an unreachable memory-core (the prepper, 2026-10-01).
    for var in ("MEMORY_CORE_BASE_URL", "MEMORY_CORE_BEARER", "MEMORY_SERVICE_ID", "MEMORY_USER_KEY", "MEMORY_TEAM_ID",
                "MEMORY_USER_ID", "MEMORY_AGENT_ID", "MCP_BASE_URL", "MCP_AUTH_TOKEN", "MCP_HOST_HEADER", "MCP_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "data"


# --- where the data lives -------------------------------------------------------------------------------------------
def test_default_data_dir_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert cli.default_data_dir() == tmp_path / "Local" / "homeshed-mcp"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert cli.default_data_dir() == Path.home() / "Library" / "Application Support" / "homeshed-mcp"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert cli.default_data_dir() == tmp_path / "xdg" / "homeshed-mcp"
    monkeypatch.delenv("XDG_DATA_HOME")
    assert cli.default_data_dir() == Path.home() / ".local" / "share" / "homeshed-mcp"


# --- serve ----------------------------------------------------------------------------------------------------------------
def test_no_command_serves_over_stdio_and_http_is_explicit(monkeypatch, data_dir):
    import server
    calls = []
    monkeypatch.setattr(server.mcp, "run", lambda transport: calls.append(("run", transport)))
    monkeypatch.setattr(server, "serve_http", lambda port=None: calls.append(("http", port)))
    assert cli.main([]) == 0
    assert cli.main(["serve", "--http", "--port", "9000"]) == 0
    assert calls == [("run", "stdio"), ("http", 9000)] and data_dir.is_dir()


# --- init -------------------------------------------------------------------------------------------------------------------
def test_init_writes_new_secrets_once_and_never_replaces_a_key(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("MCP_ALLOWED_HOSTS=localhost:8765\nVAULT_KEY=keep-me\n", encoding="utf-8")
    assert cli.main(["init", "--env-file", str(env)]) == 0
    lines = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())
    assert lines["VAULT_KEY"] == "keep-me" and lines["MCP_ALLOWED_HOSTS"] == "localhost:8765"
    token = lines["MCP_AUTH_TOKEN"]
    assert len(token) >= 40 and token in capsys.readouterr().out  # shown once
    assert cli.main(["init", "--env-file", str(env)]) == 0
    assert "nothing changed" in capsys.readouterr().out and token not in env.read_text(encoding="utf-8").replace(
        f"MCP_AUTH_TOKEN={token}", "")  # still exactly one token line


def test_init_fills_empty_keys_and_the_host_list(tmp_path, monkeypatch):
    """The prepper, 2026-09-30: the Docker path (init, then compose up) restart-looped: init wrote no MCP_ALLOWED_HOSTS,
    and a key left empty from .env.example counted as set, so no token was made either."""
    monkeypatch.delenv("MCP_PORT", raising=False)
    env = tmp_path / ".env"
    env.write_text("# from .env.example\nMCP_AUTH_TOKEN=\nOTHER=1\n", encoding="utf-8")
    assert cli.main(["init", "--env-file", str(env)]) == 0
    lines = env.read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith("#"))
    assert lines[0] == "# from .env.example" and lines[1].startswith("MCP_AUTH_TOKEN=") and len(values["MCP_AUTH_TOKEN"]) >= 40
    assert values["MCP_ALLOWED_HOSTS"] == "localhost:8765,127.0.0.1:8765" and values["OTHER"] == "1" and values["VAULT_KEY"]
    assert sum(line.startswith("MCP_AUTH_TOKEN=") for line in lines) == 1


def test_init_makes_a_working_vault_key(tmp_path):
    env = tmp_path / ".env"
    cli.main(["init", "--env-file", str(env)])
    key = dict(line.split("=", 1) for line in env.read_text(encoding="utf-8").splitlines())["VAULT_KEY"]
    from cryptography.fernet import Fernet
    assert Fernet(key.encode()).decrypt(Fernet(key.encode()).encrypt(b"x")) == b"x"


# --- setup ------------------------------------------------------------------------------------------------------------------
def test_setup_finds_a_local_model_and_prints_the_connect_command(monkeypatch, capsys):
    def fake_get(url, timeout=3.0):
        if url.startswith("http://localhost:11434"):
            return True, json.dumps({"data": [{"id": "llama3.2"}]})
        return False, ""
    monkeypatch.setattr(cli, "_get", fake_get)
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "Local model: Ollama at http://localhost:11434/v1, model llama3.2" in out
    assert ("claude mcp add homeshed-mcp -s user -e LOCAL_AI_BASE_URL=http://localhost:11434/v1 "
            "-e LOCAL_AI_MODEL=llama3.2 -- uvx homeshed-mcp") in out
    assert cli.main(["setup", "--model"]) == 0
    assert "claude mcp add" not in capsys.readouterr().out  # --model stops after the probe


def test_setup_without_a_model_still_explains(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (False, ""))
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "none found" in out and "claude mcp add homeshed-mcp -s user -- uvx homeshed-mcp" in out


class FakeClaude:
    """Stands in for `claude mcp add`: records each command, answers with the given exit code and text."""
    def __init__(self, code=0, said=""):
        self.calls, self.code, self.said = [], code, said

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return SimpleNamespace(returncode=self.code, stdout=self.said, stderr="")


def _setup_with(monkeypatch, fake, tty=False, answer=""):
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (url.startswith("http://localhost:11434"),
                                                              json.dumps({"data": [{"id": "llama3.2"}]})))
    monkeypatch.setattr(cli, "_claude", lambda: "C:/bin/claude.exe")
    monkeypatch.setattr(cli.subprocess, "run", fake)
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: tty))
    asked = []
    monkeypatch.setattr("builtins.input", lambda q: asked.append(q) or answer)
    return asked


def test_setup_asks_then_adds_it_to_claude_code_with_its_own_command(monkeypatch, capsys):
    fake = FakeClaude()
    asked = _setup_with(monkeypatch, fake, tty=True, answer="")  # Enter = yes
    assert cli.main(["setup"]) == 0
    assert asked == [cli.GROUPS_QUESTION, "Add homeshed-mcp to Claude Code now? [Y/n] "]  # tool groups come first
    assert fake.calls == [["C:/bin/claude.exe", "mcp", "add", "homeshed-mcp", "-s", "user",
                           "-e", "LOCAL_AI_BASE_URL=http://localhost:11434/v1", "-e", "LOCAL_AI_MODEL=llama3.2",
                           "--", "uvx", "homeshed-mcp"]]
    out = capsys.readouterr().out
    assert "Done: Claude Code has homeshed-mcp now" in out
    assert '"command": "uvx"' in out  # the other apps' settings are still printed, never written


@pytest.mark.parametrize("argv, tty, answer", [(["setup"], True, "n"), (["setup"], False, ""), (["setup", "--no"], True, "")])
def test_setup_changes_nothing_on_no_with_no_one_there_or_with_no(monkeypatch, capsys, argv, tty, answer):
    fake = FakeClaude()
    asked = _setup_with(monkeypatch, fake, tty=tty, answer=answer)
    assert cli.main(argv) == 0 and fake.calls == []
    assert len(asked) == (2 if answer == "n" else 0)  # the tool groups, then Claude Code; a script or --no: never asked
    assert "claude mcp add homeshed-mcp -s user" in capsys.readouterr().out  # the line to run by hand


def test_setup_yes_adds_without_asking_and_a_failure_says_so(monkeypatch, capsys):
    fake = FakeClaude()
    asked = _setup_with(monkeypatch, fake)
    assert cli.main(["setup", "--yes"]) == 0 and len(fake.calls) == 1 and asked == []
    fake.code, fake.said = 1, "MCP server homeshed-mcp already exists in user config"
    assert cli.main(["setup", "--yes"]) == 0
    assert "already has homeshed-mcp" in capsys.readouterr().out
    fake.said = "permission denied"
    assert cli.main(["setup", "--yes"]) == 1
    assert "didn't add it: permission denied" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["setup", "--yes", "--no"])  # one or the other


def test_setup_without_claude_code_only_prints(monkeypatch, capsys):
    fake = FakeClaude()
    asked = _setup_with(monkeypatch, fake, tty=True)
    monkeypatch.setattr(cli, "_claude", lambda: None)
    assert cli.main(["setup", "--yes"]) == 0 and fake.calls == [] and asked == []
    assert "claude mcp add homeshed-mcp -s user" in capsys.readouterr().out


# --- doctor -----------------------------------------------------------------------------------------------------------------
TOOLS = [{"id": "reasoning.solve", "requires": []},
         {"id": "notify.send", "requires": ["ntfy (NTFY_BASE_URL, NTFY_AUTH_TOKEN)"]},
         {"id": "git.commit", "requires": []}]


@pytest.fixture
def manifests(monkeypatch):
    import manifest
    monkeypatch.setattr(manifest, "load_manifests", lambda: TOOLS)


def test_doctor_on_a_new_install_is_calm(manifests, capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "Core tools      1 ready" in out and "Notifications" in out and "NTFY_BASE_URL=https://ntfy.sh" in out
    assert "git.commit" in out and "ENABLE_TOOLS=git.commit" in out  # write tools are shown as off
    assert out.rstrip().endswith("1 of 3 tools ready. Nothing is broken.")


def test_doctor_calls_a_configured_address_that_does_not_answer_broken(manifests, monkeypatch, capsys):
    monkeypatch.setenv("NTFY_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (False, ""))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "NTFY_BASE_URL=http://127.0.0.1:9 didn't answer" in out and "Fix:" in out and "1 problem(s)" in out
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (True, ""))
    assert cli.main(["doctor"]) == 0 and "2 of 3 tools ready" in capsys.readouterr().out


def test_doctor_json_carries_the_same_report(manifests, capsys):
    assert cli.main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["tools_total"] == 3 and report["tools_ready"] == 1 and report["write_tools_off"] == ["git.commit"]
    assert [g["name"] for g in report["groups"]] == ["Notifications"] and report["broken"] == 0


PUBLIC = "https://homeshed.example.test"
META = {"issuer": PUBLIC + "/", "authorization_endpoint": PUBLIC + "/authorize", "token_endpoint": PUBLIC + "/token",
        "registration_endpoint": PUBLIC + "/register", "client_id_metadata_document_supported": True}


@pytest.fixture
def oauth_store(monkeypatch, tmp_path):
    """A sign-in store in the test's temp folder: two signed-in apps (one twice), an expired one, one waiting sign-in."""
    import time as _time
    cli._prepare()
    import clients
    import oauth_server
    store = tmp_path / "oauth.json"
    monkeypatch.setattr(oauth_server, "STORE_FILE", store)
    monkeypatch.setattr(clients, "_load", lambda: {"clients": {}})
    now = _time.time()
    live = {"expires_at": now + 3600, "client_id": "c", "scopes": [], "grant": "g"}
    store.write_text(json.dumps({
        "refresh": {"h1": {**live, "app": "claude-ai"}, "h2": {**live, "app": "claude-ai"},
                    "h3": {**live, "app": "chatgpt"}, "h4": {**live, "app": "gone", "expires_at": now - 5}},
        "access": {"h5": {**live, "app": "claude-ai"}},
        "pending": {"p1": {"client_id": "https://claude.ai/oauth/x", "client_name": "Claude", "redirect_uri":
                           "https://claude.ai/cb", "status": "waiting", "at": now, "match": "123456"}}}),
        encoding="utf-8")
    return store


def test_doctor_says_oauth_is_off_and_gives_the_connector_steps(manifests, capsys):
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "Sign-in (OAuth) off (MCP_PUBLIC_URL not set)" in out
    assert "Add custom connector" in out and "MCP_PUBLIC_URL=https://" in out and "/mcp" in out


def test_doctor_reports_oauth_on_with_metadata_apps_and_pending_names_only(manifests, monkeypatch, capsys,
                                                                          oauth_store):
    monkeypatch.setenv("MCP_PUBLIC_URL", PUBLIC + "/")
    asked = []
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (asked.append(url) or True, ""))
    monkeypatch.setattr(cli, "_get_json", lambda url, timeout=5.0: META if url.endswith("authorization-server") else
                        {"resource": PUBLIC + "/mcp"} if "protected-resource" in url else None)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert PUBLIC + "/healthz" in asked
    assert f"on at {PUBLIC}/mcp, answers, sign-in metadata served" in out
    assert f"authorize {PUBLIC}/authorize" in out and f"resource {PUBLIC}/mcp" in out
    assert "2 app(s) signed in this way: chatgpt, claude-ai; 1 sign-in(s) waiting" in out
    assert "gone" not in out and "h1" not in out and "hso_" not in out and "123456" not in out  # no tokens, no codes
    assert cli.main(["doctor", "--json"]) == 0
    oauth = json.loads(capsys.readouterr().out)["oauth"]
    assert oauth["apps"] == ["chatgpt", "claude-ai"] and oauth["pending"] == 1 and oauth["state"] == "ready"
    assert "h1" not in json.dumps(oauth) and "expires_at" not in json.dumps(oauth)


def test_doctor_calls_oauth_broken_when_the_public_address_or_metadata_is_missing(manifests, monkeypatch, capsys):
    monkeypatch.setenv("MCP_PUBLIC_URL", PUBLIC)
    monkeypatch.setattr(cli, "_get_json", lambda url, timeout=5.0: None)
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (False, ""))
    assert cli.main(["doctor"]) == 1
    assert f"on, but {PUBLIC} didn't answer" in capsys.readouterr().out
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (True, ""))
    assert cli.main(["doctor"]) == 1
    assert "serves no sign-in metadata" in capsys.readouterr().out
    monkeypatch.setenv("MCP_PUBLIC_URL", "http://192.168.1.5:8765")
    assert cli.main(["doctor"]) == 1
    assert "isn't an https address" in capsys.readouterr().out


def test_doctor_reads_mcp_public_url_from_the_env_file(manifests, monkeypatch, tmp_path, capsys):
    (tmp_path / ".env").write_text(f"MCP_AUTH_TOKEN=not-printed\nMCP_PUBLIC_URL={PUBLIC}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (True, ""))
    monkeypatch.setattr(cli, "_get_json", lambda url, timeout=5.0: META)
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert f"on at {PUBLIC}/mcp" in out and "not-printed" not in out


def test_doctor_uses_plain_markers_where_emoji_cannot_print(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="ascii"))
    assert cli._marks() is cli.PLAIN
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    assert cli._marks() is cli.MARKS


def test_serve_http_reads_the_env_file_init_wrote_but_the_environment_wins(monkeypatch, tmp_path, data_dir):
    """The prepper's clean-VM test (2026-10-01): the README's `serve --http` stopped at "MCP_AUTH_TOKEN must be set"."""
    import os

    import server
    env = tmp_path / "settings.env"
    env.write_text("# made by init\nMCP_AUTH_TOKEN=from-file\nMCP_ALLOWED_HOSTS=localhost:8765\n", encoding="utf-8")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "already-set:1")
    seen = []
    monkeypatch.setattr(server, "serve_http", lambda port=None: seen.append(
        (os.environ.get("MCP_AUTH_TOKEN"), os.environ.get("MCP_ALLOWED_HOSTS"))))
    monkeypatch.setattr(server.mcp, "run", lambda transport: seen.append(("stdio", os.environ.get("MCP_AUTH_TOKEN"))))
    assert cli.main(["serve", "--http", "--env-file", str(env)]) == 0
    assert seen == [("from-file", "already-set:1")]
    monkeypatch.delenv("MCP_AUTH_TOKEN")
    (tmp_path / ".env").write_text("MCP_AUTH_TOKEN=from-the-folder\n", encoding="utf-8")  # the working folder's .env
    assert cli.main([]) == 0 and seen[-1] == ("stdio", None)  # stdio never reads it


# --- panel (the optional web panel, 2026-10-01) ----------------------------------------------------------------------------
@pytest.fixture
def panel(tmp_path, monkeypatch):
    """A stand-in panel folder, fastapi present, and uvicorn.run recorded instead of serving."""
    import types

    import uvicorn
    folder = tmp_path / "panel"
    folder.mkdir()
    (folder / "main.py").write_text("app = 'the panel app'\n", encoding="utf-8")
    monkeypatch.setattr(cli, "PANEL_DIR", folder)
    monkeypatch.setitem(sys.modules, "fastapi", types.ModuleType("fastapi"))
    monkeypatch.delitem(sys.modules, "main", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    for var in ("MCP_BASE_URL", "MCP_HOST_HEADER", "PANEL_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (True, ""))
    yield runs
    sys.path[:] = [p for p in sys.path if p != str(folder)]
    sys.modules.pop("main", None)


def test_panel_runs_on_this_machine_with_the_token_init_wrote(panel, tmp_path, data_dir, capsys):
    env = tmp_path / ".env"
    env.write_text("# made by init\nMCP_AUTH_TOKEN=tok-123\nVAULT_KEY=k\n", encoding="utf-8")
    assert cli.main(["panel", "--env-file", str(env), "--port", "9191", "--server", "http://127.0.0.1:8765/"]) == 0
    assert panel == [("the panel app", {"host": "127.0.0.1", "port": 9191, "log_level": "warning"})]
    import os
    assert os.environ["MCP_BASE_URL"] == "http://127.0.0.1:8765" and os.environ["MCP_HOST_HEADER"] == "127.0.0.1:8765"
    assert os.environ["MCP_AUTH_TOKEN"] == "tok-123" and os.environ["PANEL_DATA_DIR"] == str(data_dir / "panel")
    assert (data_dir / "panel").is_dir() and "http://127.0.0.1:9191" in capsys.readouterr().out


def test_panel_says_what_to_do_first(panel, tmp_path, monkeypatch, capsys):
    assert cli.main(["panel", "--env-file", str(tmp_path / "missing.env")]) == 1
    assert "init" in capsys.readouterr().out and panel == []
    monkeypatch.setitem(sys.modules, "fastapi", None)  # installed without the panel extra
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    assert cli.main(["panel"]) == 1 and "homeshed-mcp[panel]" in capsys.readouterr().out
    monkeypatch.setattr(cli, "PANEL_DIR", tmp_path / "nowhere")
    assert cli.main(["panel"]) == 1 and "panel extra" in capsys.readouterr().out and panel == []


def test_panel_starts_even_when_homeshed_is_not_up_yet(panel, monkeypatch, capsys):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (False, ""))
    assert cli.main(["panel"]) == 0 and len(panel) == 1
    assert "isn't answering" in capsys.readouterr().out


# --- panel --start: one command for a newcomer (the prepper for the owner, 2026-10-30) ------------------------------------
def test_settings_live_in_the_data_folder_unless_this_folder_already_has_them(tmp_path, data_dir):
    assert cli.default_env_file() == data_dir / ".env"
    (tmp_path / ".env").write_text("MCP_AUTH_TOKEN=old\n", encoding="utf-8")  # an install made before
    assert cli.default_env_file() == Path(".env")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / ".env").write_text("MCP_AUTH_TOKEN=new\n", encoding="utf-8")
    assert cli.default_env_file() == data_dir / ".env"


def test_init_in_the_data_folder_shares_one_vault_key_with_stdio(data_dir, monkeypatch):
    """A VAULT_KEY in the .env made the panel (HTTP) and the AI app (stdio, which reads no .env) each unable to read
    the other's credentials. In the data folder, init makes the key file both read instead."""
    import vault
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setattr(vault, "KEY_FILE", data_dir / "vault" / "vault.key")
    assert cli.main(["init"]) == 0
    values = cli._env_values(data_dir / ".env")
    assert values["MCP_AUTH_TOKEN"] and "VAULT_KEY" not in values
    assert vault.KEY_FILE.is_file() and vault.enabled()


@pytest.fixture
def starts(panel, monkeypatch):
    """--start with nothing running yet: background starts and the browser recorded, never run."""
    import webbrowser

    import panel_start
    seen = {"bg": [], "browser": [], "sign_in": []}
    monkeypatch.setattr(panel_start, "_background", lambda cmd, log, pid: seen["bg"].append(cmd))
    monkeypatch.setattr(panel_start, "_wait", lambda answers, url, seconds=0: True)
    monkeypatch.setattr(panel_start, "at_sign_in", lambda cmd: seen["sign_in"].append(cmd))
    monkeypatch.setattr(webbrowser, "open", lambda url: seen["browser"].append(url))
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (False, ""))
    return seen


def test_panel_start_does_init_server_panel_and_browser_in_one_go(starts, data_dir, monkeypatch, capsys):
    import vault
    monkeypatch.setattr(vault, "KEY_FILE", data_dir / "vault" / "vault.key")
    assert cli.main(["panel", "--start"]) == 0
    assert cli._env_values(data_dir / ".env")["MCP_AUTH_TOKEN"]  # init ran
    server, panel_cmd = starts["bg"]
    assert server[-6:] == ["serve", "--http", "--port", "8765", "--env-file", str((data_dir / ".env").resolve())]
    assert "panel" in panel_cmd and "--start" not in panel_cmd
    assert starts["browser"] == ["http://127.0.0.1:9090"] and starts["sign_in"] == []
    assert "panel --stop" in capsys.readouterr().out


def test_panel_start_reuses_what_runs_and_can_start_at_sign_in(starts, monkeypatch, capsys):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: (True, ""))
    assert cli.main(["panel", "--start", "--at-sign-in", "--no-browser"]) == 0
    assert starts["bg"] == [] and starts["browser"] == []
    (cmd,) = starts["sign_in"]
    assert cmd[cmd.index("panel"):][:3] == ["panel", "--start", "--no-browser"]
    with pytest.raises(SystemExit):
        cli.main(["panel", "--at-sign-in"])  # only with --start


def test_sign_in_goes_through_uv_when_this_python_is_in_its_cache(monkeypatch, tmp_path):
    """uv's cache can be cleaned or upgraded away, and the sign-in start would then stop silently (the prepper)."""
    import panel_start
    found = {"uvw": "C:/uv/uvw.exe", "uvx": "C:/uv/uvx.exe"}
    monkeypatch.setattr(panel_start.shutil, "which", lambda name: found.get(name))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "uv" / "cache" / "archive-v0" / "abc" / "python.exe"))
    assert panel_start.sign_in_command("homeshed_mcp._app.cli", "x")[:3] == ["C:/uv/uvw.exe", "tool", "run"]
    del found["uvw"]
    assert panel_start.sign_in_command("homeshed_mcp._app.cli", "x")[:2] == ["C:/uv/uvx.exe", "--from"]
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "python.exe"))  # pip install: its own Python
    assert panel_start.sign_in_command("homeshed_mcp._app.cli", "x")[1:] == ["-m", "homeshed_mcp._app.cli"]


def test_panel_stop_only_stops_a_process_whose_address_still_answers(data_dir, monkeypatch, capsys):
    import panel_start
    killed = []
    monkeypatch.setattr(panel_start.os, "kill", lambda pid, sig: killed.append(pid))
    run = data_dir / "run"
    run.mkdir(parents=True)
    (run / "server.pid").write_text("111", encoding="utf-8")
    (run / "panel.pid").write_text("222", encoding="utf-8")
    monkeypatch.setattr(cli, "_get", lambda url, timeout=3.0: ("healthz" in url, ""))  # the panel is gone already
    assert cli.main(["panel", "--stop"]) == 0
    assert killed == [111] and not list(run.iterdir())
