"""etsy.listing.update and etsy.listing.deactivate (tools/etsy/write.py): an update only queues what differs, matched to
Etsy's own attribute lists, and runs on the owner's approval; what was set is re-queued after a Printify re-publish;
deactivate is a capped, audited brake. Etsy is faked; files go under tmp_path. Test list drafted by the local model
(2026-10-08)."""
from urllib.parse import parse_qs

import httpx
import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.etsy import _client as et
from tools.etsy import write as ew

SHOP = "29235685"
LISTING = {"listing_id": 4412, "shop_id": int(SHOP), "title": "Retro Frenchie Tee", "state": "active",
           "taxonomy_id": 449, "materials": ["cotton"], "shipping_profile_id": 111, "production_partner_ids": [7]}
PROPS = {"results": [
    {"property_id": 200, "name": "primary_color", "display_name": "Primary color", "supports_attributes": True,
     "is_multivalued": False, "possible_values": [{"value_id": 1, "name": "Black"}, {"value_id": 2, "name": "White"}]},
    {"property_id": 46803063641, "name": "holiday", "display_name": "Holiday", "supports_attributes": True,
     "is_multivalued": True, "max_values_allowed": 2,
     "possible_values": [{"value_id": 35, "name": "Christmas"}, {"value_id": 36, "name": "Halloween"}]},
    {"property_id": 300, "name": "sleeve", "display_name": "Sleeve length", "supports_attributes": False,
     "possible_values": [{"value_id": 9, "name": "Short"}]}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.setattr(ew, "ATTRS_FILE", tmp_path / "attrs.json")
    monkeypatch.setattr(ew, "DEACTIVATED_FILE", tmp_path / "deactivated.json")
    for name in ("ETSY_EDIT_DAILY_MAX", "ETSY_DEACTIVATE_DAILY_MAX"):
        monkeypatch.delenv(name, raising=False)
    switches = {et.SETTING: True, et.WRITE_SETTING: True, et.DEACTIVATE_SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"listing": dict(LISTING), "current": {"results": [{"property_id": 200, "value_ids": [2]}]}}

    def get(path, params=None, oauth=True):
        if path.endswith("/properties") and "seller-taxonomy" in path:
            return PROPS
        if path.endswith("/properties"):
            return state["current"]
        return state["listing"]
    writes, pushes = [], []
    monkeypatch.setattr(et, "shop_id", lambda: SHOP)
    monkeypatch.setattr(et, "get", get)
    monkeypatch.setattr(et, "write", lambda method, path, form, setting, label: writes.append((method, path, form, setting)) or {})
    monkeypatch.setattr("tools.notify.send.send", lambda message, **kw: pushes.append((kw.get("title"), message)))
    return state, writes, switches, pushes


def test_an_update_only_queues_what_differs_and_approval_sets_it(fake):
    _, writes, _, _ = fake
    out = ew.listing_update("4412", materials=["cotton", "polyester"], shipping_profile_id=111,
                            properties={"primary COLOR": "black", "Holiday": ["Christmas"]})
    assert out["pending"] is True and writes == []  # nothing changed by the call itself
    assert out["etsy"] == ('Set Etsy details on "Retro Frenchie Tee": materials: cotton, polyester; '
                           "Primary color: Black; Holiday: Christmas")  # the delivery profile is already 111
    [item] = pp.list_pending()
    assert item["action"] == "etsy" and item["checks"]["fields"] == ["materials"]
    res = pp.decide(item["id"], True)
    assert res["approved"] and res["result"]["attributes"] == ["Primary color", "Holiday"]
    patch, color, holiday = writes
    assert patch == ("PATCH", f"/v3/application/shops/{SHOP}/listings/4412", {"materials": ["cotton", "polyester"]},
                     et.WRITE_SETTING)
    assert color[:3] == ("PUT", f"/v3/application/shops/{SHOP}/listings/4412/properties/200",
                         {"value_ids": [1], "values": ["Black"]})
    assert holiday[2] == {"value_ids": [35], "values": ["Christmas"]}
    saved = ew.applied()["4412"]
    assert saved["fields"] == {"materials": ["cotton", "polyester"], "shipping_profile_id": 111}  # all asked, kept
    assert len(pp.done_today("etsy")) == 1


@pytest.mark.parametrize("kwargs, match", [
    (dict(properties={"Colour wheel": "Black"}), "isn't an attribute of category 449; it has: Holiday, Primary color"),
    (dict(properties={"Primary color": "Mauve"}), r"isn't one of Etsy's values \(Black, White\)"),
    (dict(properties={"Primary color": ["Black", "White"]}), "at most 1 value"),
    (dict(properties={"Holiday": ["Christmas", "Halloween", "Christmas"]}), "at most 2"),
    (dict(materials=["cotton, poly"]), "letters, digits and spaces"),
    (dict(taxonomy_id=-3), "positive whole number"),
    (dict(), "nothing to change"),
])
def test_bad_asks_are_refused_before_anything_queues(fake, kwargs, match):
    with pytest.raises(h.CommerceError, match=match):
        ew.listing_update("4412", **kwargs)
    assert pp.list_pending() == []


def test_not_this_shops_listing_or_switched_off(fake):
    state, writes, switches, _ = fake
    state["listing"]["shop_id"] = 1
    with pytest.raises(h.CommerceError, match="isn't in this shop"):
        ew.listing_update("4412", materials=["linen"])
    switches[et.WRITE_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        ew.listing_update("4412", materials=["linen"])
    assert writes == [] and pp.list_pending() == []


def test_already_as_asked_queues_nothing(fake):
    out = ew.listing_update("4412", materials=["cotton"], properties={"Primary color": "White"})
    assert out["pending"] is False and pp.list_pending() == []


def test_reapply_queues_the_saved_set(fake):
    with pytest.raises(h.CommerceError, match="nothing to re-apply"):
        ew.listing_update("4412", reapply=True)
    first = ew.listing_update("4412", materials=["cotton", "polyester"], properties={"Primary color": "Black"})
    pp.decide(first["id"], True)
    queued = ew.queue_reapply("4412")  # what a Printify re-publish does: all of it, changed-looking or not
    assert queued["etsy"].startswith('Re-apply Etsy details on "Retro Frenchie Tee" after Printify\'s re-publish')
    [item] = pp.list_pending()
    assert item["payload"]["send"]["fields"] == {"materials": ["cotton", "polyester"]}
    assert ew.queue_reapply("9999") is None


def test_a_printify_re_publish_queues_the_etsy_details_again(fake, monkeypatch):
    from tools.printify import _client as pf
    from tools.printify import update_live as ul
    first = ew.listing_update("4412", properties={"Primary color": "Black"})
    pp.decide(first["id"], True)
    live = {"id": "abc123", "title": "Retro Frenchie Tee", "description": "A tee.", "tags": ["dog"],
            "external": {"id": "4412"}, "variants": [{"id": 1, "price": 2199, "cost": 1112, "is_enabled": True}]}
    fake[2].update({pf.SETTING: True, pf.LIVE_UPDATE_SETTING: True})
    monkeypatch.setattr(pf, "shop_id", lambda: "1")
    monkeypatch.setattr(pf, "get", lambda path, params=None: live)
    monkeypatch.setattr(pf, "put", lambda path, body, **kw: {})
    monkeypatch.setattr(pf, "publish_post", lambda path, body, **kw: {})
    ask = ul.product_update_live("abc123", title="Retro French Bulldog Tee")
    assert ask["update"].endswith("its Etsy details are queued again after")
    res = pp.decide(ask["id"], True)
    assert res["etsy_reapply"]["pending"] is True and "Re-apply" in pp.list_pending()[0]["summary"]


def test_deactivate_runs_at_once_is_capped_and_tells_the_owner(fake, monkeypatch):
    state, writes, switches, pushes = fake
    monkeypatch.setenv("ETSY_DEACTIVATE_DAILY_MAX", "1")
    with pytest.raises(h.CommerceError, match="5-300 characters"):
        ew.listing_deactivate("4412", "bad")
    out = ew.listing_deactivate("4412", "Ships to the EU: fails the UK-only check")
    assert out["state"] == "inactive" and out["deactivated_today"] == 1
    assert writes == [("PATCH", f"/v3/application/shops/{SHOP}/listings/4412", {"state": "inactive"},
                       et.DEACTIVATE_SETTING)]
    assert pushes == [("Etsy listing deactivated", 'owner took "Retro Frenchie Tee" off sale: Ships to the EU: fails '
                                                   "the UK-only check")]
    with pytest.raises(h.CommerceError, match="daily limit"):
        ew.listing_deactivate("4413", "Another one that fails")
    monkeypatch.setenv("ETSY_DEACTIVATE_DAILY_MAX", "5")
    state["listing"]["state"] = "inactive"
    with pytest.raises(h.CommerceError, match="not active"):
        ew.listing_deactivate("4412", "Already off sale")
    switches[et.DEACTIVATE_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        ew.listing_deactivate("4412", "Switch is off now")


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def photos(fake, monkeypatch):
    state, writes, _, _ = fake
    store = {"listing/krc-staffy-artwork-2400.png": PNG + b"art", "listing/size-guide.png": PNG + b"size"}
    monkeypatch.setattr(ew, "_image", lambda ref: store[ref] if ref in store
                        else (_ for _ in ()).throw(h.CommerceError(f"there's no image {ref}")))
    monkeypatch.setattr(ew, "_thumb", lambda data: "dGh1bWI=")  # "thumb"
    state["images"] = {"count": 1}
    get = et.get
    monkeypatch.setattr(et, "get", lambda path, params=None, oauth=True: state["images"] if path.endswith("/images")
                        else get(path, params, oauth))

    def write(method, path, form, setting, label, files=None):
        writes.append((method, path, form, files))
        if files and state.get("fail_on") == files["image"][0]:
            raise h.CommerceError("Etsy answered HTTP 500")
        return {"listing_image_id": 900 + len(writes)}
    monkeypatch.setattr(et, "write", write)
    return state, writes, store


def test_photos_are_fingerprinted_shown_and_uploaded_on_approval(photos):
    state, writes, _ = photos
    out = ew.listing_update("4412", images=[{"image": "listing/krc-staffy-artwork-2400.png", "rank": 2,
                                             "alt_text": "Staffy artwork close-up"}, {"image": "listing/size-guide.png", "rank": 3}])
    assert out["pending"] is True and writes == []
    assert out["etsy"].endswith("photos: krc-staffy-artwork-2400.png, size-guide.png")
    [item] = pp.list_pending()
    assert item["payload"]["thumbs"] == ["dGh1bWI=", "dGh1bWI="] and item["checks"]["images"] == 2
    assert item["payload"]["send"]["images"][0]["mime"] == "image/png" and len(item["payload"]["send"]["images"][0]["sha256"]) == 64
    res = pp.decide(item["id"], True)
    assert res["result"]["photos"] == 2
    (m1, p1, f1, files1), (_, _, f2, files2) = writes
    assert (m1, p1) == ("POST", f"/v3/application/shops/{SHOP}/listings/4412/images")
    assert f1 == {"rank": 2, "alt_text": "Staffy artwork close-up"} and files1["image"][0] == "krc-staffy-artwork-2400.png"
    assert f2 == {"rank": 3} and files2["image"][1] == PNG + b"size"
    assert "4412" not in ew.applied() or "images" not in ew.applied()["4412"]  # photos are never re-applied


def test_a_changed_photo_is_refused_and_a_retry_never_uploads_twice(photos):
    state, writes, store = photos
    ew.listing_update("4412", images=[{"image": "listing/krc-staffy-artwork-2400.png"}, {"image": "listing/size-guide.png"}])
    [item] = pp.list_pending()
    state["fail_on"] = "size-guide.png"
    with pytest.raises(h.CommerceError, match="HTTP 500"):
        pp.decide(item["id"], True)
    assert len(writes) == 2  # the first went up, the second failed
    state["fail_on"] = None
    pp.decide(item["id"], True)
    assert [w[3]["image"][0] for w in writes] == ["krc-staffy-artwork-2400.png", "size-guide.png", "size-guide.png"]
    ew.listing_update("4412", images=[{"image": "listing/size-guide.png"}])
    [again] = pp.list_pending()
    store["listing/size-guide.png"] = PNG + b"edited meanwhile"
    with pytest.raises(h.CommerceError, match="changed since it was asked"):
        pp.decide(again["id"], True)


@pytest.mark.parametrize("images, count, match", [
    ([{"image": "listing/size-guide.png"}] * 6, 1, "1-5"),
    ([{"image": "listing/size-guide.png"}], 10, "pass Etsy's 10"),
    ([{"image": "listing/size-guide.png", "rank": 0}], 1, "rank"),
    ([{"image": "listing/missing.png"}], 1, "no image"),
])
def test_bad_photo_asks_are_refused(photos, images, count, match):
    photos[0]["images"] = {"count": count}
    with pytest.raises(h.CommerceError, match=match):
        ew.listing_update("4412", images=images)
    assert pp.list_pending() == []


def test_only_printify_hosts_and_safe_names_are_fetched():
    with pytest.raises(h.CommerceError, match="only Printify mockup addresses"):
        ew._image("https://example.com/x.png")
    for bad in ("../secret.png", "listing/../x.png", "C:/x.png", "x.exe"):
        with pytest.raises(h.CommerceError, match="give an image name"):
            ew._image(bad)


def test_the_client_uploads_a_photo_as_multipart(monkeypatch):
    monkeypatch.setattr(h, "require", lambda key, label: None)
    monkeypatch.setattr(et, "_app_key", lambda: ("key", "sec"))
    monkeypatch.setattr(et, "_access", lambda key, sec, force=False: "12.token")
    seen = []
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(
        lambda request: seen.append(request) or httpx.Response(200, json={"listing_image_id": 7})))
    out = et.write("POST", "/v3/application/shops/1/listings/4412/images", {"rank": 2, "alt_text": "Art"},
                   et.WRITE_SETTING, "x", files={"image": ("art.png", PNG, "image/png")})
    body = seen[0].content
    assert out == {"listing_image_id": 7} and seen[0].headers["content-type"].startswith("multipart/form-data")
    assert b'name="rank"\r\n\r\n2' in body and b'filename="art.png"' in body and PNG in body


def test_the_client_sends_lists_comma_joined_and_names_a_missing_scope(monkeypatch):
    """Etsy keeps only the last of repeated form keys (etsy/open-api discussion #1086): one comma-joined value."""
    monkeypatch.setattr(h, "require", lambda key, label: None)
    monkeypatch.setattr(et, "_app_key", lambda: ("key", "sec"))
    monkeypatch.setattr(et, "_access", lambda key, sec, force=False: "12.token")
    seen = []

    def handler(request):
        seen.append(parse_qs(request.content.decode()))
        if "/properties/" in request.url.path:
            return httpx.Response(403, json={"error": "insufficient scope: listings_w"})
        return httpx.Response(200, json={"listing_id": 4412})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))
    et.write("PATCH", "/v3/application/shops/1/listings/4412", {"materials": ["cotton", "polyester"]},
             et.WRITE_SETTING, "x")
    assert seen[0] == {"materials": ["cotton,polyester"]}
    with pytest.raises(h.CommerceError, match="Connect Etsy again"):
        et.write("PUT", "/v3/application/shops/1/listings/4412/properties/200", {"value_ids": [1, 2]},
                 et.WRITE_SETTING, "x")
    assert seen[1] == {"value_ids": ["1,2"]}
