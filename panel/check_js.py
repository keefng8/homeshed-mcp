"""Syntax-check the inline <script> blocks of the Control Panel's pages with `node --check`.

Run before every dashboard deploy (the page is one big HTML file; a JS syntax error blanks it).
Exit code 0 = every page parses; 1 = a page failed (the error is printed); 2 = node is missing.
Usage: python check_js.py            (checks static/*.html next to this file)
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_page_js import inline_scripts  # noqa: E402 - one HTML parser for both checks


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("node not found: install Node.js to check the pages' JavaScript")
        return 2
    failed = 0
    for page in sorted((Path(__file__).parent / "static").glob("*.html")):
        code = "\n;\n".join(inline_scripts(page.read_text(encoding="utf-8")))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(code)
        r = subprocess.run([node, "--check", f.name], capture_output=True, text=True)
        Path(f.name).unlink(missing_ok=True)
        if r.returncode == 0:
            print(f"OK    {page.name}")
        else:
            failed += 1
            print(f"FAIL  {page.name}\n{r.stderr[:1500]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
