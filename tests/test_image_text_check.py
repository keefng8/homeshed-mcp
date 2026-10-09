"""image.text_check: one source, sent by name or as bytes to the image service, which is faked here."""
import httpx
import pytest

import tools.image.text_check as tc


@pytest.fixture
def svc(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://10.9.9.9:8082")
    monkeypatch.setattr(tc.prov, "output_dir", lambda: tmp_path)
    seen = {}

    def fake(url, json, timeout):
        seen.update(url=url, body=json)
        return httpx.Response(200, json={"ok": False, "text_found": True, "regions": [{"corner": "br", "text": True}]})
    monkeypatch.setattr(tc.httpx, "post", fake)
    return seen, tmp_path


def test_by_name_local_or_paid_and_by_finished_job(svc, monkeypatch):
    seen, store = svc
    out = tc.text_check(image_name="20261007-233235-1805025358.png")
    assert seen["url"].endswith("/image/text-check") and seen["body"] == {"name": "20261007-233235-1805025358.png"}
    assert out["text_found"] is True
    (store / "20261007-1-xai-abc.jpg").write_bytes(b"\xff\xd8\xffjpeg")
    tc.text_check(image_name="20261007-1-xai-abc.jpg")
    assert "image_b64" in seen["body"]  # a paid image lives on the tool server: sent as bytes
    import tools.image.status as st
    monkeypatch.setattr(st, "status", lambda job_id: {"status": "done", "saved_as": "20261008-1.png"})
    tc.text_check(job_id="026d25e207b2")
    assert seen["body"] == {"name": "20261008-1.png"}


@pytest.mark.parametrize("args", [dict(), dict(job_id="abc", image_name="a.png"), dict(image_name="../x.png"),
                                  dict(image_b64="x" * (tc.MAX_BYTES * 2))])
def test_bad_input_never_calls_the_service(svc, monkeypatch, args):
    monkeypatch.setattr(tc.httpx, "post", lambda *a, **k: pytest.fail("called the service"))
    with pytest.raises(tc.ImageError):
        tc.text_check(**args)


def test_an_unfinished_job_is_refused(svc, monkeypatch):
    import tools.image.status as st
    monkeypatch.setattr(st, "status", lambda job_id: {"status": "running", "saved_as": "x.png"})
    with pytest.raises(tc.ImageError, match="isn't finished"):
        tc.text_check(job_id="026d25e207b2")
