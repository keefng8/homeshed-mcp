"""Printify API v1 for the printify.* tools. See ../../capabilities/printify/read.md.

Credentials by name only (vault.secret: the Control Panel's Credentials first, then the environment):
  PRINTIFY_API_TOKEN  a Printify personal access token (Bearer)
  PRINTIFY_SHOP_ID    the shop the shop-level tools use (printify.shops.list shows the ids; not a secret)

Every request sends a User-Agent, as Printify requires. Off unless the owner switches commerce_printify_enabled on.
Writes (post) are drafts only, behind a second switch (commerce_printify_write_enabled): printify.product.create.
"""
from __future__ import annotations

from typing import Any

from tools.commerce import _http as h
from tools.commerce import vault_name

# The standard vault entry names; the owner can name others in Settings > Shops and social (read on every call).
API_TOKEN, SHOP_ID = "PRINTIFY_API_TOKEN", "PRINTIFY_SHOP_ID"
SETTING = "commerce_printify_enabled"
USER_AGENT = "HomeShed-mcp-server"


def _token() -> str:
    name = vault_name("commerce_printify_api_token_name")
    tok = h.secret(name)
    if not tok:
        raise h.missing([name], "Printify")
    return tok


def shop_id() -> str:
    name = vault_name("commerce_printify_shop_id_name")
    raw = h.secret(name)
    if not raw:
        raise h.missing([name], "Printify's shop")
    if not raw.isdigit():
        raise h.CommerceError(f"{name} should be the shop's number (it isn't shown)")
    return raw


WRITE_SETTING = "commerce_printify_write_enabled"


def post(path: str, body: dict) -> Any:
    """A write to Printify (drafts only: printify.product.create). Off unless the owner switches it on."""
    h.require(SETTING, "Printify")
    h.require(WRITE_SETTING, "Creating Printify products")
    tok = _token()
    resp = h.request("printify", "POST", path, json=body, secrets=(tok,),
                     headers={"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT,
                              "Accept": "application/json"})
    return h.json_of("printify", resp)


def put(path: str, body: dict, setting: str = WRITE_SETTING, label: str = "Editing Printify drafts") -> Any:
    """An edit: of an unpublished draft (printify.product.update checks it's a draft first; same switches as post), or
    of a live product on the owner's approval (printify_pending, with LIVE_UPDATE_SETTING)."""
    h.require(SETTING, "Printify")
    h.require(setting, label)
    tok = _token()
    resp = h.request("printify", "PUT", path, json=body, secrets=(tok,),
                     headers={"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT,
                              "Accept": "application/json"})
    return h.json_of("printify", resp)


PUBLISH_SETTING = "commerce_printify_publish_enabled"
LIVE_UPDATE_SETTING = "commerce_printify_live_update_enabled"


def publish_post(path: str, body: dict, setting: str = PUBLISH_SETTING,
                 label: str = "Publishing Printify products") -> Any:
    """A publish to the owner's shop. Only printify_pending runs it, after the owner's approval; it needs the read
    switch and the publish switch (not the drafts switch: publishing is a separate permission), or for a live
    product's update, the live-update switch."""
    h.require(SETTING, "Printify")
    h.require(setting, label)
    tok = _token()
    resp = h.request("printify", "POST", path, json=body, secrets=(tok,),
                     headers={"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT,
                              "Accept": "application/json"})
    return h.json_of("printify", resp)


def get(path: str, params: dict | None = None) -> Any:
    h.require(SETTING, "Printify")
    tok = _token()
    resp = h.request("printify", "GET", path, params=params, secrets=(tok,),
                     headers={"Authorization": f"Bearer {tok}", "User-Agent": USER_AGENT,
                              "Accept": "application/json"})
    return h.json_of("printify", resp)
