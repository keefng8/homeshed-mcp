"""The owner's decisions on apps' shop requests are logged (approved, declined, expired), passed to a private hook if
there is one, and commerce.requests.list shows an app its own (the owner sees all). Files go under tmp_path. Test list
drafted by the local model (2026-10-08)."""
import sys
import threading
import types

import pytest

import clients
import printify_pending as pp
from tools.commerce import _http as h
from tools.commerce.waiting import requests_list


@pytest.fixture
def queue(monkeypatch, tmp_path):
    for name in ("PENDING_FILE", "DONE_FILE", "HISTORY_FILE"):
        monkeypatch.setattr(pp, name, tmp_path / f"{name}.json")
    monkeypatch.setattr("tools.printify.publish.station_pause", lambda: None)
    ran = []
    monkeypatch.setattr("tools.printify.publish.execute", lambda pid: ran.append(pid) or {"note": "publishing"})
    return ran


def ask(client, pid, action="publish"):
    tok = clients.current_client.set(client)
    try:
        return pp.add(pid, f"{action} {pid}", {}, action=action)["id"]
    finally:
        clients.current_client.reset(tok)


def as_client(client, fn, *args, **kw):
    tok = clients.current_client.set(client)
    try:
        return fn(*args, **kw)
    finally:
        clients.current_client.reset(tok)


def test_decisions_are_logged_and_an_app_sees_only_its_own(queue):
    a, b, c = ask("shopbot", "aa11"), ask("shopbot", "bb22"), ask("otherapp", "cc33")
    pp.decide(a, True)
    pp.decide(b, False)
    mine = as_client("shopbot", requests_list)
    assert mine["waiting"] == []
    assert [(r["id"], r["outcome"]) for r in mine["decided"]] == [(b, "declined"), (a, "approved")]
    assert mine["decided"][1]["note"] == "publishing" and "client" not in mine["decided"][0]
    owner = requests_list()  # the owner: everyone's, with who asked
    assert [w["id"] for w in owner["waiting"]] == [c] and owner["waiting"][0]["client"] == "otherapp"
    assert len(owner["decided"]) == 2


def test_an_unanswered_request_is_logged_once_as_expired(queue, monkeypatch):
    rid = ask("shopbot", "aa11")
    later = pp.time.time() + pp.TTL_S + 60
    monkeypatch.setattr(pp.time, "time", lambda: later)
    assert pp.list_pending() == [] and pp.list_pending() == []
    [row] = pp.history()
    assert row["id"] == rid and row["outcome"] == "expired"


def test_a_failed_approval_stays_waiting_and_the_hook_hears_of_it(queue, monkeypatch):
    heard, done = [], threading.Event()
    hook = types.SimpleNamespace(decided=lambda row: (heard.append(row), done.set()))
    monkeypatch.setitem(sys.modules, "private_decision_hooks", hook)

    def boom(pid):
        raise h.CommerceError("Printify is busy")
    monkeypatch.setattr("tools.printify.publish.execute", boom)
    rid = ask("shopbot", "aa11")
    with pytest.raises(h.CommerceError):
        pp.decide(rid, True)
    assert done.wait(5) and heard[0]["outcome"] == "failed" and "Printify is busy" in heard[0]["note"]
    [w] = as_client("shopbot", requests_list)["waiting"]
    assert w["last_error"] == "Printify is busy" and pp.history() == []


def test_a_broken_hook_never_fails_a_decision(queue, monkeypatch):
    called = threading.Event()

    def broken(row):
        called.set()
        raise RuntimeError("the app is down")
    monkeypatch.setitem(sys.modules, "private_decision_hooks", types.SimpleNamespace(decided=broken))
    rid = ask("shopbot", "aa11")
    assert pp.decide(rid, True)["approved"] is True and called.wait(5)
    assert pp.history()[0]["outcome"] == "approved"


def test_history_keeps_a_week_and_etsy_requests_name_their_listing(queue, monkeypatch):
    rid = ask("shopbot", "etsy-4412", action="etsy")
    pp.decide(rid, False)
    [row] = as_client("shopbot", requests_list)["decided"]
    assert row["listing_id"] == "4412" and "product_id" not in row
    later = pp.time.time() + 8 * 86400
    monkeypatch.setattr(pp.time, "time", lambda: later)
    assert pp.history() == []
