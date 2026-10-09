"""Compact output for the scanner tools (strapi.check, code.dead_scan, code.webhook_retry_check), the same idea as
bugs.list's compact default. A live dead_scan on the tool server returned 87 rows (about 10K tokens) on 2026-09-30,
too much for a server that exists to save tokens. By default a scan returns its totals, at most MAX_ROWS failing
rows, a few information rows, and file findings of one kind grouped by folder; detail="full" returns every row.
"""
from __future__ import annotations

import posixpath
from collections import Counter, OrderedDict

MAX_ROWS = 20
MAX_INFO = 5
DETAILS = ("compact", "full")


def check_detail(detail: str, error: type[Exception]) -> None:
    if detail not in DETAILS:
        raise error('detail must be "compact" or "full"')


def compact(result: dict, detail: str = "compact", group_ids: tuple[str, ...] = ()) -> dict:
    """result with its checks trimmed. Rows whose id is in group_ids become one row per folder with a count."""
    checks = result.get("checks") or []
    counts = Counter(c["id"] for c in checks if not c.get("ok"))
    result = {**result, "counts": dict(counts),
              "passed": sorted({c["id"] for c in checks if c.get("ok") and not c.get("info")})}
    if detail == "full":
        return result
    failing = [c for c in checks if not c.get("ok")]
    grouped: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
    rows = []
    for c in failing:
        if c["id"] in group_ids and c.get("file"):
            folder = posixpath.dirname(str(c["file"]).replace("\\", "/")) or "."
            g = grouped.setdefault((c["id"], folder), {"id": c["id"], "kb": c.get("kb"), "ok": False, "folder": folder,
                                                       "files": 0, "examples": []})
            g["files"] += 1
            if len(g["examples"]) < 3:
                g["examples"].append(posixpath.basename(str(c["file"]).replace("\\", "/")))
        else:
            rows.append(c)
    rows = (list(grouped.values()) + rows)
    info = [c for c in checks if c.get("ok") and c.get("info")][:MAX_INFO]
    passed = [c for c in checks if c.get("ok") and not c.get("info")]
    shown = rows[:MAX_ROWS]
    result["checks"] = shown + info + ([] if failing else passed[:MAX_ROWS])
    hidden = len(rows) - len(shown)
    if hidden or len(checks) > len(result["checks"]):
        result["more"] = (f"{hidden} more failing rows, " if hidden else "") + 'call again with detail="full" for every row'
    return result
