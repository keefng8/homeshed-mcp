"""dev.python. See ../../capabilities/dev/python.md."""
from __future__ import annotations

from registry import tool
from tools.dev._run import DevError, run_command

MAX_TIMEOUT = 600


@tool(name="python", category="dev", doc="dev/python.md")
def python(args: list[str], cwd: str, timeout: int = 120) -> dict:
    """Run `python <args>` in a working directory. Real code execution, in-process inside this
    container — see `tools/dev/_run.py`'s module docstring for the real risk this accepts and
    why. No confirmation step.

    Args:
        args: arguments passed to the `python` binary (e.g. `["script.py", "--flag"]` or
            `["-m", "pytest", "tests/"]`). Not a shell string — no shell metacharacter expansion.
        cwd: working directory to run in.
        timeout: seconds before the process is killed (max 600).

    Returns:
        {"exit_code": int, "stdout": str, "stderr": str} (both streams truncated to their last
        20,000 characters if longer).

    Raises:
        DevError: cwd doesn't exist, `python` isn't on PATH, or the command timed out.
    """
    if not args:
        raise DevError("args must be non-empty")
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise DevError(f"timeout must be between 1 and {MAX_TIMEOUT}")
    return run_command(["python", *args], cwd, timeout)
