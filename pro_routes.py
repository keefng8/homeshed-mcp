"""HomeShed Pro's routes: the panel's Pro card and the known-bugs feed. Owner only, like every route besides /mcp.

- GET /pro: the membership as the Pro website last answered, plus effective_pro: what Pro features actually get (a
  recent membership holds while the site can't be reached: pro.check).
- POST /pro/connect {"key": "mav_..."}: check a pasted Pro key with the site and keep it (mavis_pro.connect_key). No
  password is ever asked for.
- DELETE /pro: forget the key.
- GET /pro/packs: the rule packs on offer. GET /pro/packs/{slug}: one pack's content.
- POST /pro/bugs/sync: fetch the known-bugs feed into bugs.find (tools/bugs/pro_sync.py).
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


ROUTES = [
    ("/pro", ["GET"], status_route),
    ("/pro/connect", ["POST"], connect_route),
    ("/pro", ["DELETE"], disconnect_route),
    ("/pro/packs", ["GET"], packs_route),
    ("/pro/packs/{slug}", ["GET"], pack_route),
    ("/pro/bugs/sync", ["POST"], bugs_sync_route),
]


def register(mcp, body) -> None:
    global _body
    _body = body
    for path, methods, handler in ROUTES:
        mcp.custom_route(path, methods=methods)(handler)
