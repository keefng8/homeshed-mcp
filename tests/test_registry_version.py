"""registry.version: latest version and its date from PyPI, npm and GitHub, the edge cases R&D named (2026-10-14):
upload-time order, yanked versions, no releases, rate limits, bad names. The registries are faked."""
import pytest

from tools.registry import version as rv

PYPI = {"info": {"version": "0.10.0"}, "releases": {
    "0.9.0": [{"upload_time_iso_8601": "2026-10-01T10:00:00Z", "yanked": False}],
    "0.10.0": [{"upload_time_iso_8601": "2026-10-14T09:00:00Z"}, {"upload_time_iso_8601": "2026-10-14T09:05:00Z"}],
    "0.9.1": [{"upload_time_iso_8601": "2026-10-05T10:00:00Z", "yanked": True}],
    "0.0.1": []}}


def test_pypi_dates_the_latest_by_its_own_upload_and_sorts_by_time_not_text(monkeypatch):
    seen = []
    monkeypatch.setattr(rv, "_get", lambda url: seen.append(url) or PYPI)
    out = rv.version("pypi", "homeshed-mcp")
    assert seen == ["https://pypi.org/pypi/homeshed-mcp/json"]
    assert out["latest"] == "0.10.0" and out["released_utc"] == "2026-10-14 09:00 UTC"
    assert [p["version"] for p in out["previous"]] == ["0.9.1", "0.9.0"]  # 0.0.1 had no upload
    assert out["previous"][0]["yanked"] is True and "yanked" not in out["previous"][1]


def test_npm_uses_its_latest_tag_and_times(monkeypatch):
    doc = {"dist-tags": {"latest": "2.0.0"}, "time": {"created": "2020-01-01T00:00:00Z", "1.0.0": "2025-01-01T00:00:00Z",
                                                      "2.0.0": "2026-10-14T08:00:00.000Z"}}
    seen = []
    monkeypatch.setattr(rv, "_get", lambda url: seen.append(url) or doc)
    out = rv.version("npm", "@scope/pkg")
    assert seen == ["https://registry.npmjs.org/@scope%2Fpkg"]
    assert out["latest"] == "2.0.0" and out["released_utc"] == "2026-10-14 08:00 UTC"
    assert [p["version"] for p in out["previous"]] == ["1.0.0"]


def test_github_skips_drafts_and_prereleases_and_says_no_releases(monkeypatch):
    rels = [{"tag_name": "v0.4.0-rc1", "prerelease": True, "published_at": "2026-10-14T10:00:00Z"},
            {"tag_name": "v0.3.0", "published_at": "2026-10-13T10:00:00Z", "html_url": "https://github.com/o/r/r/v0.3.0"},
            {"tag_name": "v0.5.0", "draft": True}]
    monkeypatch.setattr(rv, "_get", lambda url: rels)
    out = rv.version("github", "o/r")
    assert out["latest"] == "v0.3.0" and out["url"].endswith("v0.3.0")
    monkeypatch.setattr(rv, "_get", lambda url: [])
    assert rv.version("github", "o/r")["note"] == "no releases"

    def missing(url):
        raise LookupError("not found")
    monkeypatch.setattr(rv, "_get", missing)
    assert rv.version("pypi", "nope")["note"] == "not found"


def test_rate_limits_and_bad_names_are_said_plainly(monkeypatch):
    import io
    import urllib.error

    def limited(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 403, "rate limit", {"X-RateLimit-Remaining": "0"}, io.BytesIO())
    monkeypatch.setattr(rv.urllib.request, "urlopen", limited)
    with pytest.raises(rv.RegistryError, match="rate-limited"):
        rv.version("github", "o/r")
    for eco, name in (("pypi", "../etc"), ("github", "no-slash"), ("cargo", "x"), ("npm", "UPPER")):
        with pytest.raises(rv.RegistryError):
            rv.version(eco, name)


def test_every_request_says_who_it_is(monkeypatch):
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b'{"info": {"version": "1.0"}, "releases": {}}'
    monkeypatch.setattr(rv.urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Resp())
    rv.version("pypi", "x")
    assert sent[0].get_header("User-agent") == rv.USER_AGENT and sent[0].host == "pypi.org"
