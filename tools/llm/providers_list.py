"""llm.providers.list. See ../../capabilities/llm/relay.md."""
from __future__ import annotations

from registry import tool
from tools.llm import providers as prov


@tool(name="providers.list", category="llm", doc="llm/relay.md")
def providers_list() -> dict:
    """Which language-model providers llm.chat can relay to, and whether each can be used now: "enabled" (the owner's
    switch; clients can read it, not change it), "configured" (its key stored: yes/no only, never the key), "usable"
    ("why_not" says why not), today's request cap and use, the token cap and tokens used, today's cost (all apps and
    per client), the default model and every model's price per 1M tokens. Read-only; no request leaves HomeShed.

    Returns:
        {"providers": [{"provider", "label", "enabled", "configured", "usable", "why_not"?, "key_name", "daily_cap",
          "used_today", "daily_token_cap", "tokens_today", "cost_today_usd", "by_client_today", "default_model",
          "pricing_url", "models": [{"model", "usd_per_1m": {"input", "cached_input", "output"}, "note"?}]}],
         "planned", "how"}
    """
    return {"providers": prov.catalogue(), "planned": list(prov.PLANNED),
            "how": "llm.chat(messages=[{role, content}], system=..., model=..., max_tokens<=2048) - paid per token. "
                   "Switches and daily caps: owner only, Control Panel > Settings > AI and tools > Language models."}
