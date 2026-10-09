"""apis.list / apis.find. Case list drafted by local_ai.ask (answered by Nemotron-Super via the
delegator's NVIDIA fallback), adapted: out-of-range k is an error, not clamped."""
import json

import pytest

import tools.apis.catalog as cat

ENTRIES = [
    {"name": "Jina Reader", "category": "web", "provider": "r.jina.ai", "cost": "free", "local": False,
     "status": "live", "auth": "none", "how_to_use": "web.read", "notes": "webpage to markdown"},
    {"name": "Voicebox", "category": "voice", "provider": "local app", "cost": "free", "local": True,
     "status": "candidate", "auth": "none", "how_to_use": "voicebox.speak", "notes": "text to speech and speech to text"},
    {"name": "NVIDIA Nemotron", "category": "llm", "provider": "gateway", "cost": "free-tier", "local": False,
     "status": "live", "auth": "bearer via env LOCAL_AI_GATEWAY_KEY", "how_to_use": "local_ai.ask", "notes": "cloud llm"},
    {"name": "Paid Search", "category": "search", "provider": "x", "cost": "paid", "local": False,
     "status": "reference", "auth": "bearer via env X_KEY", "how_to_use": "none", "notes": "web search api"},
]


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    f = tmp_path / "api_catalog.json"
    f.write_text(json.dumps({"apis": ENTRIES}))
    monkeypatch.setattr(cat, "CATALOG_FILE", f)
    return f


def test_list_all():
    assert cat.list_apis()["count"] == 4


def test_a_private_catalog_replaces_by_name_and_adds(catalog):
    """api_catalog.private.json (an install's own services; the public copy has none), release v1 2026-09-29."""
    mine = [{**ENTRIES[0], "notes": "my own reader"}, {**ENTRIES[1], "name": "My GPU service", "status": "live"}]
    catalog.with_name("api_catalog.private.json").write_text(json.dumps({"apis": mine}))
    apis = {a["name"]: a for a in cat.list_apis()["apis"]}
    assert len(apis) == 5 and apis["Jina Reader"]["notes"] == "my own reader" and "My GPU service" in apis
    catalog.with_name("api_catalog.private.json").write_text(json.dumps({"apis": [{"name": "x", "notes": "sk-" + "a" * 20}]}))
    with pytest.raises(cat.CatalogError, match="secret"):
        cat.list_apis()


def test_list_filters_combine():
    assert [a["name"] for a in cat.list_apis(category="llm")["apis"]] == ["NVIDIA Nemotron"]
    assert [a["name"] for a in cat.list_apis(cost="free", local=True)["apis"]] == ["Voicebox"]
    assert [a["name"] for a in cat.list_apis(status="live", local=False)["apis"]] == ["Jina Reader", "NVIDIA Nemotron"]
    assert cat.list_apis(cost="paid")["count"] == 1


def test_unknown_category_is_empty_not_error():
    assert cat.list_apis(category="unknown-thing") == {"count": 0, "apis": []}


def test_find_ranks_by_overlap_and_name():
    res = cat.find_apis("text to speech")["results"]
    assert res[0]["name"] == "Voicebox" and res[0]["score"] > 0
    assert cat.find_apis("jina reader")["results"][0]["name"] == "Jina Reader"


def test_exact_name_match_ranks_first():
    assert cat.find_apis("Paid Search", k=4)["results"][0]["name"] == "Paid Search"


def test_find_omits_zero_scores_and_respects_k():
    assert cat.find_apis("zzzz qqqq")["results"] == []
    assert len(cat.find_apis("free web", k=1)["results"]) == 1


@pytest.mark.parametrize("q,k", [("", 5), ("   ", 5), ("x", 0), ("x", 26)])
def test_bad_arguments_rejected(q, k):
    with pytest.raises(cat.CatalogError):
        cat.find_apis(q, k)


def test_malformed_catalog_raises_clear_error(catalog):
    catalog.write_text("{not json")
    with pytest.raises(cat.CatalogError, match="unreadable"):
        cat.list_apis()


def test_catalog_containing_a_secret_is_refused(catalog):
    leaked = dict(ENTRIES[0], notes="key sk-abcdefghijklmnop1234")  # secret-scan: allow (a fake key for this test)
    catalog.write_text(json.dumps({"apis": [leaked]}))
    with pytest.raises(cat.CatalogError, match="secret"):
        cat.list_apis()


def test_only_known_fields_served(catalog):
    catalog.write_text(json.dumps({"apis": [dict(ENTRIES[0], internal_only="x")]}))
    assert "internal_only" not in cat.list_apis()["apis"][0]


def test_real_catalog_is_valid_and_secret_free(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(cat, "CATALOG_FILE", Path(__file__).resolve().parents[1] / "api_catalog.json")
    out = cat.list_apis()
    assert out["count"] >= 15 and all(a["name"] and a["status"] for a in out["apis"])


def test_prefix_and_tags_find_the_right_api(catalog):
    # Live bug 2026-09-28: "text to speech voice" ranked NVIDIA first and missed Voicebox.
    entries = [dict(e) for e in ENTRIES]
    entries[1]["tags"] = ["text to speech", "tts"]
    catalog.write_text(json.dumps({"apis": entries}))
    assert cat.find_apis("text to speech voice")["results"][0]["name"] == "Voicebox"
    assert cat.find_apis("voice")["results"][0]["name"] == "Voicebox"  # prefix of "voicebox"


def test_real_catalog_finds_the_right_entries(monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(cat, "CATALOG_FILE", Path(__file__).resolve().parents[1] / "api_catalog.json")
    assert cat.find_apis("exchange rates")["results"][0]["name"] == "Frankfurter (exchange rates)"
    assert cat.find_apis("send a push to my phone")["results"][0]["name"] == "Push notifications (ntfy)"
