import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Every default state path (paths.py) points into a throwaway folder for the whole run, set before any module computes
# its defaults. Without it a test missing its own fixture writes to the real DATA_DIR: on Windows /data is the root of
# the current drive (2026-09-29: a moved bugs.sync test left a fake bug in D:\data\observations).
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="homeshed-test-data-")

# And a guard that catches the next one whatever path it takes (capability list #16; observations 0016 and 0018, then
# 2026-09-29 again): every file opened for writing during the run must sit in a temp folder, or be Python's own cache.
# The run fails at the end, naming each file. Python's audit hook sees every open(), Path.write_*, os.open and so on.
_OUTSIDE: set[str] = set()
_TEMP_ROOTS = tuple({os.path.normcase(os.path.realpath(p)) for p in (tempfile.gettempdir(), os.environ["DATA_DIR"])})
_NULL = {os.path.normcase(os.path.realpath(os.devnull)), os.path.normcase(os.devnull)}  # subprocess.DEVNULL opens it
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _writes(mode, flags) -> bool:
    if isinstance(mode, str):
        return any(c in mode for c in "wax+")
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _audit(event: str, args) -> None:
    if event != "open" or not args:
        return
    path, mode, flags = (list(args) + [None, None])[:3]
    if isinstance(path, int) or path is None or not _writes(mode, flags):
        return
    try:
        real = os.path.normcase(os.path.realpath(os.fsdecode(path)))
    except (TypeError, ValueError, OSError):
        return
    # pytest's own cache: recent pytest writes it into pytest-cache-files-XXXX first, then renames it to .pytest_cache
    # (the prepper's CI run, 2026-10-01: the suite passed but exited 1 on these three files)
    if (real.startswith(_TEMP_ROOTS) or real in _NULL or "__pycache__" in real or ".pytest_cache" in real
            or "pytest-cache-files-" in real):
        return
    _OUTSIDE.add(real)


sys.addaudithook(_audit)


def pytest_sessionfinish(session, exitstatus):
    if _OUTSIDE:
        print("\nTests wrote outside the temp folders (make them use tmp_path or DATA_DIR):")
        for path in sorted(_OUTSIDE):
            print(f"  {path}")
        session.exitstatus = 1
