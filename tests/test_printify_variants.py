"""printify.variants.list: catalogue variants mapped to id, colour, size and print areas; Printify faked."""
import httpx
import pytest

CATALOG = {"variants": [
    {"id": 11, "title": "Black / S", "options": {"color": "Black", "size": "S"},
     "placeholders": [{"position": "front", "width": 4500, "height": 5400}, {"position": "back", "width": 4500, "height": 5400}]},
    {"id": 12, "title": "Heather Black / M", "options": {"color": "Heather Black", "size": "M"},
     "placeholders": [{"position": "front", "width": 4500, "height": 5400}]},
    {"id": 13, "title": "White / S", "options": {"color": "White", "size": "S"}, "placeholders": []},
]}


@pytest.fixture
def api(monkeypatch):
    from tools.commerce import _http as h
    from tools.printify import read
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setenv("PRINTIFY_API_TOKEN", "t0k")
    monkeypatch.setattr(h, "require", lambda key, label: None)
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/v1/catalog/blueprints/6/print_providers/99/variants.json":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(404, json={})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))
    return read, calls


def test_maps_variants_and_print_areas(api):
    read, _ = api
    out = read.variants_list("6", "99")
    assert out["matches"] == 3 and out["next_offset"] is None
    assert out["variants"][0] == {"id": 11, "colour": "Black", "size": "S",
                                  "print_areas": [{"position": "front", "width_px": 4500, "height_px": 5400},
                                                  {"position": "back", "width_px": 4500, "height_px": 5400}]}
    assert "printify.product.get" in out["cost"]


def test_colour_filter_and_paging(api):
    read, _ = api
    out = read.variants_list("6", "99", colour="BLACK", limit=1)
    assert out["matches"] == 2 and [v["id"] for v in out["variants"]] == [11] and out["next_offset"] == 1


@pytest.mark.parametrize("bad", ["abc", "../x", ""])
def test_bad_ids_make_no_call(api, bad):
    read, calls = api
    with pytest.raises(Exception):
        read.variants_list(bad, "99")
    assert calls == []
