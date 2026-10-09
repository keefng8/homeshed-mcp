"""printify.product.update: edits unpublished drafts only, never under the margin floor, audited with the old values.
Printify is faked; files go under tmp_path."""
import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import update as up

DRAFT = {"id": "abc123", "title": "Retro Frenchie Tee", "description": "A tee.", "tags": ["dog"],
         "variants": [{"id": 1, "price": 2199, "cost": 1112, "is_enabled": True},
                      {"id": 2, "price": 2199, "cost": 1112, "is_enabled": True},
                      {"id": 3, "price": 2199, "cost": 1112, "is_enabled": False}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.setattr(up, "EDITS_FILE", tmp_path / "edits.json")
    for name in ("PRINTIFY_MIN_MARGIN", "PRINTIFY_DELIVERY_ESTIMATE_PENCE", "PRINTIFY_EDIT_DAILY_MAX"):
        monkeypatch.delenv(name, raising=False)
    switches = {pf.SETTING: True, pf.WRITE_SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"product": dict(DRAFT)}
    puts = []
    monkeypatch.setattr(pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pf, "get", lambda path, params=None: state["product"])
    monkeypatch.setattr(pf, "put", lambda path, body: puts.append((path, body)) or {})
    return state, puts, switches


def test_edits_text_tags_and_prices_and_keeps_the_old_values(fake):
    _, puts, _ = fake
    out = up.product_update("abc123", description="A tee.\n\n• Delivery: 3-5 days", tags=["french bulldog tee", "dog"],
                            variants=[{"id": 2, "price_pence": 2299}])
    assert out["changed"] == ["description", "prices", "tags"] and out["published"] is False
    assert out["old"] == {"description": "A tee.", "tags": ["dog"], "prices": {"2": 2199}}
    [(path, body)] = puts
    assert path == "/v1/shops/29235685/products/abc123.json" and "title" not in body
    assert body["variants"] == [{"id": 1, "price": 2199, "is_enabled": True}, {"id": 2, "price": 2299, "is_enabled": True},
                                {"id": 3, "price": 2199, "is_enabled": False}]  # all of them; only #2 repriced
    assert up._edits()[0]["old"] == out["old"]  # kept for undo


def test_the_floor_keeps_a_30_percent_margin_after_etsy_fees(fake):
    assert up.floor_pence(1112) == 2126  # an £11.12 tee: £21.26 at a 400p delivery estimate
    with pytest.raises(h.CommerceError, match="under the floor of 2126p"):
        up.product_update("abc123", variants=[{"id": 1, "price_pence": 2099}])
    assert fake[1] == []


@pytest.mark.parametrize("why, product, pending", [
    ("sales channel", {"external": {"id": "123", "handle": "https://etsy.com/listing/123"}}, False),
    ("publishing it right now", {"is_locked": True}, False),
    ("waiting for the owner's approval", {}, True),
])
def test_only_unpublished_drafts(fake, why, product, pending):
    state, puts, _ = fake
    state["product"] = {**DRAFT, **product}
    if pending:
        pp.add("abc123", "Publish it", {})
    with pytest.raises(h.CommerceError, match=why):
        up.product_update("abc123", title="New title")
    assert puts == []


@pytest.mark.parametrize("kwargs, match", [
    (dict(), "nothing to change"), (dict(title="x" * 141), "140"), (dict(tags=["x" * 21]), "20 characters"),
    (dict(tags=["t"] * 14), "at most 13"), (dict(variants=[{"id": 9, "price_pence": 3000}]), "isn't on this product"),
    (dict(variants=[{"id": 1, "price_pence": "30"}]), "integer id"),
])
def test_bad_input_is_refused_before_any_edit(fake, kwargs, match):
    with pytest.raises(h.CommerceError, match=match):
        up.product_update("abc123", **kwargs)
    assert fake[1] == []


def test_skus_change_on_a_draft_and_a_sku_printify_drops_is_reported(fake, monkeypatch):
    state, puts, _ = fake
    out = up.product_update("abc123", variants=[{"id": 1, "sku": "KRC-FRENCHIE-S"}, {"id": 2, "price_pence": 2299}])
    assert out["changed"] == ["prices", "skus"] and out["old"]["skus"] == {"1": None} and "note" not in out
    [(_, body)] = puts
    assert body["variants"][0] == {"id": 1, "price": 2199, "is_enabled": True, "sku": "KRC-FRENCHIE-S"}
    assert "sku" not in body["variants"][1]  # only the asked-for SKU is sent
    monkeypatch.setattr(pf, "put", lambda path, body: {"variants": [{"id": 1, "sku": "OLD-1"}]})
    assert "didn't keep the new SKU" in up.product_update("abc123", variants=[{"id": 1, "sku": "NEW-1"}])["note"]
    with pytest.raises(h.CommerceError, match="1-32 letters"):
        up.product_update("abc123", variants=[{"id": 1, "sku": "has space"}])


def test_switch_off_or_already_as_asked_sends_nothing(fake):
    state, puts, switches = fake
    assert up.product_update("abc123", title="Retro Frenchie Tee")["changed"] == []  # same as now
    switches[pf.WRITE_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        up.product_update("abc123", title="Another title")
    assert puts == []
