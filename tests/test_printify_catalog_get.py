"""printify.print_provider.get and printify.blueprint.get (to-do #73): the maker's location (KB-0067) and a blueprint's
material text. Printify is faked."""
import pytest

from tools.commerce import _http as h
from tools.printify import read as pr

PROVIDER = {"id": 72, "title": "Print Clever", "location": {"address1": "1 Factory Rd", "city": "Hull",
                                                            "region": "", "country": "GB", "zip": "HU1"},
            "blueprints": [{"id": n, "title": f"Tee {n}", "brand": "Gildan"} for n in range(70)]}
BLUEPRINT = {"id": 720, "title": "Tote Bag", "brand": "Liberty Bags", "model": "8801",
             "description": "<p>A sturdy tote.</p><ul><li>100% cotton canvas</li><li>12 oz/yd&sup2;</li></ul>"}


@pytest.fixture
def fake(monkeypatch):
    seen = []

    def get(path, params=None):
        seen.append(path)
        return PROVIDER if "print_providers/" in path else BLUEPRINT
    monkeypatch.setattr(pr.pf, "get", get)
    return seen


def test_provider_shows_where_the_maker_is_without_its_street_address(fake):
    out = pr.print_provider_get("72")
    assert fake == ["/v1/catalog/print_providers/72.json"]
    assert out["location"] == {"city": "Hull", "region": "", "country": "GB"}  # no address1 or zip
    assert out["blueprints_total"] == 70 and len(out["blueprints"]) == 60


def test_blueprint_description_comes_back_as_plain_text_lines(fake):
    out = pr.blueprint_get("720")
    assert fake == ["/v1/catalog/blueprints/720.json"]
    assert out["description"] == "A sturdy tote.\n100% cotton canvas\n12 oz/yd²" and out["brand"] == "Liberty Bags"


@pytest.mark.parametrize("bad", ["../1", "abc", "-5"])
def test_ids_are_checked_before_any_call(fake, bad):
    with pytest.raises(h.CommerceError):
        pr.print_provider_get(bad)
    with pytest.raises(h.CommerceError):
        pr.blueprint_get(bad)
    assert fake == []
