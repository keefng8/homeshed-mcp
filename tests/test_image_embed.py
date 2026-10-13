"""image.embed: fetches the image, sends it, returns the vector, and never leaks the service's address. Case list from
a local_ai.ask draft. The image service and the image store are faked."""
import base64

import httpx
import pytest

import tools.image.embed as ie
import tools.image.refs as refs
from tools.commerce._http import CommerceError
from tools.image.generate import ImageError

DESIGN = "20261009-104839-611960.png"


@pytest.fixture
def svc(monkeypatch):
    sent, state = [], {"code": 200, "body": {"model": "m", "dim": 3, "vector": [0.6, 0.8, 0.0], "ms": 5}}
    monkeypatch.setattr(ie, "base_url", lambda: "http://gpu:8082")
    monkeypatch.setattr(refs, "image_bytes", lambda ref, max_bytes: {DESIGN: b"png"}[ref])

    def fake(url, json=None, timeout=None):
        sent.append((url, json, timeout))
        return httpx.Response(state["code"], json=state["body"], request=httpx.Request("POST", url))
    monkeypatch.setattr(ie.httpx, "post", fake)
    return sent, state


def test_sends_the_image_and_returns_the_vector(svc):
    sent, _ = svc
    assert ie.embed(DESIGN)["vector"] == [0.6, 0.8, 0.0]
    url, body, timeout = sent[0]
    assert url == "http://gpu:8082/image/embed" and timeout == 60 and base64.b64decode(body["image_b64"]) == b"png"


def test_an_image_that_cant_be_fetched_says_why(svc, monkeypatch):
    def missing(ref, max_bytes):
        raise CommerceError(f"there's no image {ref}")
    monkeypatch.setattr(refs, "image_bytes", missing)
    with pytest.raises(ImageError, match="there's no image"):
        ie.embed(DESIGN)
    assert svc[0] == []


def test_short_ram_and_backend_errors_name_the_reason_not_the_address(svc, monkeypatch):
    _, state = svc
    state["code"], state["body"] = 503, {"detail": "only 1.2 GB of RAM is free (2 GB needed to load the image model)"}
    with pytest.raises(ImageError, match="RAM is free") as exc:
        ie.embed(DESIGN)
    assert "gpu:8082" not in str(exc.value)

    def down(*a, **k):
        raise httpx.ReadTimeout("timed out talking to http://gpu:8082")
    monkeypatch.setattr(ie.httpx, "post", down)
    with pytest.raises(ImageError) as exc:
        ie.embed(DESIGN)
    assert "gpu:8082" not in str(exc.value)


def test_an_image_sent_inline_is_embedded_without_the_store(svc, monkeypatch):
    sent, _ = svc
    monkeypatch.setattr(refs, "image_bytes", lambda *a: pytest.fail("looked in the store"))
    assert ie.embed(image_b64=base64.b64encode(b"inline-png").decode())["dim"] == 3
    assert base64.b64decode(sent[0][1]["image_b64"]) == b"inline-png"


@pytest.mark.parametrize("kw, why", [({}, "one of them"), ({"image": DESIGN, "image_b64": "aGk="}, "one of them"),
                                     ({"image_b64": "%%%"}, "isn't base64"), ({"image_b64": "=="}, "isn't base64")])
def test_bad_inline_input_is_refused_before_any_call(svc, kw, why):
    with pytest.raises(ImageError, match=why):
        ie.embed(**kw)
    assert svc[0] == []


def test_inline_images_are_capped(svc, monkeypatch):
    monkeypatch.setattr(ie, "MAX_B64_BYTES", 6)
    with pytest.raises(ImageError, match="at most"):
        ie.embed(image_b64=base64.b64encode(b"0123456789").decode())
    assert svc[0] == []
