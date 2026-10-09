"""Fail if any Markdown file links to a local file that doesn't exist. Run from the repo root."""
import re
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)\s]+)\)|(?:href|src)=\"([^\"]+)\"")


def broken(root: Path) -> list[str]:
    out = []
    for md in sorted(root.rglob("*.md")):
        if any(p.startswith(".") and p not in (".github",) for p in md.relative_to(root).parts[:-1]):
            continue
        for no, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
            for m in LINK.finditer(line):
                target = (m.group(1) or m.group(2)).split("#")[0]
                if not target or re.match(r"[a-z]+:", target) or "{{" in target:
                    continue
                if not (md.parent / target).exists():
                    out.append(f"{md.relative_to(root).as_posix()}:{no}: {target}")
    return out


if __name__ == "__main__":
    bad = broken(Path("."))
    for b in bad:
        print("BROKEN", b)
    sys.exit(1 if bad else 0)
