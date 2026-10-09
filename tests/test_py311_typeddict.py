"""The server supports Python 3.11 (requires-python), where pydantic refuses typing.TypedDict in a validated signature:
the whole server then fails to import (the Github prepper's verify, 2026-10-06, tools/llm/chat.py). No 3.11 here, so
this reads the source: tool code takes TypedDict from typing_extensions."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TYPING_TYPEDDICT = re.compile(r"^\s*from\s+typing\s+import\s+[^\n]*\bTypedDict\b", re.M)


def test_no_tool_takes_typeddict_from_typing():
    bad = [str(p.relative_to(ROOT)) for p in (ROOT / "tools").rglob("*.py")
           if TYPING_TYPEDDICT.search(p.read_text(encoding="utf-8"))]
    bad += [p.name for p in ROOT.glob("*.py") if TYPING_TYPEDDICT.search(p.read_text(encoding="utf-8"))]
    assert bad == [], f"use `from typing_extensions import TypedDict` in: {bad}"
