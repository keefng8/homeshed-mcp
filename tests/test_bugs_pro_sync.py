"""bugs.sync: the HomeShed Pro known-bugs feed (tools/bugs/pro_sync.py), public with Pro since 2026-10-01. Moved from
test_known_bugs_feed.py (release v1, 2026-09-29)."""
import json

import pytest

import tools.bugs.known as kb
import tools.bugs.pro_sync as pro_sync


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    return tmp_path / "known_bugs.json"


class _Resp:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


@pytest.mark.parametrize("status, says", [(401, "refused this install's key"), (403, "refused this install's key"),
                                          (404, "no known-bugs feed"), (500, "HTTP 500")])
def test_sync_explains_every_failure(monkeypatch, status, says):
    import httpx
    import runtime_settings
    import vault
    monkeypatch.setattr(runtime_settings, "address", lambda name: "https://pro.example")
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "mav_test")  # secret-scan: allow (fake)
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=None: _Resp(status))
    with pytest.raises(kb.BugsError, match=says):
        pro_sync.sync()


def test_sync_merges_the_feed_from_mavis_pro(monkeypatch):
    import httpx
    import runtime_settings
    import vault
    seen = {}
    content = json.dumps({"kind": "known-bugs", "bugs": [{"origin_id": "KB-0015", "title": "Live secrets in transcripts"}]})
    monkeypatch.setattr(runtime_settings, "address", lambda name: "https://pro.example")
    monkeypatch.setattr(vault, "secret", lambda name, default=None: "mav_test")  # secret-scan: allow (fake)

    def fake_get(url, headers=None, timeout=None):
        seen.update(url=url, auth=headers["Authorization"])
        return _Resp(200, {"data": {"content": content}})
    monkeypatch.setattr(httpx, "get", fake_get)
    assert pro_sync.sync()["added"] == 1
    assert seen == {"url": "https://pro.example/api/pro-api/packs/known-bugs", "auth": "Bearer mav_test"}  # secret-scan: allow


def test_sync_without_pro_says_how_to_connect(monkeypatch):
    import runtime_settings
    monkeypatch.setattr(runtime_settings, "address", lambda name: "")
    with pytest.raises(kb.BugsError, match="HomeShed Pro isn't connected here: paste your Pro key"):
        pro_sync.sync()
