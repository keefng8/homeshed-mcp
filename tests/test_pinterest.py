"""pinterest.* (tools/pinterest): a pin or board is only ever asked for and waits for the owner's approval of that one;
the pin links only to the shop's own active listing, says when AI helped (description line + Pinterest's ai_disclosures),
isn't repeated, and is checked again on approval; Trial access goes to the sandbox host with its own tokens. Pinterest,
Etsy and the image store are faked; files go under tmp_path."""
import base64

import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.etsy import _client as et
from tools.pinterest import _client as pc
from tools.pinterest import pins

SHOP = "29235685"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
LINK = "https://www.etsy.com/listing/4590957113/retro-staffy-tee?ref=shop"


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.setattr(pins, "PINS_FILE", tmp_path / "pins.json")
    monkeypatch.delenv("PINTEREST_DAILY_MAX", raising=False)
    switches = {et.SETTING: True, pc.SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"listing": {"listing_id": 4590957113, "shop_id": int(SHOP), "state": "active", "title": "Retro Staffy Tee"},
             "boards": [{"id": "111", "name": "Staffy", "privacy": "PUBLIC", "pin_count": 0}],
             "image": PNG, "sandbox": True}
    monkeypatch.setattr(et, "shop_id", lambda: SHOP)
    monkeypatch.setattr(et, "get", lambda path, params=None, oauth=True: state["listing"])
    monkeypatch.setattr(pins, "_image", lambda ref: state["image"])
    monkeypatch.setattr("tools.etsy.write._thumb", lambda data: "dGh1bWI=")
    monkeypatch.setattr(pc, "sandbox", lambda: state["sandbox"])
    calls = []

    def call(method, path, json=None, params=None):
        calls.append((method, path, json))
        if path == "/v5/boards" and method == "GET":
            return {"items": state["boards"], "bookmark": None}
        if path == "/v5/boards":
            state["boards"].append({"id": "222", "name": json["name"]})
            return {"id": "222"}
        if path == "/v5/pins":
            return {"id": "987654"}
        return {"username": "kindretroco"}
    monkeypatch.setattr(pc, "call", call)
    return state, calls, switches


def test_a_pin_is_only_asked_for_and_approval_makes_it_with_the_ai_disclosure(fake):
    state, calls, _ = fake
    out = pins.pin_create("listing/staffy-pin.png", "Retro Staffy T-Shirt, Dog Lover Gift", "A sunset Staffy.", LINK,
                          "staffy", ai_generated=True, alt_text="A black tee with a retro Staffy print")
    assert out["pending"] is True and not [c for c in calls if c[1] == "/v5/pins"]  # nothing pinned by the call
    [item] = pp.list_pending()
    assert item["action"] == "pinterest_pin" and item["payload"]["link"] == "https://www.etsy.com/listing/4590957113"
    assert item["payload"]["description"].endswith("AI-assisted design.") and item["payload"]["thumbs"]
    assert "TITLE\nRetro Staffy T-Shirt" in item["payload"]["detail"] and "in the sandbox" in item["summary"]
    res = pp.decide(item["id"], True)
    body = next(c[2] for c in calls if c[1] == "/v5/pins")
    assert body["board_id"] == "111" and body["ai_disclosures"] == {"values": ["AI_MODIFIED"]}
    assert body["media_source"]["source_type"] == "image_base64" and base64.b64decode(body["media_source"]["data"]) == PNG
    assert body["alt_text"].startswith("A black tee") and res["result"]["pin_id"] == "987654"
    assert len(pp.done_today("pinterest_pin")) == 1


def test_no_ai_means_no_ai_line_or_field(fake):
    _, calls, _ = fake
    pins.pin_create("a.png", "Title", "Words.", LINK, "111", ai_generated=False)
    pp.decide(pp.list_pending()[0]["id"], True)
    body = next(c[2] for c in calls if c[1] == "/v5/pins")
    assert "ai_disclosures" not in body and body["description"] == "Words."


@pytest.mark.parametrize("change, match", [
    ({"link": "https://example.com/x"}, "one of your Etsy listings"),
    ({"board": "Cats"}, 'no board "Cats"'),
    ({"title": "x" * 101}, "at most 100"),
    ({"description": "x" * 790}, "including the AI line"),
    ({"ai_generated": "yes"}, "ai_generated is required"),
])
def test_bad_asks_are_refused_and_nothing_waits(fake, change, match):
    kw = {"image": "a.png", "title": "T", "description": "D", "link": LINK, "board": "Staffy", "ai_generated": True,
          **change}
    with pytest.raises(h.CommerceError, match=match):
        pins.pin_create(**kw)
    assert pp.list_pending() == []


def test_only_the_shops_own_active_listing(fake):
    state, _, _ = fake
    state["listing"] = {**state["listing"], "shop_id": 1}
    with pytest.raises(h.CommerceError, match="isn't in your Etsy shop"):
        pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=False)
    state["listing"] = {**state["listing"], "shop_id": int(SHOP), "state": "inactive"}
    with pytest.raises(h.CommerceError, match="inactive on Etsy"):
        pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=False)


def test_checked_again_on_approval_and_never_repeated(fake):
    state, calls, _ = fake
    pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=False)
    [item] = pp.list_pending()
    state["image"] = PNG + b"x"
    with pytest.raises(h.CommerceError, match="image has changed"):
        pp.decide(item["id"], True)
    state["image"] = PNG
    state["sandbox"] = False  # switched to Standard access meanwhile
    with pytest.raises(h.CommerceError, match="decline it and ask again"):
        pp.decide(item["id"], True)
    state["sandbox"] = True
    pp.decide(item["id"], True)
    with pytest.raises(h.CommerceError, match="pinned in the last 30 days"):
        pins.pin_create("a.png", "T2", "D2", LINK, "Staffy", ai_generated=False)


def test_the_daily_cap_holds_approvals(fake, monkeypatch):
    state, _, _ = fake
    monkeypatch.setenv("PINTEREST_DAILY_MAX", "1")
    pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=False)
    pp.decide(pp.list_pending()[0]["id"], True)
    state["image"] = PNG + b"2"
    pins.pin_create("b.png", "T", "D", "https://www.etsy.com/listing/4590957114", "Staffy", ai_generated=False)
    with pytest.raises(pp.NotYet, match="PINTEREST_DAILY_MAX"):
        pp.decide(pp.list_pending()[0]["id"], True)


def test_one_pin_per_listing_a_week_whatever_the_image(fake):
    """A terms check (10 Oct): no duplicate pins of the same listing within 7 days."""
    state, _, _ = fake
    pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=False)
    state["image"] = PNG + b"other"
    with pytest.raises(h.CommerceError, match="already waiting"):  # a second one while the first waits
        pins.pin_create("b.png", "T", "D", LINK, "Staffy", ai_generated=False)
    state["image"] = PNG
    pp.decide(pp.list_pending()[0]["id"], True)
    state["image"] = PNG + b"other"
    with pytest.raises(h.CommerceError, match="pinned in the last 7 days"):
        pins.pin_create("b.png", "T", "D", LINK, "Staffy", ai_generated=False)
    rows = pins._made()
    rows[0]["at"] -= 8 * 86400  # a week later it's allowed again
    pins.PINS_FILE.write_text(pins.json.dumps({"pins": rows}), encoding="utf-8")
    assert pins.pin_create("b.png", "T", "D", LINK, "Staffy", ai_generated=False)["pending"] is True


def test_nothing_read_from_pinterest_is_kept(fake):
    """Pinterest's guidelines: only what we made is stored (the pin's id, our image's fingerprint, our link)."""
    pins.pin_create("a.png", "T", "D", LINK, "Staffy", ai_generated=True)
    pp.decide(pp.list_pending()[0]["id"], True)
    assert set(pins._made()[0]) == {"at", "pin_id", "sha256", "link", "sandbox", "ai_generated"}


def test_a_board_waits_for_approval_and_is_never_made_twice(fake):
    state, calls, switches = fake
    with pytest.raises(h.CommerceError, match="already has a board"):
        pins.board_create("staffy")
    out = pins.board_create("Dog lover gifts UK", "Gifts for people who love dogs")
    assert out["pending"] is True and not [c for c in calls if c[0] == "POST"]
    res = pp.decide(pp.list_pending()[0]["id"], True)
    assert res["result"]["made"] == "now" and ("POST", "/v5/boards", {"name": "Dog lover gifts UK", "privacy": "PUBLIC",
                                                "description": "Gifts for people who love dogs"}) in calls
    switches[pc.SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        pins.board_create("Another")


def test_the_sign_in_uses_the_sandbox_host_basic_auth_and_its_own_tokens(monkeypatch):
    saved, sent = {}, []
    monkeypatch.setattr(pc, "sandbox", lambda: True)
    monkeypatch.setattr(pc, "_app", lambda: ("app-id", "app-secret"))
    monkeypatch.setattr("vault.set_credential", lambda name, value, **kw: saved.__setitem__(name, value))

    class R:
        def __init__(self, body):
            self.body = body

    def request(service, method, path, *, headers, secrets, params=None, data=None, json=None, files=None):
        sent.append((service, method, path, headers.get("Authorization"), data))
        return R({"access_token": "acc", "refresh_token": "ref"} if path == "/v5/oauth/token" else {"username": "kr"})
    monkeypatch.setattr(h, "request", request)
    monkeypatch.setattr(h, "json_of", lambda service, resp: resp.body)
    start = pc.start("https://panel.example/api/pinterest/callback")
    assert start["url"].startswith("https://www.pinterest.com/oauth/?") and "pins%3Awrite" in start["url"]
    state = start["url"].split("state=")[1].split("&")[0]
    out = pc.finish("the-code", state)
    service, method, path, auth, form = sent[0]
    assert (service, method, path) == ("pinterest-sandbox", "POST", "/v5/oauth/token")
    assert auth == "Basic " + base64.b64encode(b"app-id:app-secret").decode() and form["grant_type"] == "authorization_code"
    assert saved == {"PINTEREST_SANDBOX_ACCESS_TOKEN": "acc", "PINTEREST_SANDBOX_REFRESH_TOKEN": "ref"}
    assert out == {"connected": True, "username": "kr", "sandbox": True}
    with pytest.raises(h.CommerceError, match="already used"):
        pc.finish("the-code", state)
    assert h.HOSTS["pinterest-sandbox"] == "api-sandbox.pinterest.com" and pc.names(False)[0] == "PINTEREST_ACCESS_TOKEN"
