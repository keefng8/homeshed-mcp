"""dev.build. See ../../capabilities/dev/build.md."""
from __future__ import annotations

from registry import tool
from tools.dev._run import DevError, run_command

MAX_TIMEOUT = 900


@tool(name="build", category="dev", doc="dev/build.md")
def build(command: list[str], cwd: str, timeout: int = 300) -> dict:
    """Run a build command in a working directory (e.g. `["npm", "run", "build"]`,
    `["make"]`, `["docker", "build", "-t", "img", "."]`). No single universal "build" binary
    exists across project types, so the caller supplies the full command rather than this
    capability guessing at one. Real code execution, in-process inside this container — see
    `tools/dev/_run.py`'s module docstring for the real risk this accepts and why. No
    confirmation step.

    Args:
        command: full argv (e.g. `["npm", "run", "build"]`). Not a shell string — no shell
            metacharacter expansion, and no `&&`/pipes.
        cwd: working directory to run in.
        timeout: seconds before the process is killed (max 900 — builds are typically the
            slowest of the dev.* commands).

    Returns:
        {"exit_code": int, "stdout": str, "stderr": str} (both streams truncated to their last
        20,000 characters if longer).

    Raises:
        DevError: cwd doesn't exist, command is empty, the binary isn't on PATH, or the command
            timed out.
    """
    if not command:
        raise DevError("command must be non-empty")
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise DevError(f"timeout must be between 1 and {MAX_TIMEOUT}")
    return run_command(command, cwd, timeout)
