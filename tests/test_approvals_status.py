"""approvals.status and GET /approvals/<id> (front door, 2026-10-27): one request's status across the memory, Proxmox,
commerce and inbox queues, waiting or decided; an app sees only its own; an unreadable file is skipped. Every file
goes under tmp_path."""
import asyncio
import importlib
import json
import sys

import pytest

import approvals
import clients
import memory_pending as mp
import owner_inbox
import printify_pending as prp
import proxmox_pending as pxp
from tools.approvals.status import status as status_tool


@pytest.fixture(autouse=True)
def files(monkeypatch, tmp_path):
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(mp, "PENDING_FILE", tmp_path / "memory_pending.json")
    monkeypatch.setattr(pxp, "PENDING_FILE", tmp_path / "proxmox_pending.json")
    monkeypatch.setattr(owner_inbox, "INBOX_FILE", tmp_path / "inbox.json")
    for name in ("PENDING_FILE", "DONE_FILE", "HISTORY_FILE"):
        monkeypatch.setattr(prp, name, tmp_path / f"printify_{name}.json")
    monkeypatch.setattr("tools.printify.publish.station_pause", lambda: None)
    monkeypatch.setattr("tools.printify.publish.execute", lambda pid: {"note": "publishing"})
    monkeypatch.setattr("tools.proxmox._client.execute", lambda action: {"upid": "UPID:x"})
    return tmp_path


def as_client(client, fn, *args, **kw):
    tok = clients.current_client.set(client)
    try:
        return fn(*args, **kw)
    finally:
        clients.current_client.reset(tok)


POWER = {"op": "power", "node": "pve", "kind": "qemu", "vmid": 101, "action": "start"}


def test_memory_waiting_then_declined():
    rid = mp.add("shopbot", "s1", "user", "remember the blue theme")
    got = approvals.status(rid, "shopbot")
    assert got == {"id": rid, "queue": "memory", "status": "waiting", "summary": "remember the blue theme",
                   "asked_at": got["asked_at"]}
    mp.decide(rid, False)
    got = approvals.status(rid, "shopbot")
    assert got["status"] == "declined" and got["decided_by"] == "owner" and got["queue"] == "memory"
    assert got["decided_at"] >= got["asked_at"]


def test_proxmox_waiting_approved_and_expired(monkeypatch):
    rid = as_client("opsbot", pxp.add, POWER, "start qemu 101 on pve", {})["id"]
    got = approvals.status(rid, "opsbot")
    assert (got["queue"], got["kind"], got["status"], got["summary"]) == ("proxmox", "power", "waiting",
                                                                        "start qemu 101 on pve")
    pxp.decide(rid, True)
    got = approvals.status(rid)
    assert got["status"] == "approved" and got["decided_by"] == "owner" and got["summary"] == "start qemu 101 on pve"
    old = as_client(None, pxp.add, POWER, "start qemu 101 on pve", {})["id"]  # the owner's own session
    later = pxp.time.time() + pxp.TTL_S + 60
    monkeypatch.setattr(approvals.time, "time", lambda: later)
    monkeypatch.setattr(pxp.time, "time", lambda: later)
    got = approvals.status(old)
    assert got["status"] == "expired" and "decided_by" not in got


def test_commerce_waiting_and_declined():
    rid = as_client("shopbot", prp.add, "aa11", "publish aa11", {})["id"]
    got = approvals.status(rid, "shopbot")
    assert (got["queue"], got["kind"], got["status"]) == ("commerce", "publish", "waiting")
    prp.decide(rid, False)
    got = approvals.status(rid, "shopbot")
    assert got["status"] == "declined" and got["decided_by"] == "owner" and got["summary"] == "publish aa11"


def test_commerce_decided_by_comes_from_the_note(files):
    now = prp.time.time()
    notes = {"a1a1a1a1": ("approved", "publishing"),  # the Control Panel
             "b2b2b2b2": ("approved", "approved in shopbot (ap-1, bot:checker). done"),
             "c3c3c3c3": ("approved", "approved in shopbot (ap-2, auto:full; run when there was room). done"),
             "d4d4d4d4": ("approved", "approved in shopbot (ap-3, the owner). done"),
             "e5e5e5e5": ("withdrawn", "not needed")}
    rows = [{"id": k, "kind": "etsy", "client": "shopbot", "summary": k, "asked_at": now - 9, "decided_at": now,
             "outcome": o, "note": n} for k, (o, n) in notes.items()]
    prp.HISTORY_FILE.write_text(json.dumps({"decisions": rows}), encoding="utf-8")
    by = {k: approvals.status(k, "shopbot").get("decided_by") for k in notes}
    assert by == {"a1a1a1a1": "owner", "b2b2b2b2": "bot:checker", "c3c3c3c3": "auto", "d4d4d4d4": "owner",
                  "e5e5e5e5": None}
    assert approvals.status("e5e5e5e5")["status"] == "withdrawn"


def test_inbox_waiting_then_done():
    rid = owner_inbox.send("builder", "may I restart the panel?", need="decision", client="shopbot")["id"]
    got = approvals.status(rid, "shopbot")
    assert (got["queue"], got["kind"], got["status"]) == ("inbox", "decision", "waiting")
    owner_inbox.reply(rid, "yes", done=True)
    got = approvals.status(rid, "shopbot")
    assert got["status"] == "approved" and got["decided_by"] == "owner" and "decided_at" in got


def test_unknown_id_and_bad_ids():
    assert approvals.status("0badf00d") == {"id": "0badf00d", "status": "unknown"}
    assert approvals.status("../etc/passwd", "shopbot")["status"] == "unknown"
    assert approvals.status("", None) == {"id": "", "status": "unknown"}


def test_an_app_sees_only_its_own_requests():
    mine = mp.add("shopbot", "s1", "user", "a fact")
    theirs = mp.add("otherapp", "s2", "user", "another fact")
    owners = as_client(None, prp.add, "aa11", "publish aa11", {})["id"]
    note = owner_inbox.send("me", "hello", client="otherapp")["id"]
    assert as_client("shopbot", status_tool, mine)["status"] == "waiting"
    for rid in (theirs, owners, note):
        assert as_client("shopbot", status_tool, rid) == {"id": rid, "status": "unknown"}
    mp.decide(theirs, False)  # decided ones too
    assert as_client("shopbot", status_tool, theirs)["status"] == "unknown"
    assert status_tool(theirs)["status"] == "declined"  # the owner sees everyone's


def test_an_unreadable_file_is_skipped(files):
    rid = owner_inbox.send("me", "hello", client="shopbot")["id"]
    for f in (mp.PENDING_FILE, pxp.PENDING_FILE, prp.PENDING_FILE, prp.HISTORY_FILE):
        f.write_text("{not json", encoding="utf-8")
    clients.AUDIT_FILE.write_text("{not json\n", encoding="utf-8")
    assert approvals.status(rid, "shopbot")["status"] == "waiting"
    assert approvals.status("0badf00d") == {"id": "0badf00d", "status": "unknown"}
    owner_inbox.INBOX_FILE.write_text("{not json", encoding="utf-8")
    assert approvals.status(rid, "shopbot")["status"] == "unknown"


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    sys.modules.pop("server", None)
    return importlib.import_module("server")


class Req:
    def __init__(self, item_id):
        self.path_params = {"item_id": item_id}


def test_the_owner_route_answers_and_404s_unknown(server):
    rid = owner_inbox.send("me", "hello", client="shopbot")["id"]
    resp = asyncio.run(server.approvals_status(Req(rid)))
    assert resp.status_code == 200 and json.loads(resp.body)["queue"] == "inbox"
    resp = asyncio.run(server.approvals_status(Req("0badf00d")))
    assert resp.status_code == 404 and json.loads(resp.body)["status"] == "unknown"
