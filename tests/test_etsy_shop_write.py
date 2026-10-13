"""etsy.shop.update (tools/etsy/shop_write.py): a call only queues what differs from the shop, with the full new texts for
the owner's card; approval sets them and makes new sections once; the shop scope is asked only while its switch is on.
Etsy is faked; files go under tmp_path."""
import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.etsy import _client as et
from tools.etsy import connect
from tools.etsy import shop_write as sw

SHOP = "29235685"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.delenv("ETSY_SHOP_EDIT_DAILY_MAX", raising=False)
    switches = {et.SETTING: True, et.SHOP_WRITE_SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"shop": {"shop_id": int(SHOP), "title": "Tees for dog people", "announcement": "",
                      "sale_message": "Thanks!"},
             "sections": [{"shop_section_id": 1, "title": "Dogs"}]}

    def get(path, params=None, oauth=True):
        return {"results": state["sections"]} if path.endswith("/sections") else state["shop"]

    writes = []

    def write(method, path, form, setting, label):
        writes.append((method, path, form, setting))
        if path.endswith("/sections"):
            state["sections"].append({"shop_section_id": 100 + len(writes), "title": form["title"]})
            return {"shop_section_id": 100 + len(writes)}
        return {}
    monkeypatch.setattr(et, "shop_id", lambda: SHOP)
    monkeypatch.setattr(et, "get", get)
    monkeypatch.setattr(et, "write", write)
    return state, writes, switches


def test_only_what_differs_is_queued_with_the_full_texts_and_approval_sets_it(fake):
    state, writes, _ = fake
    long = "Christmas orders: order by 10 December for UK delivery. " * 3
    out = sw.shop_update(title="Tees for dog people", announcement=long, sections=["dogs", "Christmas", "Christmas"])
    assert out["pending"] is True and writes == []  # nothing changed by the call itself
    assert "announcement" in out["etsy_shop"] and "shop title" not in out["etsy_shop"]  # the title is already that
    [item] = pp.list_pending()
    assert item["action"] == "etsy_shop" and item["checks"]["sections"] == ["Christmas"]  # "dogs" exists, no repeat
    assert long.strip() in item["payload"]["detail"]  # the owner's card shows the whole text, not the 300 summary
    res = pp.decide(item["id"], True)
    put, section = writes
    assert put == ("PUT", f"/v3/application/shops/{SHOP}", {"announcement": long.strip()}, et.SHOP_WRITE_SETTING)
    assert section[:3] == ("POST", f"/v3/application/shops/{SHOP}/sections", {"title": "Christmas"})
    assert res["result"]["sections"][0]["made"] == "now" and len(pp.done_today("etsy_shop")) == 1


def test_a_section_made_meanwhile_is_not_made_twice(fake):
    state, writes, _ = fake
    sw.shop_update(sections=["Halloween"])
    state["sections"].append({"shop_section_id": 7, "title": "halloween"})  # by hand, or an earlier try
    [item] = pp.list_pending()
    res = pp.decide(item["id"], True)
    assert writes == [] and res["result"]["sections"] == [{"title": "Halloween", "shop_section_id": 7, "made": "earlier"}]


def test_already_as_asked_and_bad_input_and_the_switch(fake):
    _, _, switches = fake
    assert sw.shop_update(sale_message=" Thanks! ", sections=["Dogs"])["pending"] is False
    for kw, msg in (({}, "nothing to change"), ({"title": "x" * 56}, "at most 55"), ({"title": " "}, "can't be empty"),
                    ({"sections": ["A" * 25]}, "1-24"), ({"sections": []}, "1-10")):
        with pytest.raises(h.CommerceError, match=msg):
            sw.shop_update(**kw)
    switches[et.SHOP_WRITE_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        sw.shop_update(title="New")
    assert pp.list_pending() == []


def test_an_empty_text_clears_it(fake):
    sw.shop_update(sale_message="")
    [item] = pp.list_pending()
    assert item["payload"]["send"] == {"sale_message": ""} and "cleared" in item["summary"]


def test_connect_asks_for_shop_edits_only_while_the_shop_switch_is_on(monkeypatch):
    on = set()
    monkeypatch.setattr(h, "enabled", lambda key: key in on)
    assert connect.scopes() == connect.SCOPES
    on.add(et.SHOP_WRITE_SETTING)
    assert connect.scopes().split()[-1] == "shops_w" and "listings_w" not in connect.scopes()
    on.add(et.WRITE_SETTING)
    assert set(connect.scopes().split()) >= {"listings_w", "shops_w"}
