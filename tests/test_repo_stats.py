"""repo.stats: GitHub counts and release downloads without a token, traffic with one (kept past GitHub's 14 days),
PyPI only when its page links the repo, and the 30-minute cache. GitHub and PyPI are faked."""
import pytest

from tools.repo import stats as st


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


def _fake(monkeypatch, tmp_path, token=None, traffic_ok=True, pypi_links=True, views=None):
    monkeypatch.setattr(st, "HISTORY", tmp_path / "repo_stats.json")
    monkeypatch.setattr(st, "_cache", {})
    monkeypatch.setattr(st, "_token", lambda: token)
    seen = []
    views = views if views is not None else [{"timestamp": "2026-10-09T00:00:00Z", "count": 30, "uniques": 9},
                                             {"timestamp": "2026-10-10T00:00:00Z", "count": 12, "uniques": 4}]

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, params=None, timeout=None):
            seen.append((url, (headers or {}).get("Authorization")))
            if url.endswith("/repos/me/tool"):
                return _Resp(200, {"html_url": "https://github.com/me/tool", "stargazers_count": 5, "forks_count": 2,
                                   "subscribers_count": 1, "open_issues_count": 3, "private": False})
            if url.endswith("/releases"):
                return _Resp(200, [{"tag_name": "v0.1.0", "assets": [{"download_count": 6}, {"download_count": 1}]}])
            if url.endswith("/traffic/views"):
                return _Resp(200 if traffic_ok else 403, {"views": views})
            if url.endswith("/traffic/clones"):
                return _Resp(200, {"clones": [{"timestamp": "2026-10-10T00:00:00Z", "count": 4, "uniques": 2}]})
            if url.endswith("/popular/referrers"):
                return _Resp(200, [{"referrer": "news.example", "count": 20, "uniques": 7}])
            if url == "https://pypi.org/pypi/tool/json":
                return _Resp(200, {"info": {"version": "0.1.0", "project_urls": {
                    "Source": "https://github.com/me/tool" if pypi_links else "https://github.com/someone/else"}}})
            if url.startswith("https://pypistats.org/"):
                return _Resp(200, {"data": {"last_day": 3, "last_week": 10, "last_month": 10}})
            return _Resp(404, {})
    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    return seen


def test_public_counts_and_downloads_without_a_token(monkeypatch, tmp_path):
    seen = _fake(monkeypatch, tmp_path)
    out = st.stats("me/tool")
    assert (out["stars"], out["forks"], out["watchers"], out["open_issues"]) == (5, 2, 1, 3)
    assert out["release_downloads"] == 7 and out["releases"][0] == {"tag": "v0.1.0", "published": None, "downloads": 7}
    assert out["pypi"]["week"] == 10 and "traffic" not in out and "Settings > Credentials" in out["traffic_note"]
    assert not any("traffic" in u for u, _ in seen) and all(a is None for _, a in seen)


def test_traffic_with_a_token_is_kept_past_githubs_14_days(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, token="t0k")
    out = st.stats("me/tool")
    tr = out["traffic"]
    assert tr["views_14d"] == 42 and tr["clones_14d"] == 4 and tr["referrers"][0] == {"from": "news.example",
                                                                                      "views": 20, "people": 7}
    # a later look where GitHub no longer has the 9th: the kept day still counts
    _fake(monkeypatch, tmp_path, token="t0k", views=[{"timestamp": "2026-10-10T00:00:00Z", "count": 15, "uniques": 5}])
    tr = st.stats("me/tool")["traffic"]
    assert tr["views_total"] == 45 and tr["since"] == "2026-10-09"


def test_a_token_without_traffic_access_says_so(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, token="t0k", traffic_ok=False)
    assert "push access" in st.stats("me/tool")["traffic_note"]


def test_pypi_only_when_its_page_links_the_repo(monkeypatch, tmp_path):
    _fake(monkeypatch, tmp_path, pypi_links=False)
    assert st.stats("me/tool")["pypi"] is None


def test_names_are_checked_and_answers_cached(monkeypatch, tmp_path):
    seen = _fake(monkeypatch, tmp_path)
    for bad in ("", "tool", "me/tool/x", "-me/tool", "me/to ol"):
        with pytest.raises(st.StatsError):
            st.stats(bad)
    st.stats("me/tool")
    n = len(seen)
    st.stats("Me/Tool")
    assert len(seen) == n  # cached
    with pytest.raises(st.StatsError, match="404"):
        st.stats("me/missing")
