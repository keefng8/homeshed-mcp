"""Shared parts of the shop proxy tools (etsy.*, printify.*): a host-locked HTTP client and the owner's on/off switches.

The point (2026-10-03): an app on another machine uses the shops through these tools, so it never holds a shop key.
Keys stay in the Control Panel's vault and are read here, server-side, by name. See ../../capabilities/etsy/read.md.

SETTINGS joins runtime_settings (the Settings page draws it), the same way the image provider switches do.

Which vault entry each shop setting is read from is the owner's choice too (2026-10-06, to-do #55: "lets allow change
in settings to correct the api keys mismatch, then I can change them to exactly what is in the vault"). Only the
owner's Settings page can change them: no app ever names a vault entry in a call, so a client can't point these tools
at another secret (say an AI key) and have it sent to a shop's API.
"""
import re

NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")  # a vault entry name (the Control Panel's own rule)

# Setting key -> (the standard entry name, what it holds).
VAULT_NAMES = {
    "commerce_etsy_keystring_name": ("ETSY_KEYSTRING", "Etsy app keystring"),
    "commerce_etsy_shared_secret_name": ("ETSY_SHARED_SECRET", "Etsy app shared secret"),
    "commerce_etsy_refresh_token_name": ("ETSY_REFRESH_TOKEN", "Etsy shop refresh token"),
    "commerce_etsy_shop_id_name": ("ETSY_SHOP_ID", "Etsy shop number"),
    "commerce_printify_api_token_name": ("PRINTIFY_API_TOKEN", "Printify access token"),
    "commerce_printify_shop_id_name": ("PRINTIFY_SHOP_ID", "Printify shop number"),
    "social_threads_app_id_name": ("THREADS_APP_ID", "Threads app ID"),
    "social_threads_app_secret_name": ("THREADS_APP_SECRET", "Threads app secret"),
}

SETTINGS = {
    "commerce_etsy_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps read your Etsy shop",
        "help": "The etsy.* tools: shop details, listings and orders, read-only. Buyers' names, addresses and messages "
                "are left out. Needs the Etsy entries below under Keys and passwords. Off until you switch it on.",
    },
    "commerce_etsy_write_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps ask to edit your Etsy listings' details",
        "help": "The etsy.listing.update tool: an app can ASK to set a listing's category, materials, production "
                "partners, delivery profile and attributes (colour, occasion, recipient, holiday); nothing changes "
                "until you approve that one in the approvals bar. Title, tags and prices stay with Printify. At most "
                "ETSY_EDIT_DAILY_MAX a day (default 20). After switching on, press Connect Etsy again once: it then "
                "asks Etsy for listing edits (listings_w). Off until you switch it on.",
    },
    "commerce_etsy_deactivate_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps deactivate your Etsy listings",
        "help": "The etsy.listing.deactivate tool takes a listing off sale AT ONCE, without asking you (it's a brake, "
                "e.g. for a listing that fails a safety or delivery check), and sends you a push with the reason. At "
                "most ETSY_DEACTIVATE_DAILY_MAX a day (default 5). Etsy's Shop Manager re-activates it. Needs Connect "
                "Etsy with listing edits too. Off until you switch it on.",
    },
    "commerce_printify_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps read your Printify account",
        "help": "The printify.* tools: shops, products, the catalogue (blueprints, print providers) and orders, "
                "read-only. Delivery addresses are left out. Needs the Printify entries below under Keys and "
                "passwords. Off until you switch it on.",
    },
    "signals_etsy_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let TrendScout count Etsy listings",
        "help": "signal.collect asks Etsy how many active listings match each trend topic and their price spread "
                "(counts and prices only, nothing about a listing or seller is kept). Uses your Etsy app key. Check "
                "Etsy's API terms first. Off until you switch it on.",
    },
    "commerce_printify_write_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps create and edit Printify drafts (unpublished)",
        "help": "printify.product.create uploads a print file and makes an UNPUBLISHED product in your Printify shop, "
                "so you see Printify's mockups (at most PRINTIFY_DAILY_MAX a day, default 20). printify.product.update "
                "edits a draft's title, description, tags, prices and SKUs, never a published one and never under the 30% "
                "margin floor, keeping the old values (at most 50 edits a day). Nothing reaches your shop's buyers. "
                "Needs the read switch above too. Off until you switch it on.",
    },
    "commerce_printify_publish_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps ask to publish Printify products to your shop",
        "help": "The printify.product.publish tool: an app can ASK to publish a draft; nothing goes live until you "
                "approve that one in the approvals bar. It then appears on your Etsy shop, where buyers see it and "
                "Etsy charges its listing fee. At most PRINTIFY_PUBLISH_DAILY_MAX a day (default 5). Needs the read "
                "switch above too. Off until you switch it on.",
    },
    "commerce_printify_live_update_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps ask to update live Printify products",
        "help": "The printify.product.update_live tool: an app can ASK to change a published product's title, "
                "description, tags or prices; nothing changes on your shop until you approve that one in the approvals "
                "bar. Printify then edits it and re-publishes just those parts to Etsy. SKUs never change on a live "
                "product (Printify matches Etsy orders by SKU), and no price goes under the 30% margin floor. At most "
                "PRINTIFY_LIVE_UPDATE_DAILY_MAX a day (default 10). Needs the read switch above too. Off until you "
                "switch it on.",
    },
    "social_threads_enabled": {
        "type": "bool", "default": False, "group": "Online shops",
        "label": "Let apps post to Threads",
        "help": "The threads.publish tool: text posts to the Threads account you connect below, at most "
                "THREADS_DAILY_MAX a day (default 25). Each post is public. Off until you switch it on.",
    },
    **{key: {"type": "str", "default": standard, "group": "Online shops",
             "label": f"Vault entry for the {what}",
             "help": f"The name of the entry under Keys and passwords that holds the {what}. Change it to match the "
                     f"name you saved it under. Standard: {standard}."}
       for key, (standard, what) in VAULT_NAMES.items()},
}


def vault_name(key: str) -> str:
    """The vault entry name the owner set for this shop setting, or the standard one (unset, or not a valid name)."""
    standard = VAULT_NAMES[key][0]
    try:
        import runtime_settings
        chosen = str(runtime_settings.get(key) or "").strip()
    except Exception:  # noqa: BLE001 - no settings file, or none readable: the standard name
        return standard
    return chosen if NAME_RE.fullmatch(chosen) else standard
