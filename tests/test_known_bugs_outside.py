"""bugs.find's opt-in outside lookup: off by default, only a cleaned signature leaves the machine, only links come back,
and nothing about GitHub (errors, timeouts, rate limits) can break a lookup. Test list drafted by the local model
(qwen3-coder-30b); the strong-local-match case added on review."""
import pytest

import tools.bugs.known as kb


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


ISSUES = {"items": [{"title": f"Issue {n}", "html_url": f"https://github.com/o/r/issues/{n}", "state": "open",
                     "repository_url": "https://api.github.com/repos/o/r"} for n in range(5)]}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import runtime_settings
    import vault
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    monkeypatch.setattr(kb, "_outside_cache", {})
    monkeypatch.setattr(kb, "_shedkeeper_pick", lambda *a: None)
    monkeypatch.setattr(vault, "secret", lambda name, default=None: None)
    monkeypatch.setattr(runtime_settings, "get", lambda key: True if key == "bugs_search_public" else None)


def _capture(monkeypatch, response):
    import httpx
    sent = []

    def fake_get(url, headers=None, timeout=None, params=None):
        sent.append({"url": url, "headers": headers, "params": params})
        if isinstance(response, Exception):
            raise response
        return response
    monkeypatch.setattr(httpx, "get", fake_get)
    return sent


def test_off_by_default_nothing_is_sent(monkeypatch):
    import runtime_settings
    monkeypatch.setattr(runtime_settings, "get", lambda key: False)
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    assert "outside" not in kb.find("docker compose fails with permission denied on volume")
    assert sent == []


def test_only_a_cleaned_signature_is_sent(monkeypatch):
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    kb.find(r"open C:\Users\alex\x.cfg failed at 192.168.1.10:8765 id 0a1b2c3d4e5f token hlc_"
            + "A1b2C3d4E5f6G7h8I9j0K1" + ' value "secret thing" after 42 retries')
    q = sent[0]["params"]["q"]
    for private in ("alex", "192.168", "8765", "0a1b2c3d", "hlc_", "secret thing", "42"):
        assert private not in q, (private, q)
    assert "failed" in q and "retries" in q and q.endswith("is:issue")


def test_at_most_three_links_come_back(monkeypatch):
    _capture(monkeypatch, _Resp(200, ISSUES))
    out = kb.find("docker compose fails with permission denied on volume")["outside"]
    assert len(out) == 3 and out[0] == {"title": "Issue 0", "url": "https://github.com/o/r/issues/0", "state": "open",
                                        "repo": "o/r"}


def test_a_strong_local_match_never_searches_outside(monkeypatch):
    kb.report("Docker volume permission denied", error_text="permission denied on volume /data/x")
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    out = kb.find("permission denied on volume /data/other")
    assert out["matches"][0]["score"] == 100 and "outside" not in out and sent == []


def test_results_are_cached_for_ten_minutes(monkeypatch):
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    kb.find("docker compose fails with permission denied on volume")
    kb.find("docker compose fails with permission denied on volume")
    assert len(sent) == 1
    monkeypatch.setattr(kb.time, "time", lambda: 10 ** 10)          # far past the 10 minutes
    kb.find("docker compose fails with permission denied on volume")
    assert len(sent) == 2


@pytest.mark.parametrize("response", [_Resp(403, {"message": "API rate limit exceeded"}), _Resp(500, {}),
                                      _Resp(200, "not json-like")])
def test_rate_limits_and_bad_answers_give_no_links_and_no_error(monkeypatch, response):
    _capture(monkeypatch, response)
    assert "outside" not in kb.find("docker compose fails with permission denied on volume")


@pytest.mark.parametrize("error_name", ["ConnectError", "ReadTimeout"])
def test_network_errors_and_timeouts_never_break_a_lookup(monkeypatch, error_name):
    import httpx
    _capture(monkeypatch, getattr(httpx, error_name)("down"))
    assert "outside" not in kb.find("docker compose fails with permission denied on volume")


def test_a_token_is_sent_only_when_configured(monkeypatch):
    import vault
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    kb.find("docker compose fails with permission denied on volume")
    assert "Authorization" not in sent[0]["headers"]
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "gh-test" if name == "GITHUB_SEARCH_TOKEN" else None)
    monkeypatch.setattr(kb, "_outside_cache", {})
    kb.find("docker compose fails with permission denied on volume")
    assert sent[1]["headers"]["Authorization"] == "Bearer gh-test"


def test_too_little_to_search_on_sends_nothing(monkeypatch):
    sent = _capture(monkeypatch, _Resp(200, ISSUES))
    kb.find("error 42")
    assert sent == []
