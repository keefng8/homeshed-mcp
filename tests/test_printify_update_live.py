"""printify.product.update_live: a call only queues; the owner's approval edits the live product and publishes just the
changed parts, after checking it hasn't changed since. SKUs never change on a live product. Printify is faked; files go
under tmp_path. Test list drafted by the local model (2026-10-08)."""
import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import update_live as ul

LIVE = {"id": "abc123", "title": "Retro Frenchie Tee", "description": "A tee.", "tags": ["dog", "frenchie"],
        "external": {"id": "4412", "handle": "https://www.etsy.com/listing/4412"},
        "variants": [{"id": 1, "price": 2199, "cost": 1112, "is_enabled": True, "sku": "S1"},
                     {"id": 2, "price": 2199, "cost": 1112, "is_enabled": True, "sku": "S2"}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.setattr(pp, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(pp, "STALE_FILE", tmp_path / "stale.json")
    for name in ("PRINTIFY_MIN_MARGIN", "PRINTIFY_DELIVERY_ESTIMATE_PENCE", "PRINTIFY_LIVE_UPDATE_DAILY_MAX"):
        monkeypatch.delenv(name, raising=False)
    switches = {pf.SETTING: True, pf.LIVE_UPDATE_SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"product": {**LIVE, "variants": [dict(v) for v in LIVE["variants"]]}}
    sent = []
    monkeypatch.setattr(pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pf, "get", lambda path, params=None: state["product"])
    monkeypatch.setattr(pf, "put", lambda path, body, **kw: sent.append(("PUT", path, body, kw["setting"])) or {})
    monkeypatch.setattr(pf, "publish_post", lambda path, body, **kw: sent.append(("PUBLISH", path, body, kw["setting"])) or {})
    state["etsy"] = ["active"]  # the Etsy listing's state, read by the re-activation guard (last one repeats)
    monkeypatch.setattr(ul, "_etsy_state", lambda p: state["etsy"].pop(0) if len(state["etsy"]) > 1 else state["etsy"][0])
    monkeypatch.setattr(ul, "_recheck_later", lambda p, before: sent.append(("RECHECK", before)))
    return state, sent, switches


def test_a_listing_off_on_etsy_is_held_when_asked_and_on_approval(fake):
    """10 Oct: a re-publish switched the Black Staffy tee back on after it was off."""
    state, sent, _ = fake
    state["etsy"] = ["inactive"]
    with pytest.raises(h.CommerceError, match="listing is off on Etsy: the owner turns it on first"):
        ul.product_update_live("abc123", title="New title")
    assert pp.list_pending() == []
    state["etsy"] = ["active"]
    item = ul.product_update_live("abc123", title="New title")
    state["etsy"] = ["inactive"]  # turned off while it waited
    with pytest.raises(h.CommerceError, match="listing is off on Etsy"):
        pp.decide(item["id"], True)
    assert sent == [] and pp.list_pending()[0]["last_error"].startswith("listing is off on Etsy")
    state["etsy"] = ["active", "inactive"]  # off in the seconds between the check and the publish
    with pytest.raises(h.CommerceError, match="saved in Printify but not published"):
        pp.decide(item["id"], True)
    assert [s[0] for s in sent] == ["PUT"]  # no publish


def test_etsy_unreadable_holds_it(fake, monkeypatch):
    def down(p):
        raise h.CommerceError("Etsy HTTP 503")
    monkeypatch.setattr(ul, "_etsy_state", down)
    with pytest.raises(h.CommerceError, match="couldn't check on Etsy"):
        ul.product_update_live("abc123", title="New title")


@pytest.mark.parametrize("seen, pushed", [(["inactive", "active"], True),
                                          (["active"] * 2, False), (["inactive"] * 2, False)])
def test_the_backstop_pushes_when_a_listing_turned_off_meanwhile_is_back_on(monkeypatch, seen, pushed):
    pushes = []
    monkeypatch.setattr(ul, "_etsy_state", lambda p: seen.pop(0))
    monkeypatch.setattr("tools.notify.send.send", lambda message, **kw: pushes.append((kw.get("title"), message)))
    monkeypatch.setattr(ul.threading, "Thread", lambda target, **kw: type("T", (), {"start": staticmethod(target)})())
    ul._recheck_later(dict(LIVE), "active", sleep=lambda s: None)
    assert bool(pushes) is pushed
    if pushed:
        assert pushes[0][0] == "Etsy listing switched back on" and "was inactive" in pushes[0][1]


def test_the_backstop_counts_its_etsy_reads_under_the_app_that_asked(monkeypatch):
    import contextvars
    import clients
    seen = []
    monkeypatch.setattr(ul, "_etsy_state", lambda p: seen.append((clients.current_client.get(), h._COUNT_AS.get())) or "active")
    # a real thread starts with an empty context: run the target in one
    monkeypatch.setattr(ul.threading, "Thread",
                        lambda target, **kw: type("T", (), {"start": staticmethod(lambda: contextvars.Context().run(target))})())
    token = clients.current_client.set("app-1")
    try:
        ul._recheck_later(dict(LIVE), "active", sleep=lambda s: None)
    finally:
        clients.current_client.reset(token)
    assert seen == [("app-1", ("printify.update_live:recheck", True))] * 2


def test_a_call_only_queues_and_approval_edits_then_publishes_the_changed_parts(fake):
    _, sent, _ = fake
    out = ul.product_update_live("abc123", title="Retro French Bulldog Tee", tags=["dog", "french bulldog"],
                                 variants=[{"id": 2, "price_pence": 2299}])
    assert out["pending"] is True and sent == [] and out["changed"] == ["prices", "tags", "title"]
    assert out["update"].startswith('Update live "Retro Frenchie Tee" on your Etsy shop: title "Retro Frenchie Tee" ->')
    assert "+french bulldog -frenchie" in out["update"] and "21.99->22.99" in out["update"]
    [item] = pp.list_pending()
    assert item["action"] == "update" and item["checks"]["daily_max"] == 10
    res = pp.decide(item["id"], True)
    assert res["approved"] and res["result"]["publishing"] is True
    (put, publish, recheck) = sent
    assert recheck == ("RECHECK", "active")  # the backstop watches it after the publish
    assert put[0] == "PUT" and put[3] == pf.LIVE_UPDATE_SETTING and put[2]["title"] == "Retro French Bulldog Tee"
    assert [v["price"] for v in put[2]["variants"]] == [2199, 2299] and all("sku" not in v for v in put[2]["variants"])
    assert publish[2] == {"title": True, "description": False, "images": False, "variants": True, "tags": True,
                          "keyFeatures": False, "shipping_template": False}
    assert len(pp.done_today("update")) == 1 and pp.published_today() == []  # not counted as a publish
    assert pp.done_today("update")[0]["old"]["title"] == "Retro Frenchie Tee"  # kept for undo


def test_a_size_can_be_taken_off_sale_without_touching_skus_but_never_all_of_them(fake):
    """The shop app, 2026-10-09: three Heather Navy sizes (a cotton-poly blend) slipped onto a 100% cotton tee."""
    state, sent, _ = fake
    out = ul.product_update_live("abc123", variants=[{"id": 2, "enabled": False}])
    assert out["pending"] is True and out["changed"] == ["enabled"] and "variants off: 2" in out["update"]
    with pytest.raises(h.CommerceError, match="every variant"):
        ul.product_update_live("abc123", variants=[{"id": 1, "enabled": False}, {"id": 2, "enabled": False}])
    with pytest.raises(h.CommerceError, match="true or false"):
        ul.product_update_live("abc123", variants=[{"id": 2, "enabled": "no"}])
    [item] = pp.list_pending()
    pp.decide(item["id"], True)
    put = sent[0][2]
    assert [(v["id"], v["is_enabled"]) for v in put["variants"]] == [(1, True), (2, False)]
    assert all("sku" not in v for v in put["variants"]) and pp.done_today("update")[0]["old"]["enabled"] == {"2": True}


def test_an_on_off_change_meanwhile_is_stale(fake):
    state, sent, _ = fake
    out = ul.product_update_live("abc123", variants=[{"id": 2, "enabled": False}])
    state["product"]["variants"][1]["is_enabled"] = False  # someone switched it off in Printify meanwhile
    with pytest.raises(h.CommerceError, match="variant 2 on/off"):
        pp.decide(out["id"], True)
    assert sent == []


@pytest.mark.parametrize("product, kwargs, match", [
    ({}, dict(variants=[{"id": 1, "sku": "NEW"}]), "SKUs can't change"),
    ({"external": None}, dict(title="New"), "isn't published"),
    ({"is_locked": True}, dict(title="New"), "locked"),
    ({}, dict(variants=[{"id": 1, "price_pence": 1500}]), "under the floor"),
])
def test_refusals_send_and_queue_nothing(fake, product, kwargs, match):
    state, sent, _ = fake
    state["product"].update(product)
    with pytest.raises(h.CommerceError, match=match):
        ul.product_update_live("abc123", **kwargs)
    assert sent == [] and pp.list_pending() == []


def test_one_request_per_product_and_its_own_switch(fake):
    _, _, switches = fake
    ul.product_update_live("abc123", title="New title")
    with pytest.raises(h.CommerceError, match="already waiting"):
        ul.product_update_live("abc123", description="New words")
    switches[pf.LIVE_UPDATE_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        ul.product_update_live("abc123", title="Other")


def test_a_product_changed_since_it_was_asked_is_not_overwritten(fake):
    state, sent, _ = fake
    item = ul.product_update_live("abc123", title="New title")
    state["product"]["title"] = "Edited by hand meanwhile"
    with pytest.raises(h.CommerceError, match="changed since this was asked"):
        pp.decide(item["id"], True)
    assert sent == [] and pp.list_pending() == []  # withdrawn (2026-10-25): the asker asks again
    assert pp.history()[0]["outcome"] == "withdrawn" and "changed since" in pp.history()[0]["note"]


def test_the_daily_cap_is_counted_at_approval(fake, monkeypatch):
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_DAILY_MAX", "1")
    first = ul.product_update_live("abc123", title="New title")
    pp.decide(first["id"], True)
    second = pp.add("def456", "Update another", {}, action="update", payload={"title": "x", "old": {}})
    with pytest.raises(pp.PendingError, match="daily limit"):
        pp.decide(second["id"], True)


def test_already_as_asked_queues_nothing(fake):
    assert ul.product_update_live("abc123", title="Retro Frenchie Tee")["pending"] is False
    assert pp.list_pending() == []


def test_a_boost_raises_the_daily_limit_until_its_end(monkeypatch):
    import printify_pending
    from datetime import datetime
    end = datetime.fromisoformat("2026-11-01T00:00:00+00:00").timestamp()
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_DAILY_MAX", "50")
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_BOOST", "100 until 2026-11-01T00:00:00+00:00")
    assert printify_pending.update_daily_max(now=end - 60) == 100
    assert printify_pending.update_daily_max(now=end) == 50  # it ends by itself
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_BOOST", "500 until 2026-11-01T00:00:00+00:00")
    assert printify_pending.update_daily_max(now=end - 60) == 100  # never above 100
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_BOOST", "100 until someday")
    assert printify_pending.update_daily_max(now=end - 60) == 50  # a bad date changes nothing
    monkeypatch.delenv("PRINTIFY_LIVE_UPDATE_BOOST")
    monkeypatch.setenv("PRINTIFY_LIVE_UPDATE_DAILY_MAX", "90")
    assert printify_pending.update_daily_max() == 50  # the plain setting keeps its ceiling


AREAS = [{"variant_ids": [1, 2], "placeholders": [
    {"position": "back", "images": [{"id": "img-back", "x": 0.5, "y": 0.5, "scale": 0.9, "angle": 0}]},
    {"position": "front", "images": [{"id": "img-a", "x": 0.3, "y": 0.3, "scale": 0.2, "angle": 0},
                                     {"id": "img-b", "x": 0.7, "y": 0.3, "scale": 0.2, "angle": 0}]}]}]


def test_a_print_area_move_waits_for_the_owner_then_applies_without_touching_the_etsy_photos(fake):
    """1 Nov: the zip hoodie's back print sat under the hood at y 0.5; the Director asked to move it to 0.57."""
    import copy
    state, sent, _ = fake
    state["product"]["print_areas"] = copy.deepcopy(AREAS)
    item = ul.product_update_live("abc123", move_print_areas=[{"position": "Back", "y": 0.57}])
    assert item["pending"] is True and item["changed"] == ["print_areas"] and sent == []
    assert "back print y 0.5 -> 0.57" in pp.list_pending()[0]["summary"]
    pp.decide(item["id"], True)
    put, publish = sent[0], sent[1]
    back = put[2]["print_areas"][0]["placeholders"][0]["images"][0]
    assert put[0] == "PUT" and back == {"id": "img-back", "x": 0.5, "y": 0.57, "scale": 0.9, "angle": 0}
    assert put[2]["print_areas"][0]["placeholders"][1] == AREAS[0]["placeholders"][1]  # the front is as it was
    assert publish[0] == "PUBLISH" and not any(publish[2].values())  # no images, words or variants re-sent


def test_a_move_is_never_bot_or_app_approved_and_odd_positions_are_refused(fake):
    import copy
    state, sent, _ = fake
    state["product"]["print_areas"] = copy.deepcopy(AREAS)
    with pytest.raises(h.CommerceError, match="always waits for the owner"):
        ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 0.57}],
                               owner_approval={"id": "ok-1", "by": "bot:director", "at": "2026-11-01T00:00:00Z"})
    with pytest.raises(h.CommerceError, match="several images"):
        ul.product_update_live("abc123", move_print_areas=[{"position": "front", "y": 0.4}])
    with pytest.raises(h.CommerceError, match="no image"):
        ul.product_update_live("abc123", move_print_areas=[{"position": "sleeve", "y": 0.4}])
    with pytest.raises(h.CommerceError, match="y must be a number"):
        ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 1.4}])
    with pytest.raises(h.CommerceError, match="at least one of"):
        ul.product_update_live("abc123", move_print_areas=[{"position": "back"}])
    assert ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 0.5}])["pending"] is False
    assert pp.list_pending() == [] and sent == []


def test_a_move_made_in_printify_meanwhile_makes_it_stale(fake):
    import copy
    state, sent, _ = fake
    state["product"]["print_areas"] = copy.deepcopy(AREAS)
    item = ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 0.57}])
    state["product"]["print_areas"][0]["placeholders"][0]["images"][0]["y"] = 0.55
    with pytest.raises(ul.Stale, match="print areas"):
        pp.decide(item["id"], True)
    assert sent == []


def test_the_move_sends_only_what_printify_takes_back(fake):
    """1 Nov: the read-back sent as it was (image name, src, size, flips, layer fields, an empty front placeholder)
    got HTTP 400 "Validation failed" on the live zip."""
    state, sent, _ = fake
    state["product"]["print_areas"] = [{"variant_ids": [1, 2], "placeholders": [
        {"position": "back", "decoration_method": "dtg", "images": [
            {"id": "img-back", "name": "art.png", "type": "image/png", "height": 4000, "width": 3000, "x": 0.5,
             "y": 0.5, "scale": 1, "angle": 0, "flipX": False, "flipY": False, "src": "https://x", "layerType": "image",
             "imageId": "img-back"}]},
        {"position": "front", "decoration_method": "dtg", "images": []}]}]
    item = ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 0.57}])
    pp.decide(item["id"], True)
    assert sent[0][2]["print_areas"] == [{"variant_ids": [1, 2], "placeholders": [
        {"position": "back", "images": [{"id": "img-back", "x": 0.5, "y": 0.57, "scale": 1, "angle": 0}]}]}]
    assert ul.product_update_live("abc123", move_print_areas=[{"position": "back", "y": 0.5}])["pending"] is False


def test_printifys_validation_reason_is_kept(monkeypatch):
    import httpx
    resp = httpx.Response(400, json={"status": "error", "message": "Validation failed.",
                                     "errors": {"reason": "print_areas.0.placeholders.1.images must not be empty"}})
    assert h._reason(resp) == "Validation failed. print_areas.0.placeholders.1.images must not be empty"
    assert h._reason(httpx.Response(400, json={"message": "Not found"})) == "Not found"
