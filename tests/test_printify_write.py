"""printify.product.create: drafts only, behind two switches and a daily cap; Printify faked."""
import json

import httpx
import pytest

TOKEN = "pf-t0ken-123"  # secret-scan: allow (test placeholder)


@pytest.fixture
def api(monkeypatch, tmp_path):
    from tools.commerce import _http as h
    from tools.printify import write as w
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setenv("PRINTIFY_API_TOKEN", TOKEN)
    monkeypatch.setenv("PRINTIFY_SHOP_ID", "777")
    monkeypatch.setenv("IMAGE_BASE_URL", "http://img.test")
    monkeypatch.setattr(w, "POSTS_FILE", tmp_path / "made.json")
    switches = {"commerce_printify_enabled": True, "commerce_printify_write_enabled": True}
    monkeypatch.setattr(h, "require", lambda key, label: None if switches.get(key) else (_ for _ in ()).throw(h.CommerceError(f"{label} is switched off")))
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/v1/uploads/images.json":
            return httpx.Response(200, json={"id": "img-1"})
        if req.url.path == "/v1/shops/777/products.json":
            return httpx.Response(200, json={"id": "prod-9"})
        return httpx.Response(404, json={})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))

    class F:
        status_code, content = 200, b"PNGBYTES"
    monkeypatch.setattr(w.httpx, "get", lambda url, timeout=None: F())
    return w, calls, switches


ARGS = dict(title="Oak leaf tee", description="A bold oak leaf.", blueprint_id=6, print_provider_id=99,
            variants=[{"id": 12345, "price_pence": 2199}], print_file="20261007-041200-123450.png")


def test_uploads_then_makes_an_unpublished_draft(api):
    w, calls, _ = api
    out = w.product_create(**ARGS, tags=["oak", "nature"])
    assert out == {"product_id": "prod-9", "image_id": "img-1", "title": "Oak leaf tee", "variants": 1,
                   "published": False, "created_today": 1, "daily_max": 20}
    up, made = calls
    assert json.loads(up.content)["file_name"] == ARGS["print_file"] and up.headers["authorization"] == f"Bearer {TOKEN}"
    body = json.loads(made.content)
    assert body["variants"] == [{"id": 12345, "price": 2199, "is_enabled": True}]
    assert body["print_areas"][0]["placeholders"][0]["images"][0]["id"] == "img-1"
    assert not any("publish" in c.url.path for c in calls) and TOKEN not in json.dumps(out)


def test_switched_off_and_capped(api, monkeypatch):
    w, calls, switches = api
    switches["commerce_printify_write_enabled"] = False
    with pytest.raises(Exception, match="switched off"):
        w.product_create(**ARGS)
    switches["commerce_printify_write_enabled"] = True
    monkeypatch.setenv("PRINTIFY_DAILY_MAX", "1")
    w.product_create(**ARGS)
    with pytest.raises(Exception, match="daily limit"):
        w.product_create(**ARGS)


@pytest.mark.parametrize("change", [{"title": ""}, {"variants": []}, {"variants": [{"id": "x", "price_pence": 2199}]},
                                    {"variants": [{"id": 1, "price_pence": 5}]}, {"print_file": "../x.png"},
                                    {"print_file": "x.jpg"}, {"position": "sleeve"}])
def test_bad_arguments_make_no_call(api, change):
    w, calls, _ = api
    with pytest.raises(Exception):
        w.product_create(**{**ARGS, **change})
    assert calls == []
