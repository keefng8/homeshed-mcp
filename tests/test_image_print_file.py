"""image.print_file: passes a local design by name and a paid provider's design as bytes."""
import base64

import pytest

from tools.image import print_file as pf
from tools.image.generate import ImageError


class R:
    status_code = 200

    def json(self):
        return {"files": {"light": {"name": "x.png"}}}


@pytest.fixture
def sent(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://img.test")
    monkeypatch.setattr(pf.prov, "output_dir", lambda: tmp_path)
    calls = []
    monkeypatch.setattr(pf.httpx, "post", lambda url, json=None, timeout=None: calls.append((url, json)) or R())
    return calls, tmp_path


def test_a_local_design_goes_by_name(sent):
    calls, _ = sent
    pf.print_file("20261007-040000-7.png", 4500, 5400)
    url, body = calls[0]
    assert url == "http://img.test/image/print-file" and body["name"] == "20261007-040000-7.png"
    assert body["variants"] == ["light", "dark"] and "image_b64" not in body


def test_a_paid_providers_design_is_sent_as_bytes(sent):
    calls, folder = sent
    (folder / "abc123def456.png").write_bytes(b"PNGDATA")
    pf.print_file("abc123def456.png", 4500, 5400, variants=["light"], colours=3)
    body = calls[0][1]
    assert base64.b64decode(body["image_b64"]) == b"PNGDATA" and "name" not in body and body["colours"] == 3


@pytest.mark.parametrize("bad", ["../etc/passwd", "a/b.png", "x.exe", ""])
def test_odd_names_refused_without_a_call(sent, bad):
    calls, _ = sent
    with pytest.raises(ImageError):
        pf.print_file(bad, 4500, 5400)
    assert calls == []
