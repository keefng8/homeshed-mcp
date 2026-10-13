"""One approval, not two (2026-10-09): a trusted app's own owner approval runs a publish / live update / Etsy edit at
once, through the same decide() path (limits and re-checks). Printify and notify are faked; files go under tmp_path."""
from datetime import datetime, timedelta, timezone

import pytest

import clients
import printify_pending as pp
import runtime_settings
from tools.commerce import _http as h
from tools.printify import _client as pf
from tools.printify import publish as pb

DRAFT = {"id": "abc123", "title": "Retro Frenchie Tee", "images": [1],
         "variants": [{"id": 1, "price": 2499, "is_enabled": True}]}


def _iso(minutes_ago: float = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def ok(i: str = "apr_1", by: str = "owner", minutes_ago: float = 1) -> dict:
    return {"id": i, "by": by, "at": _iso(minutes_ago)}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    for name in ("PENDING_FILE", "DONE_FILE", "HISTORY_FILE", "APPROVALS_FILE"):
        monkeypatch.setattr(pp, name, tmp_path / f"{name.lower()}.json")
    monkeypatch.delenv("PRINTIFY_PUBLISH_DAILY_MAX", raising=False)
    monkeypatch.setattr(h, "require", lambda key, label: None)
    state = {"product": dict(DRAFT), "trusted": "shop-app", "auto": "", "posts": [], "notes": [], "audit": []}
    monkeypatch.setattr(pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pf, "get", lambda path, params=None: state["product"])
    monkeypatch.setattr(pf, "publish_post", lambda path, body: state["posts"].append(path) or {})
    real_get = runtime_settings.get
    monkeypatch.setattr(runtime_settings, "get",
                        lambda key: state["trusted"] if key == pp.TRUST_SETTING else
                        state["auto"] if key == pp.AUTO_TRUST_SETTING else real_get(key))
    monkeypatch.setattr("tools.notify.send.send", lambda msg, **k: state["notes"].append((k.get("title"), msg)))
    monkeypatch.setattr(clients, "_audit", lambda action, client, **d: state["audit"].append((action, client, d)))
    token = clients.current_client.set("shop-app")
    yield state
    clients.current_client.reset(token)


def test_a_trusted_apps_approval_runs_at_once(fake):
    out = pb.product_publish("abc123", owner_approval=ok())
    assert out["pending"] is False and out["approved_in"] == "shop-app" and out["result"]["publishing"] is True
    assert fake["posts"] == ["/v1/shops/29235685/products/abc123/publish.json"] and pp.list_pending() == []
    assert len(pp.published_today()) == 1  # counted against the daily limit like any approval
    assert fake["notes"][0][0] == "Published by shop-app" and "apr_1" in fake["notes"][0][1]
    approved = [a for a in fake["audit"] if a[0] == "printify publish approved"]
    assert approved and approved[0][2]["via"] == "shop-app (apr_1, owner)"
    assert pp.history()[0]["note"].startswith("approved in shop-app (apr_1, owner)")


def test_without_an_approval_nothing_changes(fake):
    out = pb.product_publish("abc123")
    assert out["pending"] is True and "owner_approval" not in out and fake["posts"] == []


@pytest.mark.parametrize("trusted, client, approval, why", [
    ("", "shop-app", ok(), "aren't trusted"),
    ("other-app", "shop-app", ok(), "aren't trusted"),
    ("shop-app", None, ok(), "aren't trusted"),  # the owner's own sessions use the approvals bar
    ("shop-app", "shop-app", {"id": "x", "by": "owner", "at": _iso()}, "needs \"id\""),
    ("shop-app", "shop-app", {"id": "apr 1;", "by": "owner", "at": _iso()}, "needs \"id\""),
    ("shop-app", "shop-app", ok(by="auto:promoter"), "AUTO mode doesn't count"),
    ("shop-app", "shop-app", ok(by=""), "say who approved it"),
    ("shop-app", "shop-app", {"id": "apr_1", "by": "owner", "at": "yesterday"}, "ISO time"),
    ("shop-app", "shop-app", ok(minutes_ago=61), "last hour"),
    ("shop-app", "shop-app", ok(minutes_ago=-10), "last hour"),
    ("shop-app", "shop-app", "apr_1", "needs \"id\""),
])
def test_an_unusable_approval_queues_as_usual_and_says_why(fake, trusted, client, approval, why):
    fake["trusted"] = trusted
    clients.current_client.set(client)
    out = pb.product_publish("abc123", owner_approval=approval)
    assert out["pending"] is True and out["owner_approval"].startswith("not used:") and why in out["owner_approval"]
    assert fake["posts"] == [] and fake["notes"] == [] and len(pp.list_pending()) == 1


def test_an_approval_id_works_once(fake):
    pb.product_publish("abc123", owner_approval=ok("apr_7"))
    fake["product"] = {**DRAFT, "id": "def456"}
    again = pb.product_publish("def456", owner_approval=ok("apr_7"))
    assert again["pending"] is True and "already used" in again["owner_approval"] and len(fake["posts"]) == 1


def test_the_daily_limit_holds_and_the_approved_request_runs_once_theres_room(fake, monkeypatch):
    """The owner, 2026-10-09: "yes to auto retries": an app's approval held back only by a limit runs by itself later,
    through decide() (every check again); nothing else does."""
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "0")
    out = pb.product_publish("abc123", owner_approval=ok("apr_8"))
    assert out["pending"] is True and "daily limit" in out["owner_approval"] and "by itself" in out["owner_approval"]
    assert fake["posts"] == [] and len(pp.list_pending()) == 1
    assert fake["notes"] == []                                    # nothing for the owner to do: no phone note
    plain = pp.add("def456", "Publish the other one", {})       # no app approval: never run by the retry
    assert pp.retry_waiting() == [] and fake["posts"] == []      # still no room
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "20")
    ran = pp.retry_waiting()
    assert [r["id"] for r in ran] == [out["id"]] and fake["posts"] == ["/v1/shops/29235685/products/abc123/publish.json"]
    assert [i["id"] for i in pp.list_pending()] == [plain["id"]]   # the unapproved one still waits for the owner
    assert fake["notes"][-1][0] == "Published by shop-app"
    assert "run when there was room" in pp.history()[0]["note"]


def test_a_retry_that_fails_otherwise_waits_for_the_owner_and_isnt_retried(fake, monkeypatch):
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "0")
    out = pb.product_publish("abc123", owner_approval=ok("apr_9"))
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "20")
    fake["product"] = {**DRAFT, "external": {"id": "etsy-1"}}  # published elsewhere meanwhile: the re-check refuses
    assert pp.retry_waiting()[0]["id"] == out["id"] and fake["posts"] == []
    assert fake["notes"][-1][0] == "Waiting for you: shop-app" and pp.list_pending()[0]["last_error"]
    assert pp.retry_waiting() == []  # not tried again: it's the owner's now


def test_a_run_that_fails_stays_waiting(fake, monkeypatch):
    real_add = pp.add

    def add(*a, **k):  # published by hand between the tool's check and the run: the run's re-check refuses it
        fake["product"] = {**DRAFT, "external": {"id": "etsy-1"}}
        return real_add(*a, **k)
    monkeypatch.setattr(pp, "add", add)
    out = pb.product_publish("abc123", owner_approval=ok("apr_9"))
    assert out["pending"] is True and "already published" in out["owner_approval"]
    [still] = pp.list_pending()
    assert still["last_error"].startswith("that product is already published") and fake["posts"] == []


def test_the_etsy_and_live_update_tools_pass_it_on(fake, monkeypatch):
    import inspect

    from tools.etsy import write as ew
    from tools.printify import update_live as ul
    seen = []
    monkeypatch.setattr(pp, "add", lambda *a, **k: seen.append(k.get("owner_approval")) or {"pending": True})
    ew._queue("123", "Tee", "Set Etsy details", {"fields": {}, "properties": []}, {}, False, owner_approval=ok("apr_e"))
    assert seen[0]["id"] == "apr_e"
    for fn in (ew.listing_update, ul.product_update_live, pb.product_publish):
        assert inspect.signature(fn).parameters["owner_approval"].default is None


def test_auto_counts_only_for_an_app_named_for_auto(fake):
    fake["auto"] = "shop-app"
    out = pb.product_publish("abc123", owner_approval=ok("apr_a1", by="auto:full-auto"))
    assert out["pending"] is False and fake["notes"][0][0] == "Published by shop-app (AUTO)"
    fake["trusted"], fake["product"] = "", {**DRAFT, "id": "def456"}  # named for auto only: owner taps count too
    assert pb.product_publish("def456", owner_approval=ok("apr_a2"))["pending"] is False
    fake["auto"], fake["trusted"], fake["product"] = "", "shop-app", {**DRAFT, "id": "aaa111"}
    out = pb.product_publish("aaa111", owner_approval=ok("apr_a3", by="AUTO"))  # trusted for taps, not for AUTO
    assert out["pending"] is True and "AUTO mode doesn't count" in out["owner_approval"]


def test_the_phone_hears_only_publishes_and_stuck_requests(fake, monkeypatch):
    from tools.etsy import write as ew
    monkeypatch.setattr(ew, "execute", lambda payload: {"note": "set"})
    fake["auto"] = "shop-app"
    out = ew._queue("123", "Tee", "Set Etsy details", {"fields": {}, "properties": []}, {}, False,
                    owner_approval=ok("apr_q1", by="auto:full-auto"))
    assert out["pending"] is False and fake["notes"] == []  # an Etsy edit that ran: logged, no phone note
    monkeypatch.setenv("PRINTIFY_PUBLISH_DAILY_MAX", "0")
    out = pb.product_publish("abc123", owner_approval=ok("apr_q2", by="auto:full-auto"))
    assert out["pending"] is True and fake["notes"] == []  # held by the limit: it runs later, nothing to tell
