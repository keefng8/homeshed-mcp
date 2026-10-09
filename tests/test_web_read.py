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


def test_rejects_antibot_challenge_page():
    from tools.web import read as module

    fake_resp = MagicMock()
    fake_resp.__enter__.return_value = fake_resp
    fake_resp.read.return_value = b"Warning: Requiring Captcha\nTitle: Just a moment..."
    with patch("tools.web.read.urllib.request.urlopen", return_value=fake_resp):
        with pytest.raises(module.WebReadError, match="anti-bot"):
            module.read("example.com")
