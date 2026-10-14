"""Check an unpacked homeshed_pro folder against its wheel's RECORD (to-do #19 round 8; the Github prepper's draft).

Why: the wheel's sha256 can't be re-checked at start, because update() deletes the .whl after unpacking. The wheel's own
dist-info/RECORD lists every file with its sha256 (PEP 376/427), so:
- update(): after the signature check (sig covers name|version|sha256(RECORD)|issued_to), save record_sha256 in
  current.json.
- load(): call verify(folder, cur["record_sha256"]) before putting the folder on sys.path, and refuse on any problem.
A copied or hand-edited folder then fails unless its RECORD hash matches the signed statement saved with it.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
from pathlib import Path


def _b64sha(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def verify(folder: Path, record_sha256: str, package: str = "homeshed_pro") -> str | None:
    """Why the folder can't be trusted, or None. Never raises."""
    try:
        records = list(folder.glob(f"{package}-*.dist-info/RECORD"))
        if len(records) != 1:
            return "the Pro package has no single RECORD file"
        raw = records[0].read_bytes()
        if hashlib.sha256(raw).hexdigest() != (record_sha256 or "").lower():
            return "the Pro package's file list doesn't match the signed one"
        listed = {}
        for row in csv.reader(io.StringIO(raw.decode("utf-8"))):
            if row and row[0]:
                listed[row[0]] = row[1] if len(row) > 1 else ""
        record_name = records[0].relative_to(folder).as_posix()
        for name, h in listed.items():
            if name == record_name:
                continue  # RECORD can't list its own hash; it is covered by record_sha256
            p = folder / name
            if ".." in Path(name).parts or not p.is_file():
                return f"the Pro package is missing {name[:80]}"
            if not h or _b64sha(p.read_bytes()) != h:
                return f"the Pro package's {name[:80]} was changed"
        for p in folder.rglob("*"):  # an added file (say a sitecustomize or an extra module) is refused too
            if p.is_file() and "__pycache__" not in p.parts and p.relative_to(folder).as_posix() not in listed:
                return f"the Pro package has an extra file {p.relative_to(folder).as_posix()[:80]}"
        return None
    except (OSError, ValueError, UnicodeDecodeError, csv.Error) as exc:
        return f"the Pro package couldn't be checked ({type(exc).__name__})"
