"""Printify mockups for promos: product.get lists them; printify.mockup.fetch copies one into the image store, only from
Printify's image host and only one the product lists. Printify and its image host are faked."""
import httpx
import pytest

from tools.printify import mockup as mk
from tools.printify import read as pr

SRC = "https://images-api.printify.com/mockup/abc123/1001/92570/tee.jpg?camera_label=front"
OTHER = "https://images-api.printify.com/mockup/abc123/1002/92571/tee.jpg?camera_label=back"
JPEG = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x11\x08" + (1000).to_bytes(2, "big") + (1000).to_bytes(2, "big") + b"j" * 50
PRODUCT = {"id": "abc123", "title": "Tee", "variants": [{"id": 1001, "price": 2499, "cost": 1185, "is_enabled": True}],
           "images": [{"src": SRC, "position": "front", "variant_ids": [1001, 1002, 1003], "is_default": True,
                       "is_selected_for_publishing": True},
                      {"src": OTHER, "position": "back", "variant_ids": [1002], "is_default": False}]}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    from tools.image import providers as prov
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path)
    monkeypatch.setattr(pr.pf, "shop_id", lambda: "29235685")
    monkeypatch.setattr(pr.pf, "get", lambda path, params=None: PRODUCT)
    fetched = []

    class Stream:
        def __init__(self, url, status=200, body=JPEG):
            self.status_code, self.body = status, body
            fetched.append(url)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self):
            yield self.body
    state = {"status": 200, "body": JPEG}
    monkeypatch.setattr(mk.httpx, "stream", lambda method, url, **k: Stream(url, state["status"], state["body"]))
    return tmp_path, fetched, state


def test_product_get_lists_mockups(fake):
    out = pr.product_get("abc123", variant_ids=[1002])
    assert out["images"] == 2 and len(out["mockups"]) == 2
    m = out["mockups"][0]
    assert m == {"src": SRC, "position": "front", "camera": "front", "variant_ids": [1002], "variant_count": 3,
                 "is_default": True, "is_selected_for_publishing": True}
    assert pr.product_get("abc123")["mockups"][0]["variant_ids"] == [1001, 1002, 1003]


def test_fetch_saves_once_and_returns_an_image_name(fake):
    store, _, _ = fake
    out = mk.mockup_fetch("abc123", SRC)
    assert out["name"].startswith("printify-abc123-") and out["name"].endswith(".jpg") and out["new"] is True
    assert (store / out["name"]).read_bytes() == JPEG and out["width"] == 1000
    again = mk.mockup_fetch("abc123", SRC)
    assert again["name"] == out["name"] and again["new"] is False and len(list(store.iterdir())) == 1
    from tools.image.print_file import NAME_RE
    assert NAME_RE.match(out["name"])  # image.carousel and threads.publish accept the name


def test_a_replaced_print_at_the_same_address_gets_a_new_copy(fake):
    """The address stays the same when a print is replaced, and the old picture kept coming back (KB-0152)."""
    store, _, state = fake
    first = mk.mockup_fetch("abc123", SRC)
    state["body"] = JPEG + b"replaced"
    second = mk.mockup_fetch("abc123", SRC)
    assert second["new"] is True and second["name"] != first["name"]
    assert (store / second["name"]).read_bytes() == JPEG + b"replaced"
    assert (store / first["name"]).read_bytes() == JPEG  # a card approved on the old name still shows what was approved
    state["body"] = JPEG  # the print put back
    assert mk.mockup_fetch("abc123", SRC) == {**first, "new": False}


def test_a_copy_made_before_the_fix_keeps_its_name_while_the_picture_is_the_same(fake):
    store, _, state = fake
    name = mk.mockup_fetch("abc123", SRC)["name"]
    old_style = name.rsplit("-", 1)[0] + ".jpg"
    (store / name).rename(store / old_style)
    assert mk.mockup_fetch("abc123", SRC)["name"] == old_style
    state["body"] = JPEG + b"replaced"
    assert mk.mockup_fetch("abc123", SRC)["name"] != old_style


def test_no_old_copy_is_passed_off_as_current_when_the_host_fails(fake):
    _, _, state = fake
    mk.mockup_fetch("abc123", SRC)
    state["status"] = 503
    with pytest.raises(Exception, match="HTTP 503"):
        mk.mockup_fetch("abc123", SRC)


def test_fetch_takes_the_images_host_product_get_lists_now(fake, monkeypatch):
    # 2026-10-07: product.get listed mockups on images.printify.com, and fetch refused them (the PM's report)
    new = "https://images.printify.com/mockup/abc123/1001/97992/tee.jpg?camera_label=front"
    monkeypatch.setattr(pr.pf, "get", lambda path, params=None: {**PRODUCT, "images": [{"src": new}]})
    out = mk.mockup_fetch("abc123", new)
    assert out["new"] is True and fake[1] == [new]


@pytest.mark.parametrize("src", [
    "http://images-api.printify.com/mockup/abc123/1001/92570/tee.jpg",  # not https
    "https://evil.example/mockup.jpg",  # another host
    "https://images-api.printify.com.evil.example/x.jpg",
    "https://images.printify.com.evil.example/x.jpg",
    "https://images-api.printify.com/mockup/abc123/9999/1/not-this-products.jpg",  # not one the product lists
])
def test_fetch_refuses_anything_else(fake, src):
    _, fetched, _ = fake
    with pytest.raises(Exception):
        mk.mockup_fetch("abc123", src)
    assert fetched == []


def test_fetch_refuses_bad_answers(fake):
    _, _, state = fake
    state["body"] = b"GIF89a" + b"x" * 20
    with pytest.raises(Exception, match="JPEG or PNG"):
        mk.mockup_fetch("abc123", OTHER)
    state["status"] = 302
    with pytest.raises(Exception, match="HTTP 302"):
        mk.mockup_fetch("abc123", OTHER)
    state.update(status=200, body=JPEG + b"x" * (mk.MAX_MOCKUP_BYTES))
    with pytest.raises(Exception, match="15 MB"):
        mk.mockup_fetch("abc123", OTHER)
