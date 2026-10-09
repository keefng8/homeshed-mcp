"""image.providers.list. See ../../capabilities/image/providers.md."""
from __future__ import annotations

from registry import tool
from tools.image import providers as prov


@tool(name="providers.list", category="image", doc="image/providers.md")
def providers_list(commercial_only: bool = False) -> dict:
    """Which image providers and models exist, whether each is switched on ("enabled": the owner's switch in the
    Control Panel; clients can read it, not change it), configured (its key stored: yes/no only, never the key),
    "usable" now (on, configured, under its daily cap; "why_not" says why not), its licence and whether outputs may be sold (commercial_use: true / false / "check"), and its cost (free
    local, or paid per image with an estimate). Read-only; asks the local GPU service which local models are
    installed (5 s timeout; "local_status": "unreachable" if it can't).

    Args:
        commercial_only: only models with commercial_use true.

    Returns:
        {"providers": [{"provider", "enabled", "configured", "usable", "why_not"?, "paid", "cost_note",
          paid only: "key_name", "pricing_url", "daily_cap", "used_today"; "models": [{"model", "enabled",
          "license", "commercial_use", "terms_url", "cost_estimate_usd", local only: "installed", "default"?,
          "note"?}]}],
         "default_provider", "local_status", "how"}
    """
    ready = prov.local_ready()
    rows = prov.catalogue(ready)
    if commercial_only:
        for r in rows:
            r["models"] = [m for m in r["models"] if m["commercial_use"] is True]
        rows = [r for r in rows if r["models"]]
    try:
        default = prov.default_provider() or prov.LOCAL
    except prov.ProviderError as exc:
        default = f"invalid ({exc})"
    return {"providers": rows, "default_provider": default,
            "local_status": "reachable" if ready is not None else "unreachable",
            "how": "image.generate(prompt, provider=..., model=...) - paid providers cost money per image; "
                   "require_commercial=True refuses anything whose outputs can't be sold. Switches and daily caps: "
                   "owner only, Control Panel > Settings > Image generation."}
