"""image.carousel: names go to the image service; paid designs (kept on the tool server) go as bytes."""
import httpx
import pytest

import tools.image.carousel as ic


def test_carousel_sends_local_names_and_paid_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")
    monkeypatch.setattr(ic.prov, "output_dir", lambda: tmp_path)
    (tmp_path / "20261007-1-xai-abc.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    seen = {}

    def fake(url, json, timeout):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"slides": [{"name": "a.png"}, {"name": "b.png"}], "folder": "promo"})
    monkeypatch.setattr(ic.httpx, "post", fake)
    out = ic.carousel(["20261007-1.png", "20261007-1-xai-abc.jpg"], shape="9:16")
    assert seen["url"].endswith("/image/promo-slides") and seen["body"]["size"] == "1080x1920"
    assert seen["body"]["items"][0] == {"name": "20261007-1.png"} and "image_b64" in seen["body"]["items"][1]
    assert out["folder"] == "promo" and seen["body"]["watermark"] == "" and seen["body"]["watermark_style"] == "corner"
    ic.carousel(["20261007-1.png"], watermark="ASInspiredDesigns")
    assert seen["body"]["watermark"] == "ASInspiredDesigns"
    ic.carousel(["20261007-1.png"], watermark="KindRetroCo", watermark_style="tiled")
    assert seen["body"]["watermark_style"] == "tiled"


@pytest.mark.parametrize("args", [dict(image_names=[]), dict(image_names=["a.png"] * 11),
                                  dict(image_names=["../x.png"]), dict(image_names=["a.png"], shape="1:1"),
                                  dict(image_names=["a.png"], watermark="Brand", watermark_style="diagonal"),
                                  dict(image_names=["a.png"], watermark_style="tiled")])
def test_carousel_refuses_bad_input_without_calling_the_service(monkeypatch, args):
    monkeypatch.setattr(ic.httpx, "post", lambda *a, **k: pytest.fail("called the service"))
    with pytest.raises(ic.ImageError):
        ic.carousel(**args)
