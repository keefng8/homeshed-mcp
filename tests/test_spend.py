"""spend.py + spend.today + the per-app cap (2026-10-10, the shop app's bots): paid images and model calls are counted
per app per day; an app at its owner-set cap gets SPEND_CAP_REACHED on paid calls while free ones keep working; the
owner is never capped; a refused image is refunded. Providers are faked (the LLM and image test fakes); files go under
tmp_path."""
import pytest

import clients
import spend
from test_image_providers import KEYS, SyncThread, _key, api  # noqa: F401 - fixtures
from test_llm_relay import ask, fake, on  # noqa: F401 - fixtures
from tools.image import providers as iprov
from tools.spend import today as spend_tool

REAL_CAP_OF = spend.cap_of


@pytest.fixture(autouse=True)
def ledger(monkeypatch, tmp_path):
    monkeypatch.setattr(spend, "FILE", tmp_path / "spend.json")
    monkeypatch.setattr(clients, "CLIENTS_FILE", tmp_path / "clients.json")
    monkeypatch.setattr(clients, "AUDIT_FILE", tmp_path / "client_audit.jsonl")
    caps = {}
    monkeypatch.setattr(spend, "cap_of", lambda c: None if not c or c == "owner" else caps.get(c))
    return caps


def as_client(name):
    return clients.current_client.set(name)


def test_counting_capping_and_refunds(ledger):
    spend.check("bot-a", "llm")  # no cap: never refused
    ledger["bot-a"] = 1.0
    spend.add("bot-a", "llm", 0.6)
    spend.add("bot-a", "image", 0.3)
    spend.check("bot-a", "llm")  # 0.90 of 1.00: still allowed
    with pytest.raises(spend.SpendCapReached, match="SPEND_CAP_REACHED: bot-a has spent \\$0.90 of its \\$1.00"):
        spend.check("bot-a", "image", 0.2)  # would take it over
    spend.add("bot-a", "image", -0.5)  # a refund never goes below zero
    assert spend.spent("bot-a") == {"image": 0.0, "llm": 0.6, "total": 0.6}
    spend.add("owner", "llm", 50)
    spend.check("owner", "llm", 99)  # the owner is counted, never capped
    rows = {r["client"]: r for r in spend.today_all()}
    assert rows["bot-a"]["cap_usd"] == 1.0 and rows["bot-a"]["left_usd"] == 0.4 and rows["bot-a"]["capped"] is False
    assert rows["owner"]["total"] == 50 and rows["owner"]["cap_usd"] is None


def test_a_bot_at_its_cap_gets_its_model_calls_refused(fake, ledger):
    on()
    ledger["bot-a"] = 0.0001
    tok = as_client("bot-a")
    try:
        ask()  # under the cap: answered, and its cost (xAI's own: 1,250,000 ticks = $0.000125) counted
        assert spend.spent("bot-a")["llm"] == pytest.approx(0.000125)
        with pytest.raises(Exception, match="SPEND_CAP_REACHED"):
            ask()
        assert len(fake.calls) == 1  # the refused call never reached xAI
    finally:
        clients.current_client.reset(tok)
    ask()  # the owner, same moment: never capped
    assert len(fake.calls) == 2


def test_a_bot_at_its_cap_gets_paid_images_refused_and_failed_ones_refunded(api, monkeypatch, ledger):
    _key(monkeypatch, "REPLICATE_API_TOKEN")
    ledger["bot-b"] = 0.005
    tok = as_client("bot-b")
    try:
        job = iprov.start_remote("replicate", "black-forest-labs/flux-schnell", "a cat", 512, 512, 1, 4)
        assert job["status"] == "done" and job["caller"] == "bot-b" and job["cost_counted_usd"] == pytest.approx(0.003)
        assert spend.spent("bot-b")["image"] == pytest.approx(0.003)
        with pytest.raises(iprov.ProviderError, match="SPEND_CAP_REACHED"):
            iprov.start_remote("replicate", "black-forest-labs/flux-schnell", "a dog", 512, 512, 1, 4)
    finally:
        clients.current_client.reset(tok)
    # a provider that made nothing (HTTP 429): the app's charge is refunded with the owner's count
    ledger["bot-c"] = 1.0
    monkeypatch.setattr(iprov, "ADAPTERS", {**iprov.ADAPTERS,
                                            "replicate": lambda *a, **k: (_ for _ in ()).throw(
                                                iprov.ProviderError("replicate returned http 429: out of quota"))})
    tok = as_client("bot-c")
    try:
        job = iprov.start_remote("replicate", "black-forest-labs/flux-schnell", "a fox", 512, 512, 1, 4)
    finally:
        clients.current_client.reset(tok)
    assert job["status"] == "failed" and spend.spent("bot-c")["image"] == 0.0 and job["cost_counted_usd"] == 0.0
    # any 4xx made nothing: a project spend cap answers 403 in 0.1 s (2026-10-11, Nano) and must cost $0 too
    monkeypatch.setattr(iprov, "ADAPTERS", {**iprov.ADAPTERS,
                                            "replicate": lambda *a, **k: (_ for _ in ()).throw(
                                                iprov.ProviderError("replicate returned http 403 (key lacks access)"))})
    tok = as_client("bot-c")
    try:
        job = iprov.start_remote("replicate", "black-forest-labs/flux-schnell", "an owl", 512, 512, 1, 4)
    finally:
        clients.current_client.reset(tok)
    assert job["status"] == "failed" and job["cost_counted_usd"] == 0.0 and spend.spent("bot-c")["image"] == 0.0


def test_the_owner_sets_the_cap_and_the_tool_reads_it(monkeypatch, ledger):
    monkeypatch.setattr(spend, "cap_of", REAL_CAP_OF)  # the real lookup, reading the (temporary) clients file
    clients.create("bot-d", preset="read")
    clients.set_spend_cap("bot-d", 2.5)
    for bad in (-1, 10_001, "two", True):
        with pytest.raises(clients.ClientError, match="between 0 and 10000"):
            clients.set_spend_cap("bot-d", bad)
    row = next(c for c in clients.list_clients()["clients"] if c["name"] == "bot-d")
    assert row["daily_spend_usd"] == 2.5 and row["spend_today"] == {"image": 0.0, "llm": 0.0, "total": 0.0}
    out = spend_tool.today("bot-d")
    assert out["currency"] == "USD" and out["clients"][0]["cap_usd"] == 2.5 and out["clients"][0]["capped"] is False
    clients.set_spend_cap("bot-d", None)
    assert spend.cap_of("bot-d") is None


def test_owner_sessions_set_delegated_caps_only(monkeypatch, ledger):
    from tools.spend import cap_set as cs
    monkeypatch.setattr(spend, "cap_of", REAL_CAP_OF)
    clients.create("seat-a", preset="read")
    clients.create("design", preset="read")
    clients.set_spend_cap("design", 3.0)
    with pytest.raises(cs.SpendCapError, match="isn't open"):
        cs.cap_set("design", 1)  # not delegated: the owner's cap stands
    assert spend.cap_of("design") == 3.0
    clients.set_cap_delegated("seat-a", True)
    out = cs.cap_set("seat-a", 0.5)
    assert out["cap_usd"] == 0.5 and out["left_usd"] == 0.5
    for bad in (5.01, -1, True, "1"):
        with pytest.raises(cs.SpendCapError, match="0 to 5"):
            cs.cap_set("seat-a", bad)
    tok = as_client("seat-a")
    try:
        with pytest.raises(cs.SpendCapError, match="only the owner"):
            cs.cap_set("seat-a", 5)  # an app can't raise its own cap
    finally:
        clients.current_client.reset(tok)
    assert next(c for c in clients.list_clients()["clients"] if c["name"] == "seat-a")["cap_delegated"] is True
