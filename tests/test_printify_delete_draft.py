"""printify.product.delete_draft: a bot the owner named deletes an unpublished draft only, after saving it. Printify
is faked; files go under tmp_path."""
import json

import pytest

import clients
import printify_pending as pp
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import delete as dl

DRAFT = {"id": "6ae6a387ba6e72afd20e3ffa", "title": "TEST zip back placement y0.57 - do not publish"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    for name in ("PENDING_FILE", "DONE_FILE", "HISTORY_FILE"):
        monkeypatch.setattr(pp, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(dl, "DELETED_FILE", tmp_path / "deleted.json")
    monkeypatch.setattr(pp, "trusted_clients", lambda setting=pp.TRUST_SETTING:
                        {"director"} if setting == dl.BOTS_SETTING else set())
    state = {"product": dict(DRAFT)}
    gone = []
    monkeypatch.setattr(pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pf, "get", lambda path, params=None: state["product"])
    monkeypatch.setattr(pf, "delete", lambda path: gone.append(path) or {})
    return state, gone, tmp_path / "deleted.json"


def test_a_named_bot_deletes_a_draft_and_it_is_kept_first(fake):
    state, gone, kept = fake
    out = dl.product_delete_draft(DRAFT["id"], "Director", "test draft for the zip back placement")
    assert out["deleted"] is True and gone == [f"/v1/shops/29235685/products/{DRAFT['id']}.json"]
    [row] = json.loads(kept.read_text(encoding="utf-8"))
    assert row["by"] == "bot:director" and row["product"] == DRAFT and "zip back" in row["reason"]


@pytest.mark.parametrize("product, why", [
    ({**DRAFT, "external": {"id": "4412"}}, "published"),
    ({**DRAFT, "is_locked": True}, "publishing it right now"),
])
def test_a_published_or_busy_product_is_never_deleted(fake, product, why):
    state, gone, kept = fake
    state["product"] = product
    with pytest.raises(h.CommerceError, match=why):
        dl.product_delete_draft(DRAFT["id"], "director", "a test")
    assert gone == [] and not kept.exists()


def test_unnamed_bots_apps_and_missing_reasons_are_refused(fake):
    state, gone, _ = fake
    with pytest.raises(h.CommerceError, match="isn't a bot the owner lets"):
        dl.product_delete_draft(DRAFT["id"], "planner", "a test")
    with pytest.raises(h.CommerceError, match="say why"):
        dl.product_delete_draft(DRAFT["id"], "director", "")
    tok = clients.current_client.set("shopbot")
    try:
        with pytest.raises(h.CommerceError, match="not an app"):
            dl.product_delete_draft(DRAFT["id"], "director", "a test")
    finally:
        clients.current_client.reset(tok)
    assert gone == []
