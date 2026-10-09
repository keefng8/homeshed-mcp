"""files.list. See ../../capabilities/files/list.md."""
from __future__ import annotations

import os
import stat
from datetime import datetime, timezone

from registry import tool


class FilesListError(RuntimeError):
    """Bad path, not a directory, or no permission to read it."""


def _human(size: int) -> str:
    """1536 -> "1.5 KB", like ls -h."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _compact(entry: dict) -> str:
    """ls -F style: "docs/", "notes.txt (1.2 KB)", "link@"."""
    if entry["type"] == "dir":
        return entry["name"] + "/"
    if entry["type"] == "file":
        return f"{entry['name']} ({_human(entry['size_bytes'])})"
    return entry["name"] + "@"


@tool(name="list", category="files", doc="files/list.md")
def list_dir(path: str = ".", limit: int = 100, detail: str = "compact") -> dict:
    """List a directory's entries — names, type, size, modified time. Metadata only, never file
    content — deliberately scoped this way; a content-reading capability is a real security
    decision (could expose `.env` secrets inside this container) not made yet, see the doc's
    "Deliberately not built" section.

    Args:
        path: directory to list. Defaults to the server's working directory (`/app` in the
            deployed container). Relative paths resolve against that working directory.
        limit: at most this many entries, 1-1000 (default 100), directories first. A bigger folder says how many
            were left out in `more` (2026-09-30: 300 files came back as 50K characters).
        detail: "compact" (the default: each entry a string like ls -F, "docs/", "notes.txt (1.2 KB)", "link@") or
            "full" (each entry a dict with every field below). Compact is about a tenth of the size.

    Returns:
        {"path": str (resolved absolute path), "total": int (every entry), "entries": [...], "more"?: int}. With
        detail="full" each entry is {name, type, size_bytes, modified_iso, permissions: {octal, human}}: `type` is
        `"file"`, `"dir"`, or `"other"` (symlink/socket/etc.), `permissions.octal` the 3-digit mode (e.g. `"644"`),
        `.human` `ls`-style (e.g. `"-rw-r--r--"`). Sorted directories-first, then alphabetically; `more` only when
        entries were left out.

    Raises:
        FilesListError: path doesn't exist, isn't a directory, isn't readable, or sits outside the folders this
            server may read (READ_ALLOWED_ROOTS, else CLAUDE_PROJECT_DIR, else its working folder: readroots.py);
            or limit isn't a whole number from 1 to 1000; or detail isn't "compact" or "full".
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise FilesListError("limit must be a whole number from 1 to 1000")
    if detail not in ("compact", "full"):
        raise FilesListError('detail must be "compact" or "full"')
    import readroots  # the scanners' folder rule (2026-09-30): a client can't list ~/.ssh or /etc
    try:
        resolved = str(readroots.resolve_folder(path))
    except readroots.OutsideRoots as exc:
        if "outside" in str(exc):
            raise FilesListError(str(exc)) from None
        resolved = os.path.abspath(path)  # inside the roots but missing or not a folder: say which below
        if not os.path.exists(resolved):
            raise FilesListError(f"path does not exist: {resolved}") from None
        raise FilesListError(f"not a directory: {resolved}") from None

    try:
        names = os.listdir(resolved)
    except PermissionError:
        raise FilesListError(f"permission denied: {resolved}") from None

    entries = []
    for name in names:
        full = os.path.join(resolved, name)
        try:
            st = os.lstat(full)
        except OSError:
            continue  # vanished between listdir and stat, or genuinely unreadable -- skip, don't fail the whole listing
        if os.path.isdir(full) and not os.path.islink(full):
            kind = "dir"
        elif os.path.isfile(full) and not os.path.islink(full):
            kind = "file"
        else:
            kind = "other"
        entries.append(
            {
                "name": name,
                "type": kind,
                "size_bytes": st.st_size,
                "modified_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                "permissions": {
                    "octal": oct(stat.S_IMODE(st.st_mode))[2:].zfill(3),
                    "human": stat.filemode(st.st_mode),
                },
            }
        )

    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    shown = entries[:limit]
    out = {"path": resolved, "total": len(entries), "entries": shown if detail == "full" else [_compact(e) for e in shown]}
    if len(entries) > limit:
        out["more"] = len(entries) - limit
    return out
