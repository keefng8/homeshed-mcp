"""Check that every inline <script> in the Control Panel page parses (node --check). The deploy script runs it first.

KB-0035 (2026-09-29): a wording change put an apostrophe inside a single-quoted string, the page's whole script
failed to parse, and the live Control Panel stayed on "Connecting". Python tests can't see that, and a green /health
doesn't either. Usage: python check_page_js.py [page.html]   (exit 1 when a script doesn't parse)
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")
SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL)


def problems(html: str) -> list[str]:
    out = []
    for i, js in enumerate(SCRIPT_RE.findall(html)):
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(js)
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
        finally:
            os.remove(path)
        if r.returncode:
            out.append(f"script {i + 1}: {r.stderr.strip()[:600]}")
    return out


if __name__ == "__main__":
    page = sys.argv[1] if len(sys.argv) > 1 else PAGE
    with open(page, encoding="utf-8") as f:
        found = problems(f.read())
    for p in found:
        print(p, file=sys.stderr)
    print("The page's scripts parse." if not found else f"{len(found)} script(s) don't parse.")
    sys.exit(1 if found else 0)
