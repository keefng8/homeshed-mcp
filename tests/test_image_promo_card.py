"""image.promo_card: the request goes to the image service; a mockup kept on the tool server goes as bytes."""
import httpx
import pytest

import tools.image.promo_card as ipc


def test_promo_card_sends_the_card_and_mockup_bytes(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")
    monkeypatch.setattr(ipc.prov, "output_dir", lambda: tmp_path)
    (tmp_path / "printify-abc-1234abcd.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    seen = {}

    def fake(url, json, timeout):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"name": "20261007-010101-123450.png", "folder": "promo"})
    monkeypatch.setattr(ipc.httpx, "post", fake)
    out = ipc.promo_card("printify-abc-1234abcd.jpg", "Retro Sunset Tee", 24.99, template="editorial", shape="4:5",
                         watermark="KindRetroCo")
    body = seen["body"]
    assert seen["url"].endswith("/image/promo-card") and out["folder"] == "promo"
    assert "image_b64" in body["product"] and body["size"] == "1080x1350" and body["template"] == "editorial"
    assert body["price_gbp"] == 24.99 and body["watermark_style"] == "tiled" and body["frame"] == ""
    assert body["cutout"] is False  # a trimmed, framed photo tile by default (R&D's pick, 2026-10-07)
    ipc.promo_card("20261007-202233-788960977.png", "Tee", 20, frame="20261007-202234-1.png", cutout=True)
    assert seen["body"]["product"] == {"name": "20261007-202233-788960977.png"} and seen["body"]["cutout"] is True
    assert seen["body"]["size"] == "1080x1920" and seen["body"]["frame"] == "20261007-202234-1.png"
    ipc.promo_card("a.png", "Tee", 20, frame="halloween")  # a brand frame's style name
    assert seen["body"]["frame"] == "halloween"


@pytest.mark.parametrize("args", [dict(shape="1:1"), dict(template="neon"), dict(watermark_style="diagonal"),
                                  dict(product_image="../x.png"), dict(frame="../frame.png"), dict(frame="Neon Gold")])
def test_promo_card_refuses_bad_input_without_calling_the_service(monkeypatch, args):
    monkeypatch.setattr(ipc.httpx, "post", lambda *a, **k: pytest.fail("called the service"))
    base = dict(product_image="a.png", headline="Tee", price_gbp=20.0)
    with pytest.raises(ipc.ImageError):
        ipc.promo_card(**{**base, **args})
