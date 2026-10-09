"""printify.shipping: the catalogue delivery table, filtered, in units, currency kept (asked for by the business
machine for its listing margins, 2026-10-07). Printify is faked."""
import pytest


def test_shipping_filters_converts_and_keeps_currency(monkeypatch):
    from tools.printify import read as pr
    calls = []
    table = {"handling_time": {"value": 3, "unit": "day"}, "profiles": [
        {"variant_ids": [11, 12], "countries": ["GB"], "first_item": {"cost": 399, "currency": "GBP"},
         "additional_items": {"cost": 150, "currency": "GBP"}},
        {"variant_ids": [11, 12, 13], "countries": ["US", "CA"], "first_item": {"cost": 475, "currency": "USD"},
         "additional_items": {"cost": 240, "currency": "USD"}},
        {"variant_ids": [11, 12, 13], "countries": ["REST_OF_THE_WORLD"], "first_item": {"cost": 900, "currency": "USD"},
         "additional_items": {"cost": 400, "currency": "USD"}}]}
    monkeypatch.setattr(pr.pf, "get", lambda path, **k: calls.append(path) or table)
    out = pr.shipping("6", "99", country="gb")
    assert calls[-1] == "/v1/catalog/blueprints/6/print_providers/99/shipping.json"
    assert out["handling_time"] == {"value": 3, "unit": "day"} and out["matches"] == 1
    assert out["profiles"][0] == {"variant_ids": [11, 12], "countries": ["GB"], "first_item": 3.99,
                                  "additional_items": 1.5, "currency": "GBP"}
    assert pr.shipping("6", "99", country="DE")["profiles"][0]["countries"] == ["REST_OF_THE_WORLD"]
    assert pr.shipping("6", "99", variant_id="13")["matches"] == 2
    assert pr.shipping("6", "99")["matches"] == 3


def test_shipping_refuses_bad_input_before_any_call(monkeypatch):
    from tools.printify import read as pr
    monkeypatch.setattr(pr.pf, "get", lambda *a, **k: pytest.fail("called Printify"))
    for args in (("x", "99"), ("6", "../1"), ("6", "99", "Britain"), ("6", "99", "", "abc")):
        with pytest.raises(Exception):
            pr.shipping(*args)


def test_product_get_gives_cost_pages_and_filters(monkeypatch):
    from tools.printify import read as pr
    variants = [{"id": 1000 + i, "title": f"Black / {s}", "price": 2499, "cost": 1185 + i, "sku": f"S{i}",
                 "is_enabled": True} for i, s in enumerate(["S", "M", "L", "XL", "2XL", "3XL"] * 3)]
    variants.append({"id": 9999, "title": "off", "price": 1, "cost": 1, "is_enabled": False})
    monkeypatch.setattr(pr.pf, "shop_id", lambda: "123")
    monkeypatch.setattr(pr.pf, "get", lambda path, **k: {"id": "abc123", "title": "Tee", "variants": variants})
    out = pr.product_get("abc123")
    assert out["variants_enabled"] == 18 and out["matches"] == 18 and len(out["variants"]) == 18  # no 15 cap
    assert out["variants"][0] == {"id": 1000, "title": "Black / S", "price": 24.99, "cost": 11.85, "sku": "S0"}
    assert out["currency"] is None and out["next_offset"] is None
    page = pr.product_get("abc123", offset=0, limit=10)
    assert len(page["variants"]) == 10 and page["next_offset"] == 10
    picked = pr.product_get("abc123", variant_ids=[1001, "1002", 9999])
    assert [v["id"] for v in picked["variants"]] == [1001, 1002]  # a disabled one is never returned
    with pytest.raises(Exception):
        pr.product_get("abc123", variant_ids=["big"])
