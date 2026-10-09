"""Release smoke test: does the package a new user installs actually work? (2026-09-30: the first run found the server
introducing itself as "homelab-capabilities" with no version.) Checks drafted by local_ai.ask, built by Claude.

    python scripts/release_smoke.py            from the mcp-server folder (needs uv on PATH)

Builds the wheel, installs it into a fresh virtualenv in a temp folder, then with a throwaway DATA_DIR and no settings
from this machine: --help, init (writes MCP_AUTH_TOKEN, VAULT_KEY and MCP_ALLOWED_HOSTS), setup, doctor, and over
stdio: initialize (serverInfo name and version = the wheel's), tools/list, and a memory round trip on the built-in
store. Prints each check; exits 1 if any failed. Nothing is published, and nothing outside the temp folder is written.
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
results: list[tuple[bool, str]] = []
# Every step has a limit, so a server that can't start fails fast with its stderr (the prepper, 2026-10-06: on Python
# 3.11 the stdio check sat 12+ minutes until verify's own limit).
STDIO_REPLY_S = 60


def check(ok: bool, what: str) -> bool:
    results.append((ok, what))
    print(("  ok    " if ok else "  FAIL  ") + what)
    return ok


def run(args, env, timeout=180):
    try:
        return subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(args, -1, "", f"timed out after {timeout} s\n{err}")


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="homeshed-release-smoke-"))
    try:
        built = run(["uv", "build", "--wheel", "--out-dir", str(work / "dist")], dict(os.environ), timeout=600)
        wheels = list((work / "dist").glob("homeshed_mcp-*.whl"))
        if not check(built.returncode == 0 and len(wheels) == 1, "the wheel builds"):
            print(built.stderr[-800:])
            return 1
        version = re.match(r"homeshed_mcp-([^-]+)-", wheels[0].name).group(1)
        run(["uv", "venv", "-q", str(work / "venv")], dict(os.environ))
        py = work / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        exe = work / "venv" / ("Scripts/homeshed-mcp.exe" if os.name == "nt" else "bin/homeshed-mcp")
        inst = run(["uv", "pip", "install", "-q", "--python", str(py), str(wheels[0])], dict(os.environ), timeout=600)
        if not check(inst.returncode == 0 and exe.exists(), f"it installs into a fresh virtualenv ({version})"):
            print(inst.stderr[-800:])
            return 1
        env = {k: v for k, v in os.environ.items() if not k.startswith(("MEMORY_", "MCP_", "LOCAL_AI_", "CLAUDE_",
                                                                           "NTFY_", "VAULT_", "READ_ALLOWED"))}
        env.update(DATA_DIR=str(work / "data"), CLAUDE_PROJECT_DIR=str(work))
        check(run([str(exe), "--help"], env).returncode == 0, "--help works")
        env_file = work / "app.env"
        init = run([str(exe), "init", "--env-file", str(env_file)], env)
        keys = {ln.split("=", 1)[0] for ln in env_file.read_text().splitlines() if "=" in ln} if env_file.exists() else set()
        check(init.returncode == 0 and {"MCP_AUTH_TOKEN", "VAULT_KEY", "MCP_ALLOWED_HOSTS"} <= keys,
              "init writes the token, the vault key and the host list")
        setup = run([str(exe), "setup"], env)
        check(setup.returncode == 0 and "claude mcp add homeshed-mcp" in setup.stdout, "setup prints the connect command")
        doctor = run([str(exe), "doctor"], env)
        check(doctor.returncode == 0 and "Nothing is broken" in doctor.stdout, "doctor: nothing broken on a new install")
        stdio(exe, env, version)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed." + (" Failed: " + "; ".join(failed) if failed else ""))
    return 1 if failed else 0


def stdio(exe: Path, env: dict, version: str) -> None:
    p = subprocess.Popen([str(exe), "serve"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8")
    lines: queue.Queue = queue.Queue()
    err_lines: list[str] = []
    # Both pipes are read on their own threads: a reply can't block forever, and a chatty stderr can't fill its pipe
    # and stall the server.
    def pump() -> None:
        for ln in p.stdout:
            lines.put(ln)
        lines.put("")  # the server closed stdout: an empty reply, not a wait

    readers = [threading.Thread(target=pump, daemon=True),
               threading.Thread(target=lambda: err_lines.extend(p.stderr), daemon=True)]
    for t in readers:
        t.start()

    def ask(msg: dict) -> dict | None:
        try:
            p.stdin.write(json.dumps(msg) + "\n")
            p.stdin.flush()
        except OSError:
            return {}  # the server has already gone: the checks below say so
        if "id" not in msg:
            return None
        try:
            line = lines.get(timeout=STDIO_REPLY_S)
        except queue.Empty:
            check(False, f"stdio: no reply to {msg['method']} within {STDIO_REPLY_S} s"
                         + (f" (stderr: {''.join(err_lines)[-300:].strip()})" if err_lines else ""))
            p.kill()
            return {}
        return json.loads(line) if line.strip() else {}

    try:
        info = (ask({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}}})
                or {}).get("result", {}).get("serverInfo", {})
        check(info.get("name") == "homeshed-mcp" and info.get("version") == version,
              f"stdio: the server says it's homeshed-mcp {version} ({info.get('name')} {info.get('version')!r})")
        ask({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = (ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) or {}).get("result", {}).get("tools", [])
        check(len(tools) >= 20, f"stdio: {len(tools)} tools listed")
        ask({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "memory.remember_fact",
                                                                             "arguments": {"content": "smoke fact"}}})
        got = (ask({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "memory.recall_facts",
                                                                                   "arguments": {}}}) or {}).get("result", {})
        text = json.dumps(got.get("content") or [])
        check("smoke fact" in text, "stdio: memory saves and recalls on the built-in store")
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=10)
        readers[1].join(timeout=5)
        err = "".join(err_lines).strip()
        check(not re.search(r"(?i)traceback|error", err), "stdio: nothing alarming on stderr" + (f" ({err[-120:]})" if err else ""))


if __name__ == "__main__":
    sys.exit(main())
