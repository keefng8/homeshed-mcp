"""Shared subprocess runner for the dev.* capabilities. Not a capability itself.

Real, considered risk, not an oversight: this executes commands directly inside mcp-server's own
container/process — no separate sandbox, no allowlist beyond the fixed binary each capability
invokes (python/node) or an explicit argv the caller supplies (build/test/lint). If the server can
reach the Docker socket, so can anything these tools run. That is why every dev.* tool is off until
the owner names it in ENABLE_TOOLS ("*" doesn't include them). Argv lists only, never `shell=True`/string interpolation, to at least not add
shell-injection on top of the already-accepted arbitrary-execution risk.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


class DevError(RuntimeError):
    """Any dev-command failure — bad cwd, binary not found, command timed out."""


def run_command(argv: list[str], cwd: str, timeout: int) -> dict:
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        raise DevError(f"not a directory: {cwd}")

    try:
        result = subprocess.run(
            argv, cwd=cwd_path, capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise DevError(f"{argv[0]!r} is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise DevError(f"command timed out after {timeout}s") from None

    return {
        "exit_code": result.returncode,
        "stdout": result.stdout[-20_000:],
        "stderr": result.stderr[-20_000:],
    }
