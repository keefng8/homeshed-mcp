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


def test_the_zip_hoodie_gets_the_hoodie_floor():
    """bp91 (AWDIS JH050 full-zip hoodie) is a hoodie: the owner wants £56.99 for a 3869p one (31 Oct)."""
    assert up.min_margin(91) == up.min_margin(77) == 0.15
    assert up.floor_pence(3869, 91) == 5521 <= 5699 < up.floor_pence(3869) == 5934


def test_the_floor_keeps_20_percent_15_on_hoodies_after_etsy_fees(fake, monkeypatch):
    """The owner, 2026-10-09: 20% on everything, 15% on hoodies (it was 30% for all)."""
    tee, hoodie = up.floor_pence(1112), up.floor_pence(1112, 77)
    assert hoodie < tee < 2126  # 2126p (£21.26) was the 30% floor for an £11.12 tee at a 400p delivery estimate
    monkeypatch.setenv("PRINTIFY_MIN_MARGIN", "0.30")
    assert up.floor_pence(1112) == 2126 and up.floor_pence(1112, 77) == hoodie  # each has its own setting
    monkeypatch.delenv("PRINTIFY_MIN_MARGIN")
    with pytest.raises(h.CommerceError, match=f"under the floor of {tee}p \\(a 20% margin"):
        up.product_update("abc123", variants=[{"id": 1, "price_pence": tee - 1}])
    assert fake[1] == []
    up.product_update("abc123", variants=[{"id": 1, "price_pence": tee}])  # exactly at the floor is fine
    assert len(fake[1]) == 1


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


OLD_AREAS = [{"variant_ids": [1, 2, 3], "placeholders": [{"position": "front", "images": [
    {"id": "img-old", "x": 0.5, "y": 0.5, "scale": 1, "angle": 0}]}]}]
CATALOG = {"variants": [{"id": 1, "placeholders": [{"position": "front"}, {"position": "back"}]},
                        {"id": 2, "placeholders": [{"position": "front"}, {"position": "back"}]}]}
BACK_AND_CHEST = [{"position": "back", "print_file": "back.png", "scale": 1, "x": 0.5, "y": 0.45},
                  {"position": "front", "print_file": "chest.png", "scale": 0.3, "x": 0.7, "y": 0.25}]


@pytest.fixture
def areas(fake, monkeypatch):
    """The draft has a blueprint, provider and a front print; the catalogue and Printify's uploads are faked."""
    from tools.printify import write as wr
    state, puts, switches = fake
    state["product"] = {**DRAFT, "blueprint_id": 6, "print_provider_id": 99, "print_areas": OLD_AREAS}
    monkeypatch.setattr(pf, "get", lambda path, params=None: CATALOG if "/catalog/" in path else state["product"])
    uploads = []
    monkeypatch.setattr(wr, "_print_file_bytes", lambda name: b"PNG")
    monkeypatch.setattr(pf, "post", lambda path, body: uploads.append((path, body["file_name"])) or {"id": f"img-{len(uploads)}"})
    return state, puts, uploads


def test_print_areas_replace_a_drafts_layout_and_keep_the_old_one_for_undo(areas):
    state, puts, uploads = areas
    out = up.product_update("abc123", print_areas=BACK_AND_CHEST)
    assert out["changed"] == ["print_areas"] and out["old"] == {"print_areas": OLD_AREAS}
    assert uploads == [("/v1/uploads/images.json", "back.png"), ("/v1/uploads/images.json", "chest.png")]
    [(path, body)] = puts
    assert path == "/v1/shops/29235685/products/abc123.json" and set(body) == {"print_areas"}  # no prices or variants
    assert body["print_areas"] == [{"variant_ids": [1, 2, 3], "placeholders": [
        {"position": "back", "images": [{"id": "img-1", "x": 0.5, "y": 0.45, "scale": 1.0, "angle": 0}]},
        {"position": "front", "images": [{"id": "img-2", "x": 0.7, "y": 0.25, "scale": 0.3, "angle": 0}]}]}]
    assert up._edits()[0]["old"] == {"print_areas": OLD_AREAS} and up._edits()[0]["fields"] == ["print_areas"]
    assert state["product"]["variants"] == DRAFT["variants"]  # prices untouched


def test_print_areas_with_a_price_change_keep_the_other_prices(areas):
    _, puts, _ = areas
    out = up.product_update("abc123", variants=[{"id": 2, "price_pence": 2299}], print_areas=BACK_AND_CHEST[:1])
    assert out["changed"] == ["prices", "print_areas"] and out["old"]["prices"] == {"2": 2199}
    [(_, body)] = puts
    assert [v["price"] for v in body["variants"]] == [2199, 2299, 2199]


def test_print_areas_on_a_published_product_are_refused(areas):
    state, puts, uploads = areas
    state["product"] = {**state["product"], "external": {"id": "123", "handle": "https://etsy.com/listing/123"}}
    with pytest.raises(h.CommerceError, match="only unpublished drafts.*sales channel"):
        up.product_update("abc123", print_areas=BACK_AND_CHEST)
    assert puts == [] and uploads == []


@pytest.mark.parametrize("change, match", [
    ({"position": "sleeve"}, "sleeve isn't on this blueprint.*back, front"),
    ({"scale": 0.01}, "scale must be a number from 0.05 to 2.0"), ({"scale": 2.5}, "scale must be"),
    ({"x": 1.2}, "x must be a number from 0.0 to 1.0"), ({"y": -0.1}, "y must be"),
    ({"angle": 200}, "angle must be"), ({"scale": True}, "scale must be"),
    ({"print_file": "../x.png"}, "PNG name"), ({"print_file": "x.jpg"}, "PNG name"),
])
def test_bad_print_areas_are_refused_before_any_upload_or_edit(areas, change, match):
    _, puts, uploads = areas
    with pytest.raises(h.CommerceError, match=match):
        up.product_update("abc123", print_areas=[{**BACK_AND_CHEST[1], **change}])
    assert puts == [] and uploads == []
    with pytest.raises(h.CommerceError, match="list of 1-10"):
        up.product_update("abc123", print_areas=[])
