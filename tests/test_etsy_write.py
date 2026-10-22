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
           "taxonomy_id": 449, "materials": ["cotton"], "shipping_profile_id": 111,
           "production_partners": [{"production_partner_id": 7, "partner_name": "Print Co", "location": "UK"}]}
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


def test_null_removes_an_attribute_and_it_is_never_re_applied(fake):
    """2026-10-14: "Holiday": "Christmas" went on ~55 year-round tees. {"Holiday": null} deletes it on approval and
    drops it from what a re-apply sets; a listing without it queues nothing but still forgets it."""
    state, writes, _, _ = fake
    first = ew.listing_update("4412", properties={"Primary color": "Black", "Holiday": "Christmas"})
    pp.decide(first["id"], True)
    writes.clear()
    state["current"] = {"results": [{"property_id": 200, "value_ids": [1]}, {"property_id": 46803063641,
                                                                             "value_ids": [35]}]}
    out = ew.listing_update("4412", properties={"holiday": None})
    assert out["pending"] is True and out["etsy"] == 'Set Etsy details on "Retro Frenchie Tee": Holiday: removed'
    assert [p["property_id"] for p in ew.applied()["4412"]["properties"]] == [200]  # forgotten at once
    res = pp.decide(out["id"], True)
    assert res["approved"] and writes == [("DELETE", f"/v3/application/shops/{SHOP}/listings/4412/properties/"
                                           "46803063641", {}, et.WRITE_SETTING)]
    assert [p["property_id"] for p in ew.applied()["4412"]["properties"]] == [200]
    ew.queue_reapply("4412")  # after a Printify re-publish: Christmas doesn't come back
    [again] = pp.list_pending()
    assert [p["property_id"] for p in again["payload"]["send"]["properties"]] == [200]
    pp.decide(again["id"], False)
    state["current"] = {"results": [{"property_id": 200, "value_ids": [1]}]}  # Etsy has none now
    assert ew.listing_update("4412", properties={"Holiday": None})["pending"] is False


def test_removing_an_attribute_etsy_already_lacks_is_done(fake, monkeypatch):
    def gone(method, path, form, setting, label):
        raise h.CommerceError("Etsy: HTTP 404 not found")
    monkeypatch.setattr(et, "write", gone)
    payload = {"listing_id": "4412", "send": {"properties": [{"property_id": 5, "name": "Holiday", "value_ids": [],
                                                              "values": [], "remove": True}]}}
    assert ew.execute(payload)["attributes"] == ["Holiday"]


def test_production_partners_compare_with_etsys_listing_field(fake):
    """Etsy's listing names its partners in "production_partners" (2026-10-09): one already linked isn't re-queued."""
    state, _, _, _ = fake
    assert ew.listing_update("4412", production_partner_ids=[7])["pending"] is False
    state["listing"]["production_partners"] = []  # none linked, as on the live tees
    out = ew.listing_update("4412", production_partner_ids=[7])
    assert out["pending"] is True and "production partners: 7" in out["etsy"]


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


def test_a_printify_price_re_publish_queues_the_etsy_details_again_a_words_one_doesnt(fake, monkeypatch):
    """2026-10-09: a title, description or tags publish left every Etsy detail intact (5 listings,
    checked on Etsy), so only a publish that re-sends variants, images or the delivery template queues them again."""
    from tools.printify import _client as pf
    from tools.printify import update_live as ul
    first = ew.listing_update("4412", properties={"Primary color": "Black"})
    pp.decide(first["id"], True)
    live = {"id": "abc123", "title": "Retro Frenchie Tee", "description": "A tee.", "tags": ["dog"],
            "external": {"id": "4412"}, "variants": [{"id": 1, "price": 2199, "cost": 1112, "is_enabled": True}]}
    fake[2].update({pf.SETTING: True, pf.LIVE_UPDATE_SETTING: True})
    published = []
    monkeypatch.setattr(pf, "shop_id", lambda: "1")
    monkeypatch.setattr(pf, "get", lambda path, params=None: live)
    monkeypatch.setattr(pf, "put", lambda path, body, **kw: {})
    monkeypatch.setattr(pf, "publish_post", lambda path, body, **kw: published.append(body) or {})
    words = ul.product_update_live("abc123", title="Retro French Bulldog Tee", tags=["dog", "frenchie"])
    assert "queued again" not in words["update"]
    res = pp.decide(words["id"], True)
    assert "etsy_reapply" not in res and "reapply_etsy" not in res["result"] and pp.list_pending() == []
    assert published[-1]["title"] and not published[-1]["variants"] and not published[-1]["images"]
    live["title"], live["tags"] = "Retro French Bulldog Tee", ["dog", "frenchie"]  # as Printify has it now
    price = ul.product_update_live("abc123", variants=[{"id": 1, "price_pence": 2299}])
    assert price["update"].endswith("its Etsy details are queued again after")
    res = pp.decide(price["id"], True)
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
    res = pp.decide(item["id"], True)["result"]
    assert [w[3]["image"][0] for w in writes] == ["krc-staffy-artwork-2400.png", "size-guide.png", "size-guide.png"]
    # every photo's outcome by name (the shop app read "done" for a request that had no photos at all)
    assert (res["photos_asked"], res["photos_landed"], res["photos"]) == (2, 2, 1)
    assert [(r["image"], r["uploaded"]) for r in res["photo_results"]] == [
        ("listing/krc-staffy-artwork-2400.png", "earlier"), ("listing/size-guide.png", "now")]
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


def test_raw_art_swap_adds_first_then_alt_text_then_deletes_on_approval(photos):
    """2026-10-18 (designs copied from listings): add the watermarked copy, fix alt text, delete the raw art."""
    state, writes, _ = photos
    state["images"] = {"count": 2, "results": [{"listing_image_id": 501}, {"listing_image_id": 502}]}
    out = ew.listing_update("4412", images=[{"image": "listing/size-guide.png", "rank": 2}], remove_images=[502],
                            images_alt=[{"listing_image_id": 501, "alt_text": "Staffy tee, front"}])
    assert out["pending"] is True and writes == [] and "delete photos [502] (can't be undone)" in out["etsy"]
    [item] = pp.list_pending()
    res = pp.decide(item["id"], True)["result"]
    assert [(m, p.rsplit("/", 2)[-2:] if m == "DELETE" else p.rsplit("/", 1)[-1], f) for m, p, f, _ in writes] == [
        ("POST", "images", {"rank": 2}),
        ("POST", "images", {"listing_image_id": 501, "alt_text": "Staffy tee, front"}),
        ("DELETE", ["images", "502"], {})]
    assert res["photos_removed"] == [502] and res["alt_text_set"] == [501]


RAW = {"listing_image_id": 777, "rank": 3, "alt_text": "Staffy artwork, full design", "full_width": 2400,
       "full_height": 2400, "url_fullxfull": "https://i.etsystatic.com/1/r/il/abc/777/il_fullxfull.777.jpg"}


@pytest.mark.parametrize("change, why", [
    (dict(swap=False), "must be a swap"), (dict(removes=[777, 778]), "exactly one"), (dict(new=[]), "replacement"),
    (dict(photo={"alt_text": "Staffy tee on a model"}), "artwork"),
    (dict(photo={"alt_text": "Staffy artwork mockup"}), "mockup"),
    (dict(photo={"full_width": 1500, "full_height": 1500}), "2048 or 2400"),
    (dict(photo={"full_width": 2400, "full_height": 2000}), "2048 or 2400"),
    (dict(photo={"rank": 2}), "rank 2"), (dict(photo={"rank": 1}), "rank 1"),
])
def test_an_automatic_swap_must_pass_every_guard(change, why):
    """The owner's yes, 2026-10-20: FULL AUTO may delete raw artwork photos, only when every guard passes."""
    photo = {**RAW, **change.get("photo", {})}
    args = (change.get("swap", True), change.get("removes", [777]), change.get("new", [{"image": "x.png"}]), [photo])
    assert why in ew._swap_problem(*args)
    assert ew._swap_problem(True, [777], [{"image": "x.png"}], [RAW]) == ""  # the clean case passes


HERO = {"listing_image_id": 888, "rank": 1, "alt_text": "Our hoodie worn in a winter market, by Kind Retro Co."}
MOCKUP = {"listing_image_id": 889, "rank": 2, "alt_text": "Staffy hoodie by Kind Retro Co."}
BARRED = {"image": "promo/x.png", "alt_text": "Our hoodie worn in a winter market, by Kind Retro Co."}


@pytest.mark.parametrize("change, why", [
    (dict(removes=[888, 889]), "exactly one"), (dict(new=[]), "exactly one"), (dict(new=[BARRED, BARRED]), "exactly one"),
    (dict(photo={"alt_text": "Staffy artwork, full design"}), "removed photo"),
    (dict(photo={"alt_text": "Staffy artwork worn in a cafe"}), "removed photo"),  # never artwork
    (dict(new=[{"image": "promo/x.png", "alt_text": "Size guide"}]), "added photo"),
    (dict(new=[{"image": "promo/x.png"}]), "added photo"),
    (dict(photo={"rank": 2}), "rank 2"),
    (dict(others=[{"listing_image_id": 890, "alt_text": "Size guide for the hoodie"}]), "non-AI photo"),
    (dict(others=[{"listing_image_id": 890, "alt_text": "Our hoodie worn in a park"}]), "non-AI photo"),
])
def test_an_automatic_hero_swap_must_pass_every_guard(change, why):
    """The owner's go, 2026-10-22: the AI hero swapped for its barred copy, guarded."""
    photo = {**HERO, **change.get("photo", {})}
    listed = [photo, *change.get("others", [MOCKUP])]
    assert why in ew._hero_swap_problem(change.get("removes", [888]), change.get("new", [BARRED]), listed)
    assert ew._hero_swap_problem([888], [BARRED], [HERO, MOCKUP]) == ""  # the clean case passes


def test_the_hero_alt_pattern_is_a_setting(monkeypatch):
    """ETSY_HERO_ALT: a shop's own hero wording; a broken pattern falls back to "worn in"."""
    hero = {**HERO, "alt_text": "Our hoodie on a teddy bear"}
    barred = {"image": "promo/x.png", "alt_text": "Our hoodie on a teddy bear"}
    assert "removed photo" in ew._hero_swap_problem([888], [barred], [hero, MOCKUP])  # the default doesn't know it
    monkeypatch.setenv("ETSY_HERO_ALT", "worn in|on a teddy")
    assert ew._hero_swap_problem([888], [barred], [hero, MOCKUP]) == ""
    monkeypatch.setenv("ETSY_HERO_ALT", "([")
    assert ew.hero_alt().pattern == "worn in"


def test_hero_swap_is_used_only_when_asked_and_keeps_the_swap_path(photos, monkeypatch):
    state, writes, _ = photos
    state["images"] = {"count": 2, "results": [dict(HERO), dict(MOCKUP)]}
    seen = {}
    monkeypatch.setattr(ew, "_queue", lambda lid, title, summary, send, wanted, reapply, thumbs, ok:
                        seen.update(ok=ok, send=send) or {"pending": True, "id": "q1"})
    auto = {"id": "a1", "by": "auto:full", "at": "2026-10-22T20:00:00Z"}
    new = [{"image": "listing/size-guide.png", "alt_text": BARRED["alt_text"]}]
    out = ew.listing_update("4412", images=new, remove_images=[888], owner_approval=auto)
    assert out["swap_held"].startswith("a photo delete with an automatic approval must be a swap") and seen["ok"] is None
    out = ew.listing_update("4412", images=new, remove_images=[888], hero_swap=True, owner_approval=auto)
    assert "swap_held" not in out and seen["ok"]["id"] == "a1" and seen["send"]["swap"] is True  # read-back + backup
    out = ew.listing_update("4412", images=new, remove_images=[888], swap=True, hero_swap=True, owner_approval=auto)
    assert out["swap_held"] and seen["ok"] is None  # both set: the strict raw-art guards apply, and a hero fails them


def test_a_failed_guard_drops_the_automatic_approval_so_the_owner_taps_it(photos, monkeypatch):
    state, writes, _ = photos
    state["images"] = {"count": 2, "results": [{**RAW, "rank": 2}, {"listing_image_id": 501, "rank": 1}]}
    seen = {}
    monkeypatch.setattr(ew, "_queue", lambda lid, title, summary, send, wanted, reapply, thumbs, ok:
                        seen.update(ok=ok, send=send) or {"pending": True, "id": "q1"})
    out = ew.listing_update("4412", images=[{"image": "listing/size-guide.png"}], remove_images=[777], swap=True,
                            owner_approval={"id": "a1", "by": "auto:full", "at": "2026-10-20T20:00:00Z"})
    assert out["swap_held"].startswith("the photo is at rank 2") and seen["ok"] is None and "swap" not in seen["send"]
    state["images"]["results"][0]["rank"] = 3  # all guards pass: the approval is kept and execute() is told
    out = ew.listing_update("4412", images=[{"image": "listing/size-guide.png"}], remove_images=[777], swap=True,
                            owner_approval={"id": "a2", "by": "auto:full", "at": "2026-10-20T20:00:00Z"})
    assert "swap_held" not in out and seen["ok"]["id"] == "a2" and seen["send"]["swap"] is True
    out = ew.listing_update("4412", images=[{"image": "listing/size-guide.png"}], remove_images=[777],
                            owner_approval={"id": "a3", "by": "owner", "at": "2026-10-20T20:00:00Z"})
    assert seen["ok"]["id"] == "a3" and "swap" not in seen["send"]  # the owner's own approval isn't held


def test_a_swap_reads_the_replacement_back_and_backs_up_before_deleting(photos, monkeypatch, tmp_path):
    state, writes, _ = photos
    monkeypatch.setattr(ew, "SWAP_BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(ew, "SWAP_FILE", tmp_path / "swaps.json")
    monkeypatch.setattr(ew, "_own_listing", lambda lid: {"listing_id": 4412})
    monkeypatch.setattr(ew.httpx, "get", lambda url, **k: type("R", (), {"status_code": 200, "content": b"j" * 5000})())
    state["images"] = {"count": 2, "results": [dict(RAW), {"listing_image_id": 501, "rank": 1}]}
    send = {"fields": {}, "properties": [], "images": [], "remove_images": [777], "images_alt": [], "swap": True}
    with pytest.raises(h.CommerceError, match="replacement isn't on the listing"):  # nothing uploaded: no delete
        ew.execute({"listing_id": "4412", "send": send})
    assert not [w for w in writes if w[0] == "DELETE"] and not (tmp_path / "backups").exists()
    state["images"]["results"].append({"listing_image_id": 909, "rank": 4})
    send["images"] = [{"image": "listing/size-guide.png", "done_id": 909}]  # uploaded by this request
    res = ew.execute({"listing_id": "4412", "send": send})
    assert res["photos_removed"] == [777] and (tmp_path / "backups" / "4412" / "777.jpg").read_bytes() == b"j" * 5000
    meta = __import__("json").loads((tmp_path / "backups" / "4412" / "777.json").read_text())
    assert meta["url"] == RAW["url_fullxfull"] and meta["alt_text"] == RAW["alt_text"] and len(ew._swaps_today()) == 1


def test_a_hero_at_rank_1_moves_the_other_photos_down(photos):
    """2026-10-18, live on a listing: Etsy put the hero at rank 1 but left the mockup there too, [1, 1, 2, 3, 4, 5]."""
    state, writes, _ = photos
    state["images"] = {"count": 5, "results": [{"listing_image_id": 8714, "rank": 1, "alt_text": "Mockup, front"}] +
                       [{"listing_image_id": 600 + r, "rank": r, "alt_text": f"Photo {r}"} for r in (2, 3, 4)] +
                       [{"listing_image_id": 605, "rank": 5, "alt_text": ""}]}
    ew.listing_update("4412", images=[{"image": "listing/size-guide.png", "rank": 1}])
    [item] = pp.list_pending()
    state["images"]["results"].append({"listing_image_id": 901, "rank": 1})  # what Etsy reports after the upload
    res = pp.decide(item["id"], True)["result"]
    assert res["photos_reranked"] == [{"listing_image_id": 8714, "rank": 2}, {"listing_image_id": 602, "rank": 3},
                                      {"listing_image_id": 603, "rank": 4}, {"listing_image_id": 604, "rank": 5},
                                      {"listing_image_id": 605, "rank": 6}]  # the hero (901) stays at 1
    # each re-save carries the photo's alt text, or Etsy blanks it (live, same day); an empty one isn't sent
    assert writes[1][2] == {"listing_image_id": 8714, "rank": 2, "alt_text": "Mockup, front"} and len(writes) == 6
    assert writes[2][2]["alt_text"] == "Photo 2" and "alt_text" not in writes[5][2]


@pytest.mark.parametrize("kwargs, match", [
    ({"remove_images": [999]}, "not photos of this listing"),
    ({"remove_images": [501, 502]}, "no photo"),
    ({"remove_images": ["abc"]}, "listing_image_ids"),
    ({"images_alt": [{"listing_image_id": 501, "alt_text": ""}]}, "images_alt"),
    ({"images_alt": [{"listing_image_id": 501, "alt_text": "x" * 501}]}, "500"),
    ({"remove_images": [501], "images_alt": [{"listing_image_id": 501, "alt_text": "a"}]}, "both"),
])
def test_bad_photo_edits_are_refused_before_anything_queues(photos, kwargs, match):
    photos[0]["images"] = {"count": 2, "results": [{"listing_image_id": 501}, {"listing_image_id": 502}]}
    with pytest.raises(h.CommerceError, match=match):
        ew.listing_update("4412", **kwargs)
    assert pp.list_pending() == [] and photos[1] == []


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


@pytest.mark.parametrize("value, edits, deactivations", [("150", 150, 100), ("500", 500, 100), ("900", 500, 100),
                                                         ("x", 50, 5), ("", 50, 5)])
def test_the_edit_limit_goes_to_500_and_deactivation_stays_at_100(monkeypatch, value, edits, deactivations):
    """2026-10-14, the owner: raise the edit limit to 150 (30 publishes need 60 edits a night, plus repairs);
    2026-10-18: to 500."""
    from tools.etsy import write as w
    monkeypatch.setenv("ETSY_EDIT_DAILY_MAX", value)
    monkeypatch.setenv("ETSY_DEACTIVATE_DAILY_MAX", value)
    assert w.edit_daily_max() == edits and w.deactivate_daily_max() == deactivations
