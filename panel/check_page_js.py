"""Check that every inline <script> in the Control Panel page, and every script file beside it, parses (node --check).
The deploy script runs it first.

KB-0035 (2026-09-29): a wording change put an apostrophe inside a single-quoted string, the page's whole script
failed to parse, and the live Control Panel stayed on "Connecting". Python tests can't see that, and a green /health
doesn't either. Since the split of 2026-10-28 most of the page's code is in static/panel*.js and pages-*.js, so those
are checked too. Usage: python check_page_js.py [page.html]   (exit 1 when a script doesn't parse)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from html.parser import HTMLParser

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html")


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.found: list[str] = []
        self._in = False

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not any(k == "src" for k, _ in attrs):
            self._in = True
            self.found.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = False

    def handle_data(self, data):
        if self._in:
            self.found[-1] += data


def inline_scripts(html: str) -> list[str]:
    """Every inline <script>'s code, by an HTML parser (a regex misses </script > and the like: CodeQL bad-tag-filter)."""
    p = _Scripts()
    p.feed(html)
    p.close()
    return p.found


def _node_check(path: str) -> str:
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=60)
    return r.stderr.strip()[:600] if r.returncode else ""


def problems(html: str) -> list[str]:
    out = []
    for i, js in enumerate(inline_scripts(html)):
        fd, path = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(js)
            err = _node_check(path)
        finally:
            os.remove(path)
        if err:
            out.append(f"script {i + 1}: {err}")
    return out


def file_problems(folder: str) -> list[str]:
    """The page's own script files (static/*.js; the vendored ones in static/vendor are someone else's)."""
    return [f"{name}: {err}" for name in sorted(os.listdir(folder)) if name.endswith(".js")
            for err in [_node_check(os.path.join(folder, name))] if err]


if __name__ == "__main__":
    page = sys.argv[1] if len(sys.argv) > 1 else PAGE
    with open(page, encoding="utf-8") as f:
        found = problems(f.read()) + file_problems(os.path.dirname(os.path.abspath(page)))
    for p in found:
        print(p, file=sys.stderr)
    print("The page's scripts parse." if not found else f"{len(found)} script(s) don't parse.")
    sys.exit(1 if found else 0)
