"""image.store and image.delete: an app's own image kept for 7 days under a name the other image tools take,
re-encoded without metadata, and deleted by the app that owns it. Files under tmp_path."""
import base64
import io
import json
import os
import time

import pytest
from PIL import Image

import clients
from tools.image import delete as dl
from tools.image import store as st
from tools.image.generate import ImageError


def img(fmt="PNG", size=(8, 6), exif=None):
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, fmt, **({"exif": exif} if exif else {}))
    return out.getvalue()


def b64(data):
    return base64.b64encode(data).decode()


@pytest.fixture
def out(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_OUTPUT_DIR", str(tmp_path))
    return tmp_path


def as_app(name, fn, *args):
    token = clients.current_client.set(name)
    try:
        return fn(*args)
    finally:
        clients.current_client.reset(token)


def test_stores_and_the_name_feeds_input_images(out):
    got = st.store(b64(img()))
    assert got["name"].startswith("upload-owner-") and got["name"].endswith(".png") and got["mime"] == "image/png"
    assert (got["width"], got["height"]) == (8, 6) and got["bytes"] == (out / got["name"]).stat().st_size
    from tools.image.refs import image_bytes
    assert Image.open(io.BytesIO(image_bytes(got["name"]))).size == (8, 6)  # what image.generate's input_images reads


def test_gps_and_exif_are_stripped(out):
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"          # camera make
    exif[0x8825] = {2: (51.0, 30.0, 0.0)}  # GPS block
    raw = img("JPEG", exif=exif.tobytes())
    assert b"PhoneMaker" in raw
    got = st.store("data:image/jpeg;base64," + b64(raw))
    kept = (out / got["name"]).read_bytes()
    assert b"PhoneMaker" not in kept and not Image.open(io.BytesIO(kept)).getexif()


def test_type_comes_from_the_bytes(out):
    got = st.store("data:image/png;base64," + b64(img("JPEG")))  # the label says png, the bytes say jpeg
    assert got["mime"] == "image/jpeg" and got["name"].endswith(".jpg")


@pytest.mark.parametrize("value,match", [
    ("", "empty"), ("not base64!!", "valid base64"), (b64(b"GIF89a" + b"\x00" * 20), "PNG, JPEG or WebP"),
    (b64(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40), "couldn't be read"),  # a PNG header on junk
])
def test_bad_input_refused(out, value, match):
    with pytest.raises(ImageError, match=match):
        st.store(value)


def test_over_size_and_over_side_refused(out, monkeypatch):
    with pytest.raises(ImageError, match="at most 4096 px"):
        st.store(b64(img(size=(4097, 2))))
    monkeypatch.setattr(st, "MAX_BYTES", 30)
    with pytest.raises(ImageError, match="over"):
        st.store(b64(img()))


def test_decompression_bomb_refused(out, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)  # a 64-pixel image now counts as a bomb
    with pytest.raises(ImageError, match="decompression bomb"):
        st.store(b64(img(size=(8, 8))))


def test_sweep_deletes_old_uploads_only(out):
    old = out / "upload-old.png"
    old.write_bytes(img())
    other = out / "20261001-local-abc.png"
    other.write_bytes(img())
    long_ago = time.time() - st.KEEP_S - 60
    os.utime(old, (long_ago, long_ago))
    os.utime(other, (long_ago, long_ago))
    st.store(b64(img()))
    assert not old.exists() and other.exists()  # a generated image is never swept


def test_full_store_refuses(out, monkeypatch):
    monkeypatch.setattr(st, "MAX_UPLOADS", 2)
    st.store(b64(img()))
    st.store(b64(img()))
    with pytest.raises(ImageError, match="full"):
        st.store(b64(img()))


# --- image.delete ----------------------------------------------------------------------------------------------------
def test_an_app_deletes_its_own_upload_but_not_another_apps(out):
    mine = as_app("shop-app", st.store, b64(img()))["name"]
    theirs = as_app("other-app", st.store, b64(img()))["name"]
    assert st.uploader(mine) == "shop-app"
    with pytest.raises(ImageError, match="^not yours"):
        as_app("shop-app", dl.delete, theirs)
    assert as_app("shop-app", dl.delete, mine) == {"deleted": mine, "sidecar_deleted": False}
    assert not (out / mine).exists() and (out / theirs).exists()
    assert dl.delete(theirs)["deleted"] == theirs  # the owner token may delete any


def test_paid_image_deleted_with_its_record_by_the_app_that_asked(out):
    name = "20261022-060000-google-0123456789ab.png"
    (out / name).write_bytes(img())
    (out / "0123456789ab.json").write_text(json.dumps({"saved_as": name, "caller": "shop-app"}), encoding="utf-8")
    with pytest.raises(ImageError, match="^not yours"):
        as_app("other-app", dl.delete, name)
    assert as_app("shop-app", dl.delete, name)["sidecar_deleted"] is True
    assert not (out / name).exists() and not (out / "0123456789ab.json").exists()


def test_owners_uploads_and_unrecorded_images_are_not_an_apps(out):
    owners = st.store(b64(img()))["name"]
    (out / "mockup.png").write_bytes(img())
    for name in (owners, "mockup.png"):
        with pytest.raises(ImageError, match="^not yours"):
            as_app("owner", dl.delete, name)  # an app that calls itself "owner" isn't the owner


@pytest.mark.parametrize("name,match", [("../x.png", "isn't an image name"), ("nothing-here.png", "^not found")])
def test_bad_delete_refused(out, name, match):
    with pytest.raises(ImageError, match=match):
        dl.delete(name)


def test_discovered_with_manifests():
    import registry
    from manifest import load_manifests
    assert {"store", "delete"} <= {c.name for c in registry.discover() if c.category == "image"}
    risks = {m["id"]: m["risk"] for m in load_manifests() if m["id"] in ("image.store", "image.delete")}
    assert risks == {"image.store": "write", "image.delete": "write"}
