"""image.compare: fetches both images, sends them, and never leaks the service's address. Case list from a
local_ai.ask draft. The image service and the image store are faked."""
import base64

import httpx
import pytest

import tools.image.compare as ic
import tools.image.refs as refs
from tools.commerce._http import CommerceError
from tools.image.generate import ImageError

SCENE, MOCKUP = "20261008-225023-google-de59fd3497d7.jpg", "printify-6ac6b31d5e014449310bbb7c-1a57575b.jpg"


@pytest.fixture
def svc(monkeypatch):
    sent, state = [], {"code": 200}
    monkeypatch.setattr(ic, "base_url", lambda: "http://gpu:8082")
    monkeypatch.setattr(refs, "image_bytes", lambda ref, max_bytes: {SCENE: b"scene", MOCKUP: b"mock"}[ref])

    def fake(url, json=None, timeout=None):
        sent.append((url, json, timeout))
        body = {"ok": True, "found": True} if state["code"] == 200 else {"detail": "that isn't an image"}
        return httpx.Response(state["code"], json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(ic.httpx, "post", fake)
    return sent, state


def test_sends_both_images_and_returns_the_answer(svc):
    sent, _ = svc
    assert ic.compare(SCENE, MOCKUP) == {"ok": True, "found": True}
    url, body, timeout = sent[0]
    assert url == "http://gpu:8082/image/compare" and timeout == 90 and body["region"] == "print"
    assert base64.b64decode(body["image_b64"]) == b"scene" and base64.b64decode(body["reference_b64"]) == b"mock"
    ic.compare(SCENE, MOCKUP, region="all")
    assert sent[1][1]["region"] == "all"


def test_a_bad_region_is_refused_before_any_call(svc):
    with pytest.raises(ImageError, match="region"):
        ic.compare(SCENE, MOCKUP, region="sleeve")
    assert svc[0] == []


def test_an_image_that_cant_be_fetched_says_why(svc, monkeypatch):
    def missing(ref, max_bytes):
        raise CommerceError(f"there's no image {ref}")
    monkeypatch.setattr(refs, "image_bytes", missing)
    with pytest.raises(ImageError, match="there's no image"):
        ic.compare(SCENE, MOCKUP)
    assert svc[0] == []


def test_backend_errors_name_the_reason_not_the_address(svc, monkeypatch):
    _, state = svc
    state["code"] = 400
    with pytest.raises(ImageError, match="that isn't an image") as exc:
        ic.compare(SCENE, MOCKUP)
    assert "gpu:8082" not in str(exc.value)

    def down(*a, **k):
        raise httpx.ReadTimeout("timed out talking to http://gpu:8082")
    monkeypatch.setattr(ic.httpx, "post", down)
    with pytest.raises(ImageError) as exc:
        ic.compare(SCENE, MOCKUP)
    assert "gpu:8082" not in str(exc.value)
