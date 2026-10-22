"""image.store: an app's own image kept for 7 days under a name the other image tools take. Files under tmp_path."""
import base64
import os
import time

import pytest

from tools.image import store as st
from tools.image.generate import ImageError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff" + b"\x00" * 40


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def b64(data):
    return base64.b64encode(data).decode()


def test_stores_and_the_name_feeds_input_images(out):
    got = st.store(b64(PNG))
    assert got["name"].startswith("upload-") and got["name"].endswith(".png") and got["mime"] == "image/png"
    assert (out / got["name"]).read_bytes() == PNG and got["bytes"] == len(PNG)
    from tools.image.refs import image_bytes
    assert image_bytes(got["name"]) == PNG  # what image.generate's input_images reads


def test_data_url_and_type_from_the_bytes(out):
    got = st.store("data:image/png;base64," + b64(JPEG))  # the label says png, the bytes say jpeg
    assert got["mime"] == "image/jpeg" and got["name"].endswith(".jpg")


@pytest.mark.parametrize("value,match", [("", "empty"), ("not base64!!", "valid base64"),
                                         (b64(b"GIF89a" + b"\x00" * 20), "PNG, JPEG or WebP")])
def test_bad_input_refused(out, value, match):
    with pytest.raises(ImageError, match=match):
        st.store(value)


def test_over_size_refused(out, monkeypatch):
    monkeypatch.setattr(st, "MAX_BYTES", 30)
    with pytest.raises(ImageError, match="over"):
        st.store(b64(PNG))


def test_sweep_deletes_old_uploads_only(out):
    old = out / "upload-old.png"
    old.write_bytes(PNG)
    other = out / "20261001-local-abc.png"
    other.write_bytes(PNG)
    long_ago = time.time() - st.KEEP_S - 60
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    st.store(b64(PNG))
    assert not old.exists() and other.exists()  # a generated image is never swept


def test_full_store_refuses(out, monkeypatch):
    monkeypatch.setattr(st, "MAX_UPLOADS", 2)
    st.store(b64(PNG))
    st.store(b64(PNG))
    with pytest.raises(ImageError, match="full"):
        st.store(b64(PNG))


def test_discovered_with_manifest():
    import registry
    from manifest import load_manifests
    assert "store" in {c.name for c in registry.discover() if c.category == "image"}
    m = next(m for m in load_manifests() if m["id"] == "image.store")
    assert m["risk"] == "write"
