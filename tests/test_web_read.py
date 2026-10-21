"""Tests for web.read. SSRF-validation tests are real (no mocking, pure function, fails before
any network call); the Jina Reader fetch itself is mocked — no live external dependency.
"""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "web" and c.name == "read"]
    assert len(matches) == 1


def test_rejects_localhost():
    from tools.web.read import WebReadError, read

    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://localhost/")
    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://127.0.0.1/")


def test_rejects_private_ip():
    from tools.web.read import WebReadError, read

    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://192.168.1.1/")
    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://10.0.0.1/")


def test_rejects_cloud_metadata_endpoint():
    from tools.web.read import WebReadError, read

    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://metadata.google.internal/")


def test_rejects_userinfo_and_non_http_scheme():
    from tools.web.read import WebReadError, read

    with pytest.raises(WebReadError, match="only public HTTP"):
        read("http://user:pass@example.com/")
    with pytest.raises(WebReadError, match="only public HTTP"):
        read("ftp://example.com/")


def test_rejects_empty_url():
    from tools.web.read import WebReadError, read

    with pytest.raises(WebReadError):
        read("")
    with pytest.raises(WebReadError):
        read("   ")


def test_defaults_to_https_scheme():
    from tools.web.read import _normalize_public_http_url

    assert _normalize_public_http_url("example.com/page") == "https://example.com/page"


def test_reads_valid_public_url():
    from tools.web import read as module

    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = b"# Example\n\nReal page content."
    with patch("tools.web.read.urllib.request.urlopen", return_value=fake_resp) as mock_open:
        result = module.read("example.com")

    assert result == {"url": "https://example.com", "content": "# Example\n\nReal page content."}
    req = mock_open.call_args[0][0]
    assert req.full_url == "https://r.jina.ai/https://example.com"


def test_rejects_oversized_response():
    from tools.web import read as module

    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = b"x" * (5 * 1024 * 1024 + 1)
    with patch("tools.web.read.urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(module.WebReadError, match="byte limit"):
            module.read("example.com")


def _page(body: bytes):
    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = body
    return fake_resp


def test_main_content_by_default_whole_page_on_request():
    """R&D 2026-10-15: cookie banners and menus came before the article; Jina's own options drop them."""
    from tools.web import read as module

    with patch("tools.web.read.urllib.request.urlopen", return_value=_page(b"text")) as mock_open:
        module.read("example.com")
        module.read("example.com", full=True)
    main, whole = (c[0][0] for c in mock_open.call_args_list)
    assert main.get_header("X-retain-images") == "none" and "cookie" in main.get_header("X-remove-selector")
    assert whole.get_header("X-remove-selector") is None and whole.get_header("Accept") == "text/plain"


@pytest.mark.parametrize("body, words", [
    (b'{"code": 429, "name": "RateLimitTriggeredError", "readableMessage": "Too many requests"}', "Too many requests"),
    (b'  {"code": 451, "message": "blocked"}', "blocked"),
])
def test_a_jina_error_answer_is_an_error_not_page_text(body, words):
    """R&D 2026-10-15: a JSON answer came back as the page and the caller couldn't parse it."""
    from tools.web import read as module

    with patch("tools.web.read.urllib.request.urlopen", return_value=_page(body)):
        with pytest.raises(module.WebReadError, match=words):
            module.read("example.com")


def test_a_page_that_is_json_but_not_an_error_is_returned():
    from tools.web import read as module

    with patch("tools.web.read.urllib.request.urlopen", return_value=_page(b'{"items": [1, 2]}')):
        assert module.read("example.com")["content"] == '{"items": [1, 2]}'


def test_rejects_antibot_challenge_page():
    from tools.web import read as module

    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = b"Warning: Requiring Captcha\nTitle: Just a moment..."
    with patch("tools.web.read.urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(module.WebReadError, match="anti-bot"):
            module.read("example.com")
