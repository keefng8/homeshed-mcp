"""Docs match the code: exit 1 when the docs name something the code doesn't have.

1. Settings: every variable in the first column of docs/configuration.md's tables is read by the code (it appears as
   a quoted name in a .py or .json file outside tests/ and scripts/).
2. Links: every `file.md#anchor` and `#anchor` (Markdown links and href="#...") points at a real heading (GitHub's
   slug rules) or an explicit <a id="...">.
3. Commands: every `uvx PACKAGE <subcommand> --flag` in README.md and docs/ exists in cli.py's argparse setup.

Checks 1 and 3 need the code; without it (a docs-only checkout) they are skipped with a note, not failed.

    python scripts/check_docs.py [--root .] [--package NAME]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
EXPLICIT_ID = re.compile(r"""<a\s+(?:id|name)=["']([^"']+)["']""")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
HREF = re.compile(r"""href=["']([^"']+)["']""")
QUOTED_NAME = re.compile(r"""["']([A-Z][A-Z0-9_]{2,})["']""")
TABLE_VAR = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")


def slug(text: str) -> str:
    """GitHub's heading anchor: lower case, drop punctuation except - and _, spaces become -."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # a link in a heading keeps only its text
    text = text.replace("`", "").strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    found, seen, fenced = set(), {}, False
    for line in md.read_text(encoding="utf-8").splitlines():
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


def check_links(root: Path, docs: list[Path]) -> list[str]:
    problems, cache = [], {}
    for md in docs:
        text = md.read_text(encoding="utf-8")
        for target in MD_LINK.findall(text) + HREF.findall(text):
            if "#" not in target or "://" in target:
                continue
            file_part, anchor = target.split("#", 1)
            dest = md if not file_part else (md.parent / file_part).resolve()
            if dest.suffix != ".md" or not dest.exists():
                continue  # missing files are check_links.py's job
            if dest not in cache:
                cache[dest] = anchors(dest)
            if anchor not in cache[dest]:
                problems.append(f"{md.relative_to(root).as_posix()}: #{anchor} isn't a heading in {dest.name}")
    return problems


def code_names(root: Path) -> set[str]:
    names = set()
    for f in list(root.rglob("*.py")) + list(root.rglob("*.json")):
        rel = f.relative_to(root).parts
        if rel[0] in ("tests", "scripts", ".venv", "node_modules") or ".git" in rel:
            continue
        names |= set(QUOTED_NAME.findall(f.read_text(encoding="utf-8", errors="replace")))
    return names


def check_settings(root: Path) -> list[str]:
    config = root / "docs" / "configuration.md"
    if not config.exists():
        return []
    documented = set()
    for line in config.read_text(encoding="utf-8").splitlines():
        cells = line.split("|")
        if line.startswith("|") and len(cells) > 2 and not set(cells[1].strip()) <= {"-", ":"}:
            documented |= set(TABLE_VAR.findall(cells[1]))
    read = code_names(root)
    return [f"docs/configuration.md: {v} is documented but no code reads it" for v in sorted(documented - read)]


def check_commands(root: Path, package: str, docs: list[Path], cli_file: str = "cli.py") -> list[str]:
    cli = (root / cli_file).read_text(encoding="utf-8")
    subs = set(re.findall(r"""add_parser\(\s*["']([\w-]+)["']""", cli))
    flags = set(re.findall(r"""add_argument\(\s*["'](--[\w-]+)["']""", cli))
    cmd = re.compile(rf"(?:uvx\s+|`|^\s*){re.escape(package)}\s+([a-z][\w-]*)([^`\n]*)", re.M)
    problems = []
    for md in docs:
        for sub, rest in cmd.findall(md.read_text(encoding="utf-8")):
            where = md.relative_to(root).as_posix()
            if sub not in subs:
                problems.append(f"{where}: `{package} {sub}` isn't a command in cli.py")
            for flag in re.findall(r"(?<![\w-])(--[a-z][\w-]*)", rest.split(" -- ")[0]):
                if flag not in flags:
                    problems.append(f"{where}: `{package} {sub} {flag}`: {flag} isn't an option in cli.py")
    return problems


def package_name(root: Path) -> str | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return None
    m = re.search(r'^name\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--package")
    ap.add_argument("--cli", default="cli.py", help="the file with the argparse setup (Python projects only)")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    docs = [p for p in [root / "README.md", *sorted((root / "docs").rglob("*.md"))] if p.exists()]
    problems = check_links(root, docs)
    has_code = (root / args.cli).exists()
    if has_code:
        problems += check_settings(root)
        package = args.package or package_name(root)
        if package:
            problems += check_commands(root, package, docs, args.cli)
    else:
        print(f"note: no {args.cli} here (docs-only checkout): settings and commands not checked")
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
