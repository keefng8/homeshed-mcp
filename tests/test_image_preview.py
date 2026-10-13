"""image.preview: checks its inputs before calling the image service, sends the request, and never leaks the
service's address. Case list from a local_ai.ask draft. The image service is faked."""
import httpx
import pytest

import tools.image.preview as ip
from tools.image.generate import ImageError

NAME = "20261008-223115-919451.png"


@pytest.fixture
def posts(monkeypatch):
    sent, state = [], {"code": 200}
    monkeypatch.setattr(ip, "base_url", lambda: "http://gpu:8082")

    def fake(url, json=None, timeout=None):
        sent.append((url, json, timeout))
        body = {"name": "x.jpg", "ref": "previews/x.jpg"} if state["code"] == 200 else {"detail": "no such print file"}
        return httpx.Response(state["code"], json=body, request=httpx.Request("POST", url))
    monkeypatch.setattr(ip.httpx, "post", fake)
    return sent, state


def test_sends_the_request_and_returns_the_answer(posts):
    sent, _ = posts
    assert ip.preview(NAME, garment="Navy", print_area="back")["ref"] == "previews/x.jpg"
    assert sent == [("http://gpu:8082/image/garment-preview",
                     {"name": NAME, "garment": "Navy", "print_area": "back", "show_area": True}, 60)]


@pytest.mark.parametrize("kw", [{"image": "design.png"}, {"image": "../20261008-223115-919451.png"},
                                {"garment": "pink"}, {"garment": "#12345g"}, {"print_area": "sleeve"}])
def test_bad_inputs_are_refused_before_any_call(posts, kw):
    sent, _ = posts
    with pytest.raises(ImageError):
        ip.preview(**{"image": NAME, **kw})
    assert sent == []


def test_hex_colours_pass(posts):
    ip.preview(NAME, garment="#A1b2C3")
    assert posts[0][0][1]["garment"] == "#A1b2C3"


def test_include_image_returns_the_jpeg(posts, monkeypatch):
    import tools.image.refs as refs
    asked = []
    monkeypatch.setattr(refs, "image_bytes", lambda ref, max_bytes: asked.append(ref) or b"\xff\xd8jpeg")
    out = ip.preview(NAME, include_image=True)
    assert asked == ["previews/x.jpg"] and out["image_b64"] == "/9hqcGVn" and out["image_mime"] == "image/jpeg"
    assert "image_b64" not in ip.preview(NAME)  # only when asked


def test_backend_errors_name_the_reason_not_the_address(posts, monkeypatch):
    _, state = posts
    state["code"] = 404
    with pytest.raises(ImageError, match="no such print file") as exc:
        ip.preview(NAME)
    assert "gpu:8082" not in str(exc.value)

    def down(*a, **k):
        raise httpx.ConnectError("cannot reach http://gpu:8082")
    monkeypatch.setattr(ip.httpx, "post", down)
    with pytest.raises(ImageError) as exc:
        ip.preview(NAME)
    assert "gpu:8082" not in str(exc.value)
