"""Folder guard for tools that read project files (strapi.check, code.dead_scan, code.webhook_retry_check).

A caller names a folder; the tool may read it only when its real path (symlinks resolved) sits inside an allowed root.
Allowed roots, in order:

    READ_ALLOWED_ROOTS=C:\\Code;D:\\Work     (os.pathsep-separated: ";" on Windows, ":" elsewhere)

and when that isn't set: CLAUDE_PROJECT_DIR (the project a stdio install serves), else the server's working directory.
Nothing outside those roots is read, so a client can't point a scanner at ~/.ssh or /etc.

files.list follows the same rule (2026-09-30, 5c229fd).
"""
from __future__ import annotations

import os
from pathlib import Path


class OutsideRoots(ValueError):
    """The folder isn't inside an allowed root, doesn't exist, or isn't a folder."""


def allowed_roots() -> list[Path]:
    raw = os.environ.get("READ_ALLOWED_ROOTS", "")
    roots = [r for r in raw.split(os.pathsep) if r.strip()] if raw.strip() else []
    if not roots:
        roots = [os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()]
    return [Path(r).expanduser().resolve() for r in roots]


def resolve_folder(path: str) -> Path:
    """The folder's real path, if it's allowed. Relative paths resolve against the first root."""
    if not path or not str(path).strip():
        raise OutsideRoots("give a folder to scan")
    roots = allowed_roots()
    p = Path(path).expanduser()
    real = (p if p.is_absolute() else roots[0] / p).resolve()
    if not any(real == r or r in real.parents for r in roots):
        raise OutsideRoots(f"{path} is outside the folders this server may read (READ_ALLOWED_ROOTS)")
    if not real.is_dir():
        raise OutsideRoots(f"not a folder: {path}")
    return real


def resolve_file(path: str, suffixes: tuple[str, ...], max_bytes: int) -> Path:
    """A file's real path, for tools that read a file's content (docs.read, voice.transcribe): inside an allowed root,
    never a hidden file or folder (so a .env can't be read), one of suffixes, at most max_bytes."""
    if not path or not str(path).strip():
        raise OutsideRoots("give the file's path")
    roots = allowed_roots()
    p = Path(str(path).strip()).expanduser()
    real = (p if p.is_absolute() else roots[0] / p).resolve()
    root = next((r for r in roots if r in real.parents), None)
    if root is None:
        raise OutsideRoots(f"{path} is outside the folders this server may read (READ_ALLOWED_ROOTS)")
    if any(part.startswith(".") for part in real.relative_to(root).parts):
        raise OutsideRoots("hidden files and folders (names starting with a dot) aren't read")
    if real.suffix.lower() not in suffixes:
        raise OutsideRoots(f"only {', '.join(suffixes)} files; this is {real.suffix or 'no extension'}")
    if not real.is_file():
        raise OutsideRoots(f"no such file: {path}")
    size = real.stat().st_size
    if size > max_bytes:
        raise OutsideRoots(f"the file is {size / 2**20:.1f} MB; the limit is {max_bytes // 2**20} MB")
    return real


def inside(real_root: Path, candidate: Path) -> bool:
    """Whether a file found while walking real_root still resolves inside it (a symlink can point anywhere)."""
    try:
        target = candidate.resolve()
    except OSError:
        return False
    return target == real_root or real_root in target.parents
