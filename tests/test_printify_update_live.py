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
    return state, sent, switches


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
    (put, publish) = sent
    assert put[0] == "PUT" and put[3] == pf.LIVE_UPDATE_SETTING and put[2]["title"] == "Retro French Bulldog Tee"
    assert [v["price"] for v in put[2]["variants"]] == [2199, 2299] and all("sku" not in v for v in put[2]["variants"])
    assert publish[2] == {"title": True, "description": False, "images": False, "variants": True, "tags": True,
                          "keyFeatures": False, "shipping_template": False}
    assert len(pp.done_today("update")) == 1 and pp.published_today() == []  # not counted as a publish
    assert pp.done_today("update")[0]["old"]["title"] == "Retro Frenchie Tee"  # kept for undo


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
    assert sent == [] and pp.list_pending()[0]["last_error"]  # stays waiting, with the reason


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
