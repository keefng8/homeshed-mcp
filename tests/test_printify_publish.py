"""printify.product.publish (to-do #59): a tool call only queues; the owner's approval publishes, after checking again,
within a daily cap. Printify is faked; files go under tmp_path."""
import pytest

import printify_pending as pp
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import publish as pb

DRAFT = {"id": "abc123", "title": "Retro Frenchie Tee", "tags": ["dog"] * 13, "images": [1, 2, 3],
         "variants": [{"id": 1, "price": 2499, "is_enabled": True}, {"id": 2, "price": 2699, "is_enabled": True},
                      {"id": 3, "price": 1, "is_enabled": False}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setattr(pp, "PENDING_FILE", tmp_path / "pending.json")
    monkeypatch.setattr(pp, "DONE_FILE", tmp_path / "done.json")
    monkeypatch.delenv("PRINTIFY_PUBLISH_DAILY_MAX", raising=False)
    switches = {pf.SETTING: True, pf.PUBLISH_SETTING: True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key)
                        else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    state = {"product": dict(DRAFT)}
    posts = []
    monkeypatch.setattr(pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pf, "get", lambda path, params=None: state["product"])
    monkeypatch.setattr(pf, "publish_post", lambda path, body: posts.append((path, body)) or {})
    return state, posts, switches


def test_a_call_only_queues_and_approval_publishes(fake):
    state, posts, _ = fake
    out = pb.product_publish("abc123")
    assert out["pending"] is True and posts == []  # nothing published by the call itself
    assert out["publish"].startswith('Publish "Retro Frenchie Tee" (2 variants, 24.99-26.99) to your Etsy shop')
    [item] = pp.list_pending()
    assert item["checks"]["variants_enabled"] == 2 and item["checks"]["daily_max"] == 5
    res = pp.decide(item["id"], True)
    assert res["approved"] and res["result"]["publishing"] is True
    assert posts == [("/v1/shops/29235685/products/abc123/publish.json",
                      {f: True for f in pb.PUBLISH_FIELDS})]
    assert pp.list_pending() == [] and len(pp.published_today()) == 1


def test_decline_drops_it_and_nothing_is_published(fake):
    _, posts, _ = fake
    item = pb.product_publish("abc123")
    assert pp.decide(item["id"], False)["approved"] is False and posts == [] and pp.list_pending() == []


def test_refusals(fake):
    state, posts, switches = fake
    switches[pf.PUBLISH_SETTING] = False
    with pytest.raises(h.CommerceError, match="switched off"):
        pb.product_publish("abc123")
    switches[pf.PUBLISH_SETTING] = True
    state["product"] = {**DRAFT, "external": {"id": "etsy-1"}}
    with pytest.raises(h.CommerceError, match="already published"):
        pb.product_publish("abc123")
    state["product"] = {**DRAFT, "is_locked": True}
    with pytest.raises(h.CommerceError, match="locked"):
        pb.product_publish("abc123")
    state["product"] = dict(DRAFT)
    pb.product_publish("abc123")
    with pytest.raises(h.CommerceError, match="already waiting"):
        pb.product_publish("abc123")
    with pytest.raises(Exception):
        pb.product_publish("../etc")
    assert posts == []


def test_approval_rechecks_and_a_failure_stays_waiting(fake, monkeypatch):
    state, posts, _ = fake
    item = pb.product_publish("abc123")
    state["product"] = {**DRAFT, "external": {"id": "etsy-1"}}  # published by hand meanwhile
    with pytest.raises(h.CommerceError, match="already published"):
        pp.decide(item["id"], True)
    [still] = pp.list_pending()
    assert still["last_error"].startswith("that product is already published") and posts == []


def test_daily_cap_holds_approvals(fake, monkeypatch):
    state, posts, _ = fake
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "1")
    first = pb.product_publish("abc123")
    pp.decide(first["id"], True)
    state["product"] = {**DRAFT, "id": "def456"}
    second = pb.product_publish("def456")
    with pytest.raises(pp.PendingError, match="daily limit"):
        pp.decide(second["id"], True)
    assert len(posts) == 1 and len(pp.list_pending()) == 1
