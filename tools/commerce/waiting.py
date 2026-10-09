"""commerce.requests.list. See ../../capabilities/commerce/requests.md.

The owner, 2026-10-08: "when i approve from the dash, does the agent get informed?" It wasn't: an app that asked to
publish, update a live product or edit an Etsy listing only found out by noticing the shop had changed, and reported
things as still waiting after they'd been approved. This lists an app's own requests with the owner's decisions.
"""
from __future__ import annotations

from registry import tool
from tools.commerce import _http as h


def _target(product_id: str) -> dict:
    pid = str(product_id or "")
    return {"listing_id": pid[5:]} if pid.startswith("etsy-") else {"product_id": pid}


@tool(name="requests.list", category="commerce", doc="commerce/requests.md")
def requests_list(days: int = 7) -> dict:
    """Your shop requests and the owner's decisions on them: Printify publishes, live-product updates and Etsy listing
    edits. Check this rather than assuming something still waits for the owner. An app sees its own requests; the
    owner sees everyone's.

    Args:
        days: how far back the decisions go, 1-7 (default 7).

    Returns:
        {"waiting": [{"id", "kind", "product_id" or "listing_id", "summary", "asked_at", "expires_at", "last_error"?}],
         "decided": [{"id", "kind", "product_id" or "listing_id", "summary", "asked_at", "decided_at", "outcome":
         approved|declined|expired, "note"?}]}. "last_error": the owner approved it but it failed, so it still waits.
    """
    import clients
    import printify_pending as pp
    days = h.clamp(days, 1, 7, "days")
    me = clients.current_client.get()

    def mine(row: dict) -> bool:
        return me is None or row.get("client") == me

    waiting = [{"id": i["id"], "kind": i.get("action") or "publish", **_target(i.get("product_id")),
                "summary": i.get("summary"), "asked_at": i.get("at"), "expires_at": float(i.get("at") or 0) + pp.TTL_S,
                **({"last_error": i["last_error"]} if i.get("last_error") else {}),
                **({"client": i.get("client")} if me is None else {})}
               for i in pp.list_pending() if mine(i)]
    decided = [{"id": r.get("id"), "kind": r.get("kind"), **_target(r.get("product_id")), "summary": r.get("summary"),
                "asked_at": r.get("asked_at"), "decided_at": r.get("decided_at"), "outcome": r.get("outcome"),
                **({"note": r["note"]} if r.get("note") else {}), **({"client": r.get("client")} if me is None else {})}
               for r in pp.history(days) if mine(r)]
    return {"waiting": waiting, "decided": decided[:100], "days": days}
