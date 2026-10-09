"""Over HTTP the server listens on this machine only unless MCP_BIND says otherwise, and the Docker image keeps
accepting its published port (release v1, 2026-09-30: it listened on every interface). Test list drafted by the
local model."""
from __future__ import annotations

import re
from pathlib import Path

import server

HERE = Path(__file__).resolve().parents[1]


def _listen(monkeypatch) -> dict:
    seen: dict = {}
    monkeypatch.setattr(server, "http_app", lambda: "app")
    monkeypatch.setattr(server, "private_routes", None)
    monkeypatch.setattr(server.uvicorn, "run", lambda app, host, port: seen.update(host=host, port=port))
    return seen


def test_http_listens_on_this_machine_only_by_default(monkeypatch):
    seen = _listen(monkeypatch)
    monkeypatch.delenv("MCP_BIND", raising=False)
    server.serve_http(9999)
    assert seen == {"host": "127.0.0.1", "port": 9999}


def test_mcp_bind_opens_it_to_other_machines(monkeypatch):
    seen = _listen(monkeypatch)
    monkeypatch.setenv("MCP_BIND", "0.0.0.0")
    server.serve_http(9999)
    assert seen["host"] == "0.0.0.0"


def test_the_docker_image_still_accepts_its_published_port():
    """Without this line the container would listen on its own loopback and the published port would go dead."""
    assert re.search(r"(?m)^ENV MCP_BIND=0\.0\.0\.0$", (HERE / "Dockerfile").read_text(encoding="utf-8"))
    # env_file passes .env into the container: an empty MCP_BIND= there would override the image's value
    example = (HERE / ".env.example").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^MCP_BIND=", example)


def _ignored(path: str, rules: list[str]) -> bool:
    """Docker's .dockerignore rule for a top-level name: the last matching pattern decides; "!" re-includes."""
    import fnmatch
    out = False
    for rule in rules:
        negate = rule.startswith("!")
        if fnmatch.fnmatchcase(path, rule.lstrip("!")):
            out = not negate
    return out


def test_the_image_never_takes_settings_files_or_their_backups():
    """2026-09-30: eight .env backups (secrets) were in the running image, since only ".env" itself was ignored."""
    rules = [ln.strip() for ln in (HERE / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    for secret in (".env", ".env.bak-2026-09-30-readroots", ".env.bak-20260923112132", ".env.local", "old.bak"):
        assert _ignored(secret, rules), secret
    for shipped in (".env.example", "server.py", "tools", "capabilities", "pyproject.toml", "Dockerfile"):
        assert not _ignored(shipped, rules), shipped


def test_the_image_runs_as_an_ordinary_user_that_owns_data():
    """2026-09-30 (R&D's plan): not root; /data made and owned by that user so new named volumes seed with it."""
    text = (HERE / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"useradd [^\n]*--uid 10001", text) and "chown -R app:app /data" in text
    users = re.findall(r"(?m)^USER (\S+)$", text)
    assert users and users[-1] == "app", users  # the last USER wins
    assert text.index("USER app") > text.index("RUN groupadd")


def test_the_base_image_is_pinned_by_digest():
    """Every build starts from the same base; Dependabot bumps the digest (2026-09-30). Pattern by the local model."""
    assert re.search(r"(?m)^FROM .+@sha256:[a-f0-9]{64}$", (HERE / "Dockerfile").read_text(encoding="utf-8"))
