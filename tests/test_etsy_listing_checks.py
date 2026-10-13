"""Etsy reads for a post-publish listing check (2026-10-07): full description, photos, production partners, and a
delivery profile's destinations with an EU flag. Etsy is faked."""
import pytest

from tools.etsy import read as er


def test_listing_get_full_description_photos_and_partners(monkeypatch):
    calls = []
    long = "A retro dog design. " * 120 + "AI-assisted design."
    listing = {"listing_id": 1, "title": "Tee", "description": long, "images": [{"rank": 2}, {"rank": 1}, {"rank": 3}],
               "production_partners": [{"production_partner_id": 555, "partner_name": "Print Co", "location": "UK"}],
               "price": {"amount": 1999, "divisor": 100, "currency_code": "GBP"}}
    monkeypatch.setattr(er.et, "get", lambda path, params=None, oauth=True: calls.append((path, params, oauth)) or listing)
    short = er.listing_get("1")
    assert calls[-1] == ("/v3/application/listings/1", {"includes": "Images"}, False)
    assert len(short["description"]) <= 1500 and short["description_chars"] == len(long)
    assert short["images"] == {"count": 3, "ranks": [1, 2, 3]} and short["production_partner_ids"] == [555]
    assert short["production_partners"] == [{"id": 555, "name": "Print Co"}]
    full = er.listing_get("1", full=True)
    assert full["description"].endswith("AI-assisted design.")  # the disclosure at the end is visible
    listing["production_partners"] = []  # none linked (the four live tees, 2026-10-09)
    assert er.listing_get("1")["production_partner_ids"] == []
    listing.pop("production_partners")
    assert er.listing_get("1")["production_partner_ids"] is None


def test_partner_ids_reads_etsys_listing_field():
    """Etsy's listing says "production_partners"; "production_partner_ids" is only what a write sends."""
    rows = [{"production_partner_id": 9}, {"production_partner_id": 3}, "x", {"production_partner_id": "7"}, {}]
    assert er.et.partner_ids({"production_partners": rows}) == [3, 9]
    assert er.et.partner_ids({"production_partners": []}) == []
    assert er.et.partner_ids({"production_partner_ids": [3]}) is None  # not Etsy's read field
    assert er.et.partner_ids({"production_partners": "3"}) is None


def test_shop_get_lists_the_shops_partners_and_survives_without_them(monkeypatch):
    calls = []

    def get(path, params=None, oauth=True):
        calls.append((path, oauth))
        if path.endswith("/production-partners"):
            return {"count": 1, "results": [{"production_partner_id": 5, "partner_name": "Print Co", "location": "UK"}]}
        return {"shop_id": 29235685, "shop_name": "Shop"}
    monkeypatch.setattr(er.et, "shop_id", lambda: "29235685")
    monkeypatch.setattr(er.et, "get", get)
    out = er.shop_get()
    assert out["shop_name"] == "Shop" and out["production_partners"] == [{"id": 5, "name": "Print Co", "location": "UK"}]
    assert ("/v3/application/shops/29235685/production-partners", True) in calls  # the shop's connection

    def no_oauth(path, params=None, oauth=True):
        if path.endswith("/production-partners"):
            raise er.h.CommerceError("Etsy isn't connected")
        return {"shop_id": 29235685, "shop_name": "Shop"}
    monkeypatch.setattr(er.et, "get", no_oauth)
    out = er.shop_get()
    assert out["shop_name"] == "Shop" and out["production_partners"] is None


def test_shop_get_lists_return_policies_and_listing_get_names_its_policy(monkeypatch):
    """2026-10-10: the PM checks every live listing carries the shop's 14-day returns policy."""
    def get(path, params=None, oauth=True):
        if path.endswith("/policies/return"):
            return {"results": [{"return_policy_id": 77, "accepts_returns": True, "accepts_exchanges": True,
                                 "return_deadline": 14}]}
        if "/listings/" in path:
            return {"listing_id": 4412, "return_policy_id": 77, "shop_section_id": 5}
        return {"shop_id": 29235685, "shop_name": "Shop"}
    monkeypatch.setattr(er.et, "shop_id", lambda: "29235685")
    monkeypatch.setattr(er.et, "get", get)
    assert er.shop_get()["return_policies"] == [{"id": 77, "accepts_returns": True, "accepts_exchanges": True,
                                                  "return_deadline_days": 14}]
    got = er.listing_get("4412")
    assert got["return_policy_id"] == 77 and got["shop_section_id"] == 5


def test_shipping_destinations_flags_the_eu(monkeypatch):
    calls = []
    d = {"count": 3, "results": [
        {"destination_country_iso": "GB", "destination_region": "none", "primary_cost": {"amount": 399, "divisor": 100,
                                                                                         "currency_code": "GBP"},
         "min_delivery_days": 2, "max_delivery_days": 5},
        {"destination_country_iso": "US", "destination_region": "none"},
        {"destination_country_iso": None, "destination_region": "eu"}]}
    monkeypatch.setattr(er.et, "shop_id", lambda: "29235685")
    monkeypatch.setattr(er.et, "get", lambda path, params=None, oauth=True: calls.append((path, oauth)) or d)
    out = er.shipping_destinations("777")
    assert calls[-1] == ("/v3/application/shops/29235685/shipping-profiles/777/destinations", True)  # OAuth (shops_r)
    assert out["includes_eu"] is True and out["count"] == 3 and out["destinations"][0]["country"] == "GB"
    assert out["destinations"][0]["min_days"] == 2
    d["results"] = d["results"][:2]
    assert er.shipping_destinations("777")["includes_eu"] is False
    d["results"].append({"destination_country_iso": "IE", "destination_region": "none"})
    assert er.shipping_destinations("777")["includes_eu"] is True  # an EU country by itself


def test_bad_ids_are_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(er.et, "get", lambda *a, **k: pytest.fail("called Etsy"))
    for fn, arg in ((er.listing_get, "../x"), (er.shipping_destinations, "abc")):
        with pytest.raises(Exception):
            fn(arg)
