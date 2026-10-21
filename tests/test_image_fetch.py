"""image.fetch: a paid job's original from the tool server's folder, a local job's from the image service."""
import base64

import pytest

from tools.image import fetch as f
from tools.image.generate import ImageError

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 50
JOB = "abc123def456"


class R:
    def __init__(self, code=200, js=None, content=b""):
        self.status_code, self._js, self.content = code, js, content

    def json(self):
        return self._js


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://img.test")
    monkeypatch.setattr(f.prov, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(f.prov, "_jobs", {})
    return tmp_path


def test_a_paid_job_returns_its_file_unchanged(env):
    (env / "x.jpg").write_bytes(b"\xff\xd8\xff" + b"j" * 20)
    (env / f"{JOB}.json").write_text('{"job_id": "%s", "status": "done", "saved_as": "x.jpg"}' % JOB)
    out = f.fetch(JOB)
    assert base64.b64decode(out["image_b64"]) == b"\xff\xd8\xff" + b"j" * 20
    assert out["image_mime"] == "image/jpeg" and out["name"] == "x.jpg"


def test_a_local_job_is_fetched_from_the_image_service(env, monkeypatch):
    urls = []

    def get(url, **kw):
        urls.append(url)
        return R(js={"status": "done", "saved_as": "2026-1.png"}) if "/jobs/" in url else R(content=PNG)

    monkeypatch.setattr(f.httpx, "get", get)
    out = f.fetch("0123456789ab")
    assert urls == ["http://img.test/image/jobs/0123456789ab", "http://img.test/image/files/2026-1.png"]
    assert base64.b64decode(out["image_b64"]) == PNG and out["image_mime"] == "image/png" and out["bytes"] == len(PNG)


def test_an_unfinished_job_is_refused(env, monkeypatch):
    monkeypatch.setattr(f.httpx, "get", lambda url, **kw: R(js={"status": "running"}))
    with pytest.raises(ImageError, match="running"):
        f.fetch("0123456789ab")


def test_unknown_job_and_bad_id(env, monkeypatch):
    monkeypatch.setattr(f.httpx, "get", lambda url, **kw: R(404))
    with pytest.raises(ImageError, match="unknown"):
        f.fetch("0123456789ab")
    with pytest.raises(ImageError):
        f.fetch("../x")


def test_a_small_default_and_an_opt_in_up_to_25_mb(env, monkeypatch):
    """The bytes land in the caller's context as base64: 1 MB unless asked for more (to-do #112's hold)."""
    big = PNG + b"x" * (1024 * 1024)  # just over 1 MB
    monkeypatch.setattr(f.httpx, "get",
                        lambda url, **kw: R(js={"status": "done", "saved_as": "a.png"}) if "/jobs/" in url else R(content=big))
    with pytest.raises(ImageError, match=r"1\.0 MB, over max_mb; ask again with max_mb=1\.1"):
        f.fetch("0123456789ab")
    assert f.fetch("0123456789ab", max_mb=2)["bytes"] == len(big)
    for bad in (0, -1, 26, True, "2"):
        with pytest.raises(ImageError, match="max_mb"):
            f.fetch("0123456789ab", max_mb=bad)


def test_over_25_mb_is_refused_whatever_is_asked(env, monkeypatch):
    monkeypatch.setattr(f, "MAX_BYTES", 10)
    monkeypatch.setattr(f, "MAX_MB", 25.0)
    monkeypatch.setattr(f.httpx, "get",
                        lambda url, **kw: R(js={"status": "done", "saved_as": "a.png"}) if "/jobs/" in url else R(content=PNG))
    with pytest.raises(ImageError, match="25 MB"):
        f.fetch("0123456789ab", max_mb=25)
