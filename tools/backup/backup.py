"""backup.export and backup.restore: HomeShed's own state in one zip, and back. See ../../capabilities/backup/backup.md.

R&D's verdict (researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md): a plain zip with a manifest of
sha256 sums; restore is a dry run first, then a confirm; names that climb out of the data folder are refused; the
vault's values never go in (only the names of the keys stored there), so the zip holds no secrets and says so.

What goes in: the state files under DATA_DIR (settings, schedules, watches, clients' grants and token hashes, the
to-do list, approvals, observations...). Left out: the vault folder, generated images, other services' folders,
lock and temp files, and logs unless asked (they can be large). Any file with a secret-shaped value in it is left
out too and named in the answer, so a key that slipped into a state file never travels.
Owner only, on HomeShed Pro: a client can't export (the zip is every app's state) or restore.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path, PurePosixPath

import clients
import paths
from registry import tool

FORMAT = 1
KEEP = 10
MAX_TOTAL = 200 * 1024 * 1024
SKIP_DIRS = {"vault", "backups", "kuma", "images"}
SKIP_SUFFIXES = {".lock", ".tmp", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".mp4"}
LOG_SUFFIXES = {".jsonl", ".log"}
_pending: dict[str, tuple[str, float]] = {}  # confirm code -> (backup file, when): a dry run's code works 10 minutes


class BackupError(ValueError):
    """Safe to show."""


PRO_ONLY = "Backups are a HomeShed Pro feature."


def _owner_only() -> None:
    """The owner, on Pro (the owner, 2026-10-24: "make it gated behind pro"). The zip stays on this machine."""
    if clients.current_client.get() is not None:
        raise BackupError("only the owner can make or restore a backup")
    import pro
    on_pro, why = pro.check()
    if not on_pro:
        raise BackupError(f"{PRO_ONLY} {why or 'Connect a Mavis Pro account in Settings to use it.'}")


def _backups_dir() -> Path:
    return Path(os.environ.get("BACKUPS_DIR") or paths.data_path("usage/backups"))


def _has_secret(data: bytes) -> bool:
    from tools.secrets.transcript_scan import _values
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return next(_values(text), None) is not None


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("homeshed-mcp")
    except Exception:  # noqa: BLE001 - a source checkout has no installed version
        return "source"


def _candidates(include_logs: bool):
    root = Path(paths.DATA_DIR)
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if rel.parts[0] in SKIP_DIRS or any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if any(part.startswith(".") for part in rel.parts) or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        if p.suffix.lower() in LOG_SUFFIXES and not include_logs:
            continue
        yield rel, p


@tool(name="export", category="backup", doc="backup/backup.md")
def export(include_logs: bool = False) -> dict:
    """Save HomeShed's settings and state to one zip file: schedules, watched pages, app access, the to-do list,
    approvals, observations and settings. The vault's key VALUES are never included, only which keys are stored, so
    you re-enter them on a new machine. Owner only. The 10 newest backups are kept.

    Args:
        include_logs: also include the activity logs (.jsonl), which can be large. Default no.

    Returns:
        {"file", "bytes", "files": n, "skipped_secret": [names left out because a value in them looked like a secret],
         "vault_keys": n, "encrypted": false}
    """
    _owner_only()
    files, skipped, total = [], [], 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, p in _candidates(include_logs):
            data = p.read_bytes()
            name = PurePosixPath(*rel.parts).as_posix()
            if _has_secret(data):
                skipped.append(name)
                continue
            total += len(data)
            if total > MAX_TOTAL:
                raise BackupError(f"the state is over {MAX_TOTAL // 2**20} MB; try without logs")
            z.writestr(f"data/{name}", data)
            files.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        import vault
        names = sorted(c["name"] for c in vault.list_credentials())
        manifest = {"format": FORMAT, "homeshed": _version(), "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "files": files, "vault_key_names": names, "encrypted": False,
                    "note": "No secret values are in this file: re-enter the vault keys listed here after a restore."}
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    out_dir = _backups_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"homeshed-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    target.write_bytes(buf.getvalue())
    for old in sorted(out_dir.glob("homeshed-backup-*.zip"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return {"file": str(target), "bytes": target.stat().st_size, "files": len(files), "skipped_secret": skipped,
            "vault_keys": len(names), "encrypted": False}


def _open(file: str) -> tuple[Path, zipfile.ZipFile, dict]:
    p = Path(file).expanduser()
    if not p.is_absolute():
        p = _backups_dir() / p
    p = p.resolve()
    if p.parent != _backups_dir().resolve() or p.suffix != ".zip":
        raise BackupError("name a backup in the backups folder (backup.export gives its file)")
    if not p.is_file():
        raise BackupError(f"no such backup: {file}")
    try:
        z = zipfile.ZipFile(p)
        manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise BackupError("that isn't a HomeShed backup (no readable manifest)") from None
    if manifest.get("format") != FORMAT:
        raise BackupError(f"backup format {manifest.get('format')} isn't one this version restores")
    return p, z, manifest


def _check(z: zipfile.ZipFile, manifest: dict) -> list[tuple[str, bytes]]:
    """Every file present, its sum right, its name safe (no climbing out, no absolute path, nothing skipped by kind)."""
    out = []
    for f in manifest.get("files") or []:
        name = str(f.get("name") or "")
        parts = PurePosixPath(name).parts
        if not parts or name.startswith("/") or "\\" in name or ":" in name or ".." in parts \
                or parts[0] in SKIP_DIRS or any(x.startswith(".") for x in parts):
            raise BackupError(f"refused: an unsafe name in the backup ({name[:80]})")
        try:
            data = z.read(f"data/{name}")
        except KeyError:
            raise BackupError(f"the backup is missing {name}") from None
        if hashlib.sha256(data).hexdigest() != f.get("sha256"):
            raise BackupError(f"{name} doesn't match its checksum: the backup is damaged")
        out.append((name, data))
    return out


@tool(name="restore", category="backup", doc="backup/backup.md")
def restore(file: str, confirm: str = "") -> dict:
    """Put a backup's state back. First call without confirm: a dry run that checks every file and lists what would
    change, and gives a confirm code. Call again with that code (within 10 minutes) to restore. A fresh backup of the
    current state is made first, so a restore can itself be undone. Owner only. Restart the tool server afterwards.

    Args:
        file: the backup's file name (from backup.export, in the backups folder).
        confirm: the code a dry run gave; empty for the dry run.

    Returns:
        dry run: {"dry_run": true, "new": [...], "changed": [...], "same": n, "vault_keys_to_reenter": [...],
        "confirm"}; restore: {"restored": n, "safety_backup", "vault_keys_to_reenter", "next": "restart"}
    """
    _owner_only()
    p, z, manifest = _open(file)
    entries = _check(z, manifest)
    root = Path(paths.DATA_DIR)
    new = [n for n, _ in entries if not (root / n).exists()]
    changed = [n for n, d in entries if (root / n).exists() and (root / n).read_bytes() != d]
    names = manifest.get("vault_key_names") or []
    if not confirm:
        code = hashlib.sha256(f"{p}|{time.time()}".encode()).hexdigest()[:8]
        _pending[code] = (str(p), time.time())
        return {"dry_run": True, "backup": p.name, "made": manifest.get("created"), "homeshed": manifest.get("homeshed"),
                "new": new[:50], "changed": changed[:50], "same": len(entries) - len(new) - len(changed),
                "vault_keys_to_reenter": names, "confirm": code}
    got = _pending.pop(str(confirm), None)
    if got is None or got[0] != str(p) or time.time() - got[1] > 600:
        raise BackupError("that confirm code isn't valid for this backup (or is over 10 minutes old): do a dry run again")
    safety = export()["file"]
    for name, data in entries:
        target = (root / name).resolve()
        if root.resolve() not in target.parents:
            raise BackupError(f"refused: {name} would land outside the data folder")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".restoring")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return {"restored": len(entries), "safety_backup": safety, "vault_keys_to_reenter": names,
            "next": "restart the tool server so every part reads the restored state"}
