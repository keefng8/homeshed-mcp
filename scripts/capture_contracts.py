"""Capture the SHAPE of each live integration's answer (keys and value types, never values) for the contract tests.

Run inside the tool server, where the real addresses and the Pro key are (read-only GETs only):
    docker exec -i -w /app -e PYTHONPATH=/app mcp-server-mcp-server-1 python - < scripts/capture_contracts.py
It prints one JSON object {name: shape}; save the parts into tests/fixtures/contracts/<name>.json. A shape holds no
values, so no key, token, name or address is ever written down.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, "/app")


def shape(value, depth: int = 0):
    """The structure of a JSON value: dicts by key, lists by their first item, scalars by type name."""
    if depth > 8:
        return "..."
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0], depth + 1)] if value else []
    return {bool: "bool", int: "int", float: "float", str: "str", type(None): "null"}.get(type(value), "other")


def main() -> int:
    import httpx

    out = {}
    import mavis_pro
    import pro_plugin
    import vault
    tok = vault.secret(mavis_pro.TOKEN)
    if tok:
        head = {"Authorization": f"Bearer {tok}"}
        out["pro_whoami"] = shape(httpx.get(mavis_pro.api_url() + "/pro-api/whoami", headers=head, timeout=20).json())
        out["pro_latest"] = shape(httpx.get(mavis_pro.api_url() + pro_plugin.LATEST_PATH, headers=head, timeout=20).json())
    gpu = (os.environ.get("GPU_SERVICE_URL") or os.environ.get("LOCAL_AI_DECIDE_BASE_URL") or "").rstrip("/")
    if gpu:
        out["rtk_daily"] = shape(httpx.get(f"{gpu}/stats/rtk/daily", timeout=20).json())
    sx = (os.environ.get("SEARXNG_URL") or "").rstrip("/")
    if sx:
        out["searxng_search"] = shape(httpx.get(f"{sx}/search", params={"q": "print on demand t-shirt sizes",
                                                                         "format": "json"}, timeout=30).json())
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
