"""code.dead_scan. See ../../capabilities/code/dead-scan.md.

It walks a Python project's import graph from
the real entry points (never a filename grep) and reports:
  KB-0026  files no entry point reaches (unreachable code still costs: reviews, checkers, context)
  KB-0029  the reason for a graph, not a grep: a checker pointed at a dead duplicate because grep saw its name
  KB-0030  names an __init__.py re-exports that nothing else uses: deleting their module breaks the package import
It also lists the call sites a static walk can't follow (importlib, __import__, subprocess launches of .py files,
packaging entry points); while any exist, "dead" reads "unreached from the given entries". Python only.
"""
from __future__ import annotations

import ast
import os
import re
from collections import deque
from pathlib import Path

import findings
import readroots
from registry import tool

DEFAULT_EXCLUDES = {".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".git",
                    "main.build", "main.dist", "dist_nuitka"}
MAX_FILES = 5000
MAX_BYTES = 2_000_000
_LAUNCHERS = {"Popen", "run", "check_call", "check_output", "call", "system", "spawnl", "spawnv", "spawnle", "spawnve"}


class DeadScanError(ValueError):
    """Bad input: a root outside the allowed roots, or an entry file that isn't a .py file inside the root."""


def _read(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def _module_name(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    parts = (rel[:-3] if rel.endswith(".py") else rel).split(os.sep)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _py_files(root: Path, excludes: set[str]):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for name in filenames:
            full = Path(dirpath) / name
            if name.endswith(".py") and readroots.inside(root, full):
                count += 1
                if count > MAX_FILES:
                    return
                yield os.path.normpath(str(full))


def _ancestors(dotted: str, index: dict) -> list[str]:
    parts = dotted.split(".")
    return [index[".".join(parts[:i])] for i in range(1, len(parts)) if ".".join(parts[:i]) in index]


def _resolve(dotted: str, index: dict) -> list[str]:
    return _ancestors(dotted, index) + ([index[dotted]] if dotted in index else [])


def _own_package(path: str, root: str) -> str:
    dotted = _module_name(path, root)
    return dotted if os.path.basename(path) == "__init__.py" else ".".join(dotted.split(".")[:-1])


def _relative(pkg: str, level: int, module: str | None) -> str:
    parts = pkg.split(".") if pkg else []
    strip = level - 1
    if strip > 0:
        parts = parts[:-strip] if strip <= len(parts) else []
    base = ".".join(parts)
    return (base + "." + module if base else module) if module else base


def _imports(path: str, root: str, index: dict, notes: list):
    """(files this file's imports reach, [(name, from_module)] for each `from X import name`)."""
    source = _read(path)
    if source is None:
        return [], []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        notes.append({"id": "syntax-error", "ok": True, "info": True, "file": path, "line": e.lineno,
                      "detail": "doesn't parse: counted reachable, its imports not followed"})
        return [], []
    targets, names, pkg = [], [], _own_package(path, root)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets += _resolve(alias.name, index)
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "") if node.level == 0 else _relative(pkg, node.level, node.module)
            if not base:
                continue
            targets += _resolve(base, index)
            for alias in node.names:
                if alias.name == "*":
                    continue
                if base + "." + alias.name in index:  # `from pkg import sub` where sub is a module
                    targets.append(index[base + "." + alias.name])
                names.append((alias.asname or alias.name, base))
    return targets, names


def _call_name(func) -> str | None:
    return func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None


def _dynamic_sites(files: list[str], root: Path) -> list[dict]:
    """Best-effort pattern match for entry points a static walk can't follow."""
    sites = []
    for path in files:
        source = _read(path)
        if source is None:
            continue
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in ("import_module", "__import__"):
                arg = node.args[0] if node.args else None
                shown = repr(arg.value) if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "<computed>"
                sites.append({"file": path, "line": node.lineno, "kind": "dynamic-import", "detail": f"{name}({shown})"})
            elif name in _LAUNCHERS:
                strings = [n.value for a in list(node.args) + [k.value for k in node.keywords]
                           for n in ast.walk(a) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                py = [s for s in strings if s.endswith(".py")]
                if py or "-m" in strings:
                    sites.append({"file": path, "line": node.lineno, "kind": "process-launch",
                                  "detail": "launches " + (", ".join(map(repr, py)) if py else "python -m <computed module>")})
    for fname in ("pyproject.toml", "setup.cfg", "setup.py"):
        fpath = root / fname
        if fpath.is_file() and readroots.inside(root, fpath):
            text = _read(str(fpath)) or ""
            if "entry_points" in text or "console_scripts" in text or "[project.scripts]" in text:
                sites.append({"file": str(fpath), "line": None, "kind": "packaging-entry-point",
                              "detail": "declares scripts or entry points: each is another real entry point"})
    return sites


@tool(name="dead_scan", category="code", doc="code/dead-scan.md")
def dead_scan(root: str, entries: list[str], exclude: list[str] | None = None, detail: str = "compact") -> dict:
    """Find Python files no entry point reaches, and __init__.py re-exports nothing else uses.

    Args:
        root: the project folder (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        entries: the real entry-point .py files, relative to root (e.g. ["main.py", "server.py"]).
        exclude: extra folder names to skip, on top of .venv, node_modules, build folders and __pycache__.
        detail: "compact" (the default: totals, unreached files grouped by folder, at most 20 rows) or "full".

    Returns:
        {root, unsure: bool, checks: [{id, kb, ok, detail, file, line, info?}], failed: int, summary}. ids:
        "dead-file" (or "unreached-file" while unsure), "unused-reexport", "cant-follow" (info), "syntax-error" (info).
        file is relative to root.

    Raises:
        DeadScanError: root outside the allowed roots, no entries, or an entry that isn't a .py file inside root.
    """
    findings.check_detail(detail, DeadScanError)
    try:
        real = readroots.resolve_folder(root)
    except readroots.OutsideRoots as e:
        raise DeadScanError(str(e)) from None
    if not entries:
        raise DeadScanError("give at least one entry-point file")
    entry_paths = []
    for e in entries:
        p = (real / e).resolve()
        if p.suffix != ".py" or not p.is_file() or not readroots.inside(real, p):
            raise DeadScanError(f"entry {e!r} isn't a .py file inside {root}")
        entry_paths.append(os.path.normpath(str(p)))
    excludes = DEFAULT_EXCLUDES | set(exclude or [])
    rootstr = str(real)
    files = list(_py_files(real, excludes))
    index = {_module_name(p, rootstr): p for p in files}
    notes: list[dict] = []

    reachable, visited, queue, init_imports = set(entry_paths), set(), deque(entry_paths), {}
    for entry in entry_paths:  # an entry's own ancestor packages run too, as with any import
        for anc in _ancestors(_module_name(entry, rootstr), index):
            reachable.add(anc)
            queue.append(anc)
    while queue:
        path = queue.popleft()
        if path in visited:
            continue
        visited.add(path)
        targets, names = _imports(path, rootstr, index, notes)
        if os.path.basename(path) == "__init__.py":
            init_imports.setdefault(path, []).extend(names)
        for t in targets:
            reachable.add(t)
            if t not in visited:
                queue.append(t)

    reexports, cache = [], {}
    for init_path, names in init_imports.items():
        for name, src in names:
            # Not the __init__ itself, and not the module that defines the name: its own `class Thing` always matches
            # (the draft searched it too, so it could never report anything).
            skip = {init_path, index.get(src), index.get(src + "." + name)}
            others = [p for p in reachable if p not in skip]
            pattern = re.compile(r"\b%s\b" % re.escape(name))
            used = False
            for other in others:
                if other not in cache:
                    cache[other] = _read(other) or ""
                if pattern.search(cache[other]):
                    used = True
                    break
            if not used:
                reexports.append((init_path, name, src))

    sites = _dynamic_sites(files, real)
    unsure = bool(sites)
    rel = lambda p: os.path.relpath(p, rootstr) if p else None  # noqa: E731
    checks = [{"id": "cant-follow", "kb": None, "ok": True, "info": True, "file": rel(s["file"]), "line": s["line"],
               "detail": f"[{s['kind']}] {s['detail']}: pass its target as an entry too, or treat unreached files as unproven"}
              for s in sites]
    checks += [{**n, "kb": None, "file": rel(n["file"])} for n in notes]
    for p in sorted(set(files) - reachable):
        checks.append({"id": "unreached-file" if unsure else "dead-file", "kb": "KB-0026", "ok": False, "file": rel(p),
                       "line": None, "detail": "not reached from the given entries" + (" (see cant-follow)" if unsure else "")})
    for init_path, name, src in reexports:
        checks.append({"id": "unused-reexport", "kb": "KB-0030", "ok": False, "file": rel(init_path), "line": None,
                       "detail": f"re-exports {name!r} from {src}, used nowhere else in the reachable code. Before deleting "
                                 f"{src}, check outside code doesn't do `from <package> import {name}`"})
    failed = sum(1 for c in checks if not c["ok"])
    noun = "unreached" if unsure else "dead"
    return findings.compact({"root": rootstr, "unsure": unsure, "checks": checks, "failed": failed,
            "summary": f"{len(files)} files, {len(reachable & set(files))} reached, "
                       f"{sum(1 for c in checks if c['id'].endswith('-file'))} {noun}, {len(reexports)} re-exports to check"},
                            detail, group_ids=("dead-file", "unreached-file"))
