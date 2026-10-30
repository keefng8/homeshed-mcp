"""HomeShed Pro's routes: the panel's Pro card and the known-bugs feed. Owner only, like every route besides /mcp.

- GET /pro: the membership as the Pro website last answered, plus effective_pro: what Pro features actually get (a
  recent membership holds while the site can't be reached: pro.check).
- POST /pro/connect {"key": "mav_..."}: check a pasted Pro key with the site and keep it (mavis_pro.connect_key). No
  password is ever asked for.
- DELETE /pro: forget the key.
- GET /pro/packs: the rule packs on offer. GET /pro/packs/{slug}: one pack's content.
- POST /pro/bugs/sync: fetch the known-bugs feed into bugs.find (tools/bugs/pro_sync.py).
- GET /pro/helper-plan: the helper PC build plan from the signed Pro package, while Pro is active.
"""
from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import JSONResponse

import mavis_pro
import pro

_body = None  # server.py's JSON-body reader (raises ValueError on bad JSON), set by register()


async def status_route(request: Request) -> JSONResponse:
    fresh = request.query_params.get("fresh") == "true"
    status = await asyncio.to_thread(mavis_pro.status, fresh)
    effective, _ = await asyncio.to_thread(pro.check)
    return JSONResponse({**status, "effective_pro": effective})


async def connect_route(request: Request) -> JSONResponse:
    try:
        body = await _body(request)
        out = await asyncio.to_thread(mavis_pro.connect_key, str((body or {}).get("key") or ""))
    except ValueError:  # not JSON: say so without echoing anything that was sent
        return JSONResponse({"error": 'Send the key as JSON: {"key": "mav_..."}.'}, status_code=400)
    except mavis_pro.MavisProError as exc:  # it says what to do, and never contains the key
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(out)


async def disconnect_route(request: Request) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(mavis_pro.disconnect))


async def packs_route(request: Request) -> JSONResponse:
    return JSONResponse(await asyncio.to_thread(mavis_pro.packs))


async def pack_route(request: Request) -> JSONResponse:
    try:
        return JSONResponse({"pack": await asyncio.to_thread(mavis_pro.pack, request.path_params["slug"])})
    except mavis_pro.MavisProError as exc:
        text = str(exc)
        status = 404 if "no pack" in text or "Unknown" in text else 400 if "isn't connected" in text else 502
        return JSONResponse({"error": text}, status_code=status)


async def bugs_sync_route(request: Request) -> JSONResponse:
    from tools.bugs import known, pro_sync
    try:
        return JSONResponse(await asyncio.to_thread(pro_sync.sync))
    except known.BugsError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def tools_route(request: Request) -> JSONResponse:
    """GET: the Pro tools package installed here and whether it loaded. POST: download and unpack the latest one
    (pro_plugin.update); it loads at the next restart."""
    import pro_plugin
    if request.method == "GET":
        return JSONResponse({"installed": pro_plugin.installed(), "loaded": pro_plugin.load()})
    try:
        return JSONResponse(await asyncio.to_thread(pro_plugin.update))
    except pro_plugin.ProPluginError as exc:  # it says what to do, and never contains the key
        return JSONResponse({"error": str(exc)}, status_code=400)


HELPER_PLAN = "helper-plan.md"  # inside the Pro package: the plan a member's own AI builds a helper PC to


def _helper_plan() -> dict:
    import pro_plugin
    on_pro, why = pro.check()
    if not on_pro:
        return {"pro": False, "reason": why or "The helper PC plan comes with HomeShed Pro."}
    if not pro_plugin.load().get("loaded"):  # only a package the release key signed (pro_plugin.trusted)
        return {"pro": True, "plan": "", "reason": "Get the Pro tools package first:  homeshed-mcp pro update"}
    path = pro_plugin.pro_dir() / pro_plugin.installed()["version"] / pro_plugin.PACKAGE / HELPER_PLAN
    try:
        return {"pro": True, "plan": path.read_text(encoding="utf-8")}
    except OSError:
        return {"pro": True, "plan": "", "reason": "This Pro package has no helper PC plan yet: homeshed-mcp pro update"}


async def helper_plan_route(request: Request) -> JSONResponse:
    """The helper PC plan (the owner, 2026-10-30: "only pro"): from the signed Pro package, while Pro is active. The
    helper itself isn't shipped; the member's own AI builds one to this plan (the panel's Settings > System > Set up)."""
    return JSONResponse(await asyncio.to_thread(_helper_plan))


ROUTES = [
    ("/pro/helper-plan", ["GET"], helper_plan_route),
    ("/pro", ["GET"], status_route),
    ("/pro/connect", ["POST"], connect_route),
    ("/pro", ["DELETE"], disconnect_route),
    ("/pro/packs", ["GET"], packs_route),
    ("/pro/packs/{slug}", ["GET"], pack_route),
    ("/pro/bugs/sync", ["POST"], bugs_sync_route),
    ("/pro/tools", ["GET", "POST"], tools_route),
]


def register(mcp, body) -> None:
    global _body
    _body = body
    for path, methods, handler in ROUTES:
        mcp.custom_route(path, methods=methods)(handler)
