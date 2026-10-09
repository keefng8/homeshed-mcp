"""repo.readiness. See ../../capabilities/repo/readiness.md.

How ready a project folder is to publish on GitHub. The files come from checklist.json, the Github prepper's machine
copy of REPO-STRUCTURE.md (the layout HomeShed shipped with, 2026-09-30; its own tests keep the two in step): each item
has a path, a level (required, recommended, or conditional on a `when` this module detects), a fix, and optional text
the file must or mustn't contain. On top, a few checks inside files where having the file isn't enough: a Dockerfile
that runs as root or floats on a tag, and compose ports open to the network. Read-only: it reads a handful of small
files, and only inside READ_ALLOWED_ROOTS (readroots.py).
"""
from __future__ import annotations

import json
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path

import findings
from registry import tool

CHECKLIST = Path(__file__).with_name("checklist.json")
PORT_LINE = re.compile(r"""^\s*-\s*["']?((?:[\d.]+:)?\d+(?:-\d+)?:\d+(?:-\d+)?(?:/\w+)?)["']?\s*(?:#.*)?$""")
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
READS_ENV = re.compile(r"os\.environ|getenv\(|process\.env")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}


class ReadinessError(RuntimeError):
    """A folder outside the allowed ones, missing, or not a folder; or a detail other than compact or full."""


@lru_cache(maxsize=1)
def checklist() -> dict:
    return json.loads(CHECKLIST.read_text(encoding="utf-8"))


def _text(path: Path, limit: int = 200_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(_text(path)) if path.is_file() else {}
    except tomllib.TOMLDecodeError:
        return {}


def _json(path: Path) -> dict:
    try:
        data = json.loads(_text(path)) if path.is_file() else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _reads_env(root: Path, max_files: int = 400) -> bool:
    """Does the code read environment variables? Stops at the first hit; skips virtualenvs and build output."""
    seen = 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(base) / d / "pyvenv.cfg").is_file()]
        if len(Path(base).relative_to(root).parts) > 3:
            dirs[:] = []
        for name in files:
            if name.endswith((".py", ".js", ".ts", ".mjs")):
                seen += 1
                if READS_ENV.search(_text(Path(base) / name, 100_000)):
                    return True
                if seen >= max_files:
                    return False
    return False


def conditions(root: Path) -> dict[str, bool]:
    """The checklist's `when` names, detected (their meanings are in checklist.json)."""
    project = _toml(root / "pyproject.toml").get("project") or {}
    package = _json(root / "package.json")
    top_py = any(p.suffix == ".py" and p.is_file() for p in root.iterdir())
    return {
        "python": (root / "pyproject.toml").is_file() or (root / "setup.py").is_file() or top_py,
        "apache": "apache license" in _text(root / "LICENSE", 4000).lower(),
        "docker": (root / "Dockerfile").is_file(),
        "settings": _reads_env(root),
        "publishes": bool(project.get("name")) or (bool(package.get("name")) and not package.get("private"))
        or (root / "Dockerfile").is_file(),
        "deps": bool(project.get("dependencies")) or (root / "requirements.txt").is_file()
        or bool(package.get("dependencies")),
    }


def _row(cid: str, level: str, ok: bool, file: str, detail: str) -> dict:
    return {"id": cid, "level": level, "ok": ok, "file": file, "detail": detail}


def _item_row(root: Path, item: dict) -> dict:
    path, level, fix = item["path"], item["level"], item.get("fix", "")
    target = root / path.rstrip("/")
    present = (target.is_dir() and any(target.iterdir())) if path.endswith("/") else target.is_file()
    if not present:
        return _row(path, level, False, path, fix)
    checks = item.get("checks") or {}
    text = _text(target) if checks and target.is_file() else ""
    missing = [s for s in checks.get("must_contain") or [] if s not in text]
    leftover = [s for s in checks.get("must_not_contain") or [] if s in text]
    if missing or leftover:
        said = (["missing: " + ", ".join(missing)] if missing else []) + (["still has: " + ", ".join(leftover)] if leftover else [])
        return _row(path, level, False, path, f"{'; '.join(said)}. {fix}")
    return _row(path, level, True, path, "found")


def _dockerfile_rows(root: Path) -> list[dict]:
    lines = [ln.strip() for ln in _text(root / "Dockerfile").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    stages = {m.group(1).lower() for ln in lines if (m := re.match(r"(?i)FROM\s+(?:--\S+\s+)*\S+\s+AS\s+(\S+)", ln))}
    # the image is the first word after FROM that isn't a flag (FROM --platform=linux/amd64 image: the prepper's catch)
    images = [next((w for w in ln.split()[1:] if not w.startswith("--")), "") for ln in lines
              if re.match(r"(?i)FROM\s+\S+", ln)]
    images = [i for i in images if i]
    unpinned = [i for i in images if i.lower() not in stages and i.lower() != "scratch" and "@sha256:" not in i]
    users = [ln.split(None, 1)[1].strip() for ln in lines if re.match(r"(?i)USER\s+\S+", ln)]
    root_user = not users or users[-1].split(":")[0] in ("root", "0")
    health = any(re.match(r"(?i)HEALTHCHECK\s", ln) for ln in lines)
    return [
        _row("docker-non-root", "recommended", not root_user, "Dockerfile",
             "add a non-root USER that owns only the data folder (it runs as root now)" if root_user
             else f"runs as {users[-1]}"),
        _row("docker-base-pinned", "recommended", not unpinned, "Dockerfile",
             f"pin by digest (FROM image@sha256:...) and let Dependabot bump it: {', '.join(unpinned)}" if unpinned
             else "every base image is pinned by digest"),
        _row("docker-healthcheck", "recommended", health, "Dockerfile",
             "has a HEALTHCHECK" if health else "add a HEALTHCHECK so Docker and monitors can see when it's unwell"),
    ]


def _compose_rows(root: Path) -> list[dict]:
    rows = []
    for name in COMPOSE_FILES:
        if (root / name).is_file():
            open_ports = [m.group(1) for ln in _text(root / name).splitlines()
                          if not ln.lstrip().startswith("#") and (m := PORT_LINE.match(ln))
                          and not m.group(1).startswith("127.0.0.1:")]
            rows.append(_row("compose-local", "recommended", not open_ports, name,
                             "published ports bind 127.0.0.1" if not open_ports else
                             f"bind published ports to 127.0.0.1 by default ({', '.join(open_ports)}); let users widen it"))
    return rows


@tool(name="readiness", category="repo", doc="repo/readiness.md")
def readiness(folder: str = ".", detail: str = "compact") -> dict:
    """How ready a project folder is to publish on GitHub, with a fix for each gap.

    Args:
        folder: the project's root folder (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        detail: "compact" (the default: the score and the gaps, at most 20) or "full" (passing checks too).

    Returns:
        {folder, score: "N of M" (the checks that apply), applies: [when names found], checks: [{id, level, ok, file,
        detail}], failed, counts, passed, more?}. level is required, recommended or conditional; detail says what to
        add or fix.

    Raises:
        ReadinessError: the folder is missing, not a folder, or outside the allowed folders; or detail isn't
            "compact" or "full".
    """
    findings.check_detail(detail, ReadinessError)
    import readroots
    try:
        root = Path(readroots.resolve_folder(folder))
    except readroots.OutsideRoots as exc:
        raise ReadinessError(str(exc)) from None

    found = conditions(root)
    rows = [_item_row(root, item) for item in checklist()["items"]
            if not item.get("when") or found.get(item["when"], False)]
    if found["docker"]:
        rows += _dockerfile_rows(root)
    rows += _compose_rows(root)
    passed = sum(1 for r in rows if r["ok"])
    result = {"folder": str(root), "score": f"{passed} of {len(rows)}", "applies": sorted(k for k, v in found.items() if v),
              "checks": rows, "failed": len(rows) - passed}
    return findings.compact(result, detail)
