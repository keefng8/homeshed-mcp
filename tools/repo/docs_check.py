"""repo.docs_check. See ../../capabilities/repo/docs-check.md.

Docs that match the code: ported from the Github prepper's scripts/check_docs.py (HomeShed's public CI, 2026-09-30),
which found six doc claims the code didn't back that night. Three checks:
1. anchors: every `file.md#anchor` and `#anchor` link (Markdown and href="#...") in README.md and docs/ points at a real
   heading (GitHub's slug rules) or an explicit <a id="...">;
2. settings: every variable in the first column of docs/configuration.md's tables appears as a quoted name in the
   code (.py or .json outside tests/ and scripts/);
3. commands: every `PACKAGE subcommand --flag` in the docs exists in the CLI file's argparse setup.
Checks 2 and 3 need the code; without the CLI file they're skipped with a note, not failed. Read-only, inside
READ_ALLOWED_ROOTS; walks skip virtualenvs, node_modules and .git, and stop after MAX_FILES files.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import findings
from registry import tool

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ID = re.compile(r"""<a\s+(?:id|name)=["']([^"']+)["']""")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
HREF = re.compile(r"""href=["']([^"']+)["']""")
QUOTED_NAME = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")
TABLE_VAR = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")
SKIP_DIRS = {".git", "node_modules", "__pycache__", "tests", "scripts", "dist", "build"}
MAX_FILES, MAX_BYTES = 3000, 400_000


class DocsCheckError(RuntimeError):
    """A folder outside the allowed ones, missing, or not a folder; or a bad detail value."""


def _text(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(MAX_BYTES)
    except OSError:
        return ""


def slug(text: str) -> str:
    """GitHub's heading anchor: lower case, drop punctuation except - and _, spaces become -."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link in a heading keeps only its text
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    found, seen, fenced = set(), {}, False
    for line in _text(md).splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        found |= set(EXPLICIT_ID.findall(line))
        m = HEADING.match(line)
        if m:
            s = slug(m.group(2))
            n = seen.get(s, 0)
            found.add(s if n == 0 else f"{s}-{n}")
            seen[s] = n + 1
    return found


def _row(cid: str, file: str, detail: str, ok: bool = False) -> dict:
    return {"id": cid, "ok": ok, "file": file, "detail": detail}


def check_links(root: Path, docs: list[Path]) -> list[dict]:
    rows, cache = [], {}
    for md in docs:
        text = _text(md)
        for target in MD_LINK.findall(text) + HREF.findall(text):
            if "#" not in target or "://" in target:
                continue
            file_part, anchor = target.split("#", 1)
            dest = md if not file_part else (md.parent / file_part).resolve()
            if dest.suffix != ".md" or not dest.is_file() or not dest.is_relative_to(root):
                continue  # a missing file is a broken-link check, not an anchor check
            if dest not in cache:
                cache[dest] = anchors(dest)
            if anchor not in cache[dest]:
                rows.append(_row("broken-anchor", md.relative_to(root).as_posix(),
                                 f"#{anchor} isn't a heading in {dest.relative_to(root).as_posix()}"))
    return rows


def code_names(root: Path) -> set[str]:
    names, seen = set(), 0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".venv")
                   and not (Path(base) / d / "pyvenv.cfg").is_file()]
        for name in files:
            if name.endswith((".py", ".json")):
                names |= set(QUOTED_NAME.findall(_text(Path(base) / name)))
                seen += 1
                if seen >= MAX_FILES:
                    return names
    return names


def check_settings(root: Path) -> list[dict]:
    config = root / "docs" / "configuration.md"
    if not config.is_file():
        return []
    documented = set()
    for line in _text(config).splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 2 and not set(cells[1].strip()) <= {"-", ":"}:
            documented |= set(TABLE_VAR.findall(cells[1]))
    read = code_names(root)
    return [_row("unread-setting", "docs/configuration.md", f"{v} is documented, but no code reads it")
            for v in sorted(documented - read)]


def check_commands(root: Path, package: str, docs: list[Path], cli_file: str) -> list[dict]:
    cli = _text(root / cli_file)
    subs = set(re.findall(r"""add_parser\(\s*["']([\w-]+)["']""", cli))
    flags = set(re.findall(r"""add_argument\(\s*["'](--[\w-]+)["']""", cli))
    cmd = re.compile(rf"(?:uvx\s+|`|^\s*){re.escape(package)}\s+([a-z][\w-]*)([^`\n]*)", re.M)
    rows = []
    for md in docs:
        where = md.relative_to(root).as_posix()
        for sub, rest in cmd.findall(_text(md)):
            if sub not in subs:
                rows.append(_row("unknown-command", where, f"`{package} {sub}` isn't a command in {cli_file}"))
            for flag in re.findall(r"(?<![\w-])(--[a-z][\w-]*)", rest.split(" -- ")[0]):
                if flag not in flags:
                    rows.append(_row("unknown-option", where, f"`{package} {sub} {flag}`: {flag} isn't an option in "
                                                              f"{cli_file}"))
    return rows


def package_name(root: Path) -> str | None:
    m = re.search(r'^name\s*=\s*"([^"]+)"', _text(root / "pyproject.toml"), re.M)
    return m.group(1) if m else None


@tool(name="docs_check", category="repo", doc="repo/docs-check.md")
def docs_check(folder: str = ".", package: str = "", cli: str = "cli.py", detail: str = "compact") -> dict:
    """Do the docs match the code? Link anchors, documented settings, and documented CLI commands and options.

    Args:
        folder: the project's root folder (must be inside READ_ALLOWED_ROOTS; see readroots.py).
        package: the command name the docs use (default: [project] name in pyproject.toml).
        cli: the file with the argparse setup, relative to folder (default cli.py; Python projects only).
        detail: "compact" (the default: totals and at most 20 problems) or "full" (every row).

    Returns:
        {folder, docs: int (files read), checks: [{id, ok, file, detail}], failed, counts, passed, more?}. ids:
        broken-anchor, unread-setting, unknown-command, unknown-option; an info row says when the code checks
        were skipped (no CLI file).

    Raises:
        DocsCheckError: the folder is missing, not a folder, or outside the allowed folders; the cli path leaves the
            folder; or detail isn't "compact" or "full".
    """
    findings.check_detail(detail, DocsCheckError)
    import readroots
    try:
        root = Path(readroots.resolve_folder(folder))
    except readroots.OutsideRoots as exc:
        raise DocsCheckError(str(exc)) from None
    if not (root / cli).resolve().is_relative_to(root):
        raise DocsCheckError("cli must be a file inside the folder")
    docs_dir = root / "docs"
    docs = ([root / "README.md"] if (root / "README.md").is_file() else []) + (
        sorted(p for p in docs_dir.rglob("*.md") if p.is_file())[:MAX_FILES] if docs_dir.is_dir() else [])
    rows = check_links(root, docs)
    if (root / cli).is_file():
        rows += check_settings(root)
        name = package or package_name(root)
        if name:
            rows += check_commands(root, name, docs, cli)
    else:
        rows.append({"id": "code-checks-skipped", "ok": True, "info": True, "file": cli,
                     "detail": f"no {cli} here (a docs-only checkout?): settings and commands weren't checked"})
    failed = sum(1 for r in rows if not r["ok"])
    return findings.compact({"folder": str(root), "docs": len(docs), "checks": rows, "failed": failed}, detail)
