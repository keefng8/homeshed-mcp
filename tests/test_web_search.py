"""web.search: SearXNG's JSON cut down to links and short snippets; bad input and a missing or failing engine are
clear errors that never name the engine's address. Case list drafted by the local model (qwen3.6-35b), checked."""
import httpx
import pytest

from tools.web import search as ws

ENGINE = "http://searxng-test:8080"


def answer(results, suggestions=None, status=200):
    def get(url, params=None, **kw):
        get.calls.append((url, params))
        return httpx.Response(status, json={"results": results, "suggestions": suggestions or []},
                              request=httpx.Request("GET", url))
    get.calls = []
    return get


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", ENGINE + "/")

    def use(get):
        monkeypatch.setattr(ws.httpx, "get", get)
        return get
    return use


def test_results_are_links_and_short_snippets_without_duplicates(engine):
    get = engine(answer([
        {"title": "A  page", "url": "https://a.example/x", "content": "word " * 200, "engine": "duckduckgo"},
        {"title": "Same page again", "url": "https://a.example/x", "content": "dup", "engine": "bing"},
        {"title": "Not a web link", "url": "ftp://b.example/file", "content": "no"},
        {"title": "B", "url": "http://b.example/", "content": "b", "engine": "brave"},
    ], suggestions=["a page tips"]))
    out = ws.search("  a   page ", max_results=5, time_range="week")
    assert get.calls == [(ENGINE + "/search", {"q": "a page", "format": "json", "language": "en", "time_range": "week"})]
    assert [r["url"] for r in out["results"]] == ["https://a.example/x", "http://b.example/"]
    assert out["results"][0]["title"] == "A page" and len(out["results"][0]["snippet"]) <= ws.SNIPPET_CHARS
    assert out["query"] == "a page" and out["suggestions"] == ["a page tips"]


def test_max_results_caps_the_list(engine):
    engine(answer([{"title": str(i), "url": f"https://e.example/{i}", "content": ""} for i in range(20)]))
    assert len(ws.search("x", max_results=3)["results"]) == 3


@pytest.mark.parametrize("kwargs, words", [
    ({"query": ""}, "non-empty"), ({"query": "x" * 301}, "at most"),
    ({"query": "x", "max_results": 0}, "1-10"), ({"query": "x", "max_results": 11}, "1-10"),
    ({"query": "x", "time_range": "decade"}, "time_range"), ({"query": "x", "language": "english!"}, "language"),
])
def test_bad_input_is_refused_before_any_call(engine, kwargs, words):
    get = engine(answer([]))
    with pytest.raises(ws.WebSearchError, match=words):
        ws.search(**kwargs)
    assert get.calls == []


@pytest.mark.parametrize("lang", ["fr", "en-GB", "all"])
def test_language_codes_are_accepted(engine, lang):
    get = engine(answer([]))
    ws.search("x", language=lang)
    assert get.calls[0][1]["language"] == lang


def test_not_set_up_says_so(monkeypatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    with pytest.raises(ws.WebSearchError, match="isn't set up"):
        ws.search("x")


@pytest.mark.parametrize("failure, words", [
    (httpx.ConnectTimeout("slow"), "timed out"), (httpx.ConnectError("refused"), "unavailable"),
])
def test_an_unreachable_engine_never_names_its_address(engine, failure, words):
    def get(url, **kw):
        raise failure
    engine(get)
    with pytest.raises(ws.WebSearchError, match=words) as exc:
        ws.search("x")
    assert "searxng-test" not in str(exc.value)


def test_errors_and_unreadable_answers(engine):
    engine(answer([], status=502))
    with pytest.raises(ws.WebSearchError, match="HTTP 502"):
        ws.search("x")

    def bad(url, **kw):
        return httpx.Response(200, content=b"<html>", request=httpx.Request("GET", url))
    engine(bad)
    with pytest.raises(ws.WebSearchError, match="wasn't readable"):
        ws.search("x")
