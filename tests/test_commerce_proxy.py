"""etsy.* and printify.* proxy tools (tools/etsy, tools/printify, tools/commerce): the owner's switches, host-locked
requests, Etsy's token refresh with the rotated refresh token written back to the vault, compact results without
buyers' personal details, and that no key or token ever shows in a result or an error. Every request goes to a fake
API (httpx.MockTransport); the credentials here are dummies built at run time."""
from __future__ import annotations

import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet

import runtime_settings as rs
import vault
from tools.commerce import _http as h
from tools.etsy import _client as et
from tools.etsy import read as er
from tools.printify import read as pr

KEYSTRING, SHARED = "ks" + "Q7" * 10, "ss" + "W3" * 5
REFRESH_OLD, REFRESH_NEW = "12345678." + "Old" * 15, "12345678." + "New" * 15
ACCESS = "12345678." + "Acc" * 15
PF_TOKEN = "pf" + "Z9" * 20
ALL = [KEYSTRING, SHARED, REFRESH_OLD, REFRESH_NEW, ACCESS, PF_TOKEN]
NAMES = {"ETSY_KEYSTRING": KEYSTRING, "ETSY_SHARED_SECRET": SHARED, "ETSY_REFRESH_TOKEN": REFRESH_OLD,
         "ETSY_SHOP_ID": "424242", "PRINTIFY_API_TOKEN": PF_TOKEN, "PRINTIFY_SHOP_ID": "5432"}
PII = ["Jane Buyer", "jane@example.com", "1 Example Street", "please gift wrap"]


def money(a):
    return {"amount": a, "divisor": 100, "currency_code": "GBP"}


RECEIPT = {"receipt_id": 9001, "status": "paid", "created_timestamp": 1790000000, "is_paid": True, "is_shipped": False,
           "grandtotal": money(2599), "subtotal": money(2199), "total_shipping_cost": money(400),
           "total_tax_cost": money(0), "discount_amt": money(0), "country_iso": "GB", "name": PII[0],
           "buyer_email": PII[1], "first_line": PII[2], "formatted_address": PII[2], "message_from_buyer": PII[3],
           "transactions": [{"listing_id": 77, "title": "Mug", "quantity": 2, "price": money(1099), "sku": "MUG-1"}],
           "shipments": [{"carrier_name": "royal-mail", "tracking_code": "RM1", "shipment_notification_timestamp": 1}]}
PF_ORDER = {"id": "5a96f649b2439217d070f507", "status": "on-hold", "created_at": "2026-10-01 10:00:00+00:00",
            "total_price": 2200, "total_shipping": 400, "total_tax": 0, "metadata": {"shop_order_id": 9001},
            "address_to": {"first_name": "Jane", "last_name": "Buyer", "email": PII[1], "address1": PII[2]},
            "line_items": [{"product_id": "p1", "variant_id": 17887, "quantity": 2, "status": "on-hold",
                            "cost": 900, "shipping_cost": 400}],
            "shipments": [{"carrier": "usps", "number": "94001", "delivered_at": None}]}


class Fake:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.token_calls = 0
        self.fail: dict[str, httpx.Response] = {}
        self.expire_access_once = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        path = req.url.path
        for k, resp in self.fail.items():
            if k in path:
                return resp
        if req.url.host == "api.etsy.com":
            assert req.headers["x-api-key"] == f"{KEYSTRING}:{SHARED}"
            if path == "/v3/public/oauth/token":
                self.token_calls += 1
                form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
                assert form["grant_type"] == "refresh_token" and form["client_id"] == KEYSTRING
                return httpx.Response(200, json={"access_token": ACCESS, "token_type": "Bearer", "expires_in": 3600,
                                                 "refresh_token": REFRESH_NEW})
            if "/receipts" in path or path.endswith("/listings"):
                if req.headers.get("authorization") != f"Bearer {ACCESS}":
                    return httpx.Response(401, json={"error": "invalid_token"})
                if self.expire_access_once:
                    self.expire_access_once = False
                    return httpx.Response(401, json={"error": "invalid_token"})
            if path == "/v3/application/shops/424242":
                return httpx.Response(200, json={"shop_id": 424242, "shop_name": "DemoShop", "currency_code": "GBP",
                                                 "listing_active_count": 3, "transaction_sold_count": 10,
                                                 "review_average": 4.9, "review_count": 7, "is_vacation": False,
                                                 "url": "https://www.etsy.com/shop/DemoShop", "create_date": 1})
            if path == "/v3/application/shops/424242/listings":
                rows = [{"listing_id": i, "title": f"Listing {i} " + "x" * 200, "state": "active",
                         "price": money(1299), "quantity": 5, "updated_timestamp": 1, "description": "d" * 5000}
                        for i in range(100)]
                return httpx.Response(200, json={"count": 300, "results": rows})
            if path == "/v3/application/listings/77":
                return httpx.Response(200, json={"listing_id": 77, "title": "Mug", "description": "D" * 9000,
                                                 "price": money(1099), "tags": [f"t{i}" for i in range(30)]})
            if path == "/v3/application/shops/424242/receipts":
                return httpx.Response(200, json={"count": 60, "results": [dict(RECEIPT, receipt_id=i)
                                                                          for i in range(100)]})
            if path == "/v3/application/shops/424242/receipts/9001":
                return httpx.Response(200, json=RECEIPT)
        if req.url.host == "api.printify.com":
            assert req.headers["authorization"] == f"Bearer {PF_TOKEN}" and req.headers["user-agent"]
            if path == "/v1/shops.json":
                return httpx.Response(200, json=[{"id": 5432, "title": "My new store", "sales_channel": "etsy"}])
            if path == "/v1/shops/5432/products.json":
                prods = [{"id": f"p{i}", "title": "T " + "y" * 200, "visible": True, "blueprint_id": 5,
                          "print_provider_id": 1, "description": "z" * 5000, "updated_at": "2026-10-01",
                          "variants": [{"id": v, "price": 1500 + v, "is_enabled": v % 2 == 0, "title": "S"}
                                       for v in range(100)]} for i in range(50)]
                return httpx.Response(200, json={"current_page": 1, "last_page": 3, "total": 120, "data": prods})
            if path == "/v1/shops/5432/products/p1.json":
                return httpx.Response(200, json={"id": "p1", "title": "Tee", "description": "z" * 9000,
                                                 "variants": [{"id": v, "price": 1500, "is_enabled": True,
                                                               "title": "S", "sku": "S1"} for v in range(200)],
                                                 "images": [{}] * 40, "tags": ["a"] * 50})
            if path == "/v1/catalog/blueprints.json":
                return httpx.Response(200, json=[{"id": i, "title": ("Mug " if i % 3 == 0 else "Tee ") + str(i),
                                                  "brand": "Gildan", "model": "M", "description": "q" * 2000,
                                                  "images": ["https://images.printify.com/x.png"] * 5}
                                                 for i in range(1500)])
            if path == "/v1/catalog/blueprints/5/print_providers.json":
                return httpx.Response(200, json=[{"id": i, "title": f"Provider {i}",
                                                  "location": {"country": "US", "address1": "secret street"}}
                                                 for i in range(100)])
            if path == "/v1/shops/5432/orders.json":
                return httpx.Response(200, json={"current_page": 1, "last_page": 9, "total": 90,
                                                 "data": [PF_ORDER] * 30})
            if path == "/v1/shops/5432/orders/5a96f649b2439217d070f507.json":
                return httpx.Response(200, json=PF_ORDER)
        return httpx.Response(404, json={"error": "no route"})


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "no.key")
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    for name, value in NAMES.items():
        monkeypatch.setenv(name, value)
    f = Fake()
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(f))
    et.forget()
    yield f
    et.forget()


def on(*which):
    rs.update({f"commerce_{w}_enabled": True for w in which})


def blob(x) -> str:
    return json.dumps(x, default=str)


def no_secrets(text: str):
    for s in ALL:
        assert s not in text


# --- switches and configuration -------------------------------------------------------------------------------

def test_both_switches_default_off_and_join_the_settings_page():
    assert rs.SCHEMA["commerce_etsy_enabled"]["default"] is False
    assert rs.SCHEMA["commerce_printify_enabled"]["default"] is False
    assert rs.SCHEMA["commerce_etsy_enabled"]["group"] == "Online shops"


@pytest.mark.parametrize("call", [er.shop_get, er.listings_list, er.orders_list, pr.shops_list, pr.products_list])
def test_off_until_the_owner_switches_it_on(fake, call):
    with pytest.raises(h.CommerceError, match="switched off.*only the owner"):
        call()
    assert fake.calls == []


def test_unreadable_settings_mean_off(fake, monkeypatch):
    monkeypatch.setattr(rs, "get", lambda key: (_ for _ in ()).throw(RuntimeError("broken")))
    with pytest.raises(h.CommerceError, match="switched off"):
        pr.shops_list()


@pytest.mark.parametrize("missing_name", ["ETSY_KEYSTRING", "ETSY_SHARED_SECRET", "ETSY_SHOP_ID"])
def test_unconfigured_etsy_names_the_entry_never_a_value(fake, monkeypatch, missing_name):
    on("etsy")
    monkeypatch.delenv(missing_name)
    with pytest.raises(h.CommerceError) as exc:
        er.shop_get()
    assert missing_name in str(exc.value) and "isn't configured" in str(exc.value)
    no_secrets(str(exc.value))


def test_missing_refresh_token_blocks_only_the_shop_owner_calls(fake, monkeypatch):
    on("etsy")
    monkeypatch.delenv("ETSY_REFRESH_TOKEN")
    assert er.shop_get()["shop_name"] == "DemoShop"  # public shop data needs only the app key
    with pytest.raises(h.CommerceError, match="ETSY_REFRESH_TOKEN"):
        er.orders_list()


def test_unconfigured_printify(fake, monkeypatch):
    on("printify")
    monkeypatch.delenv("PRINTIFY_API_TOKEN")
    with pytest.raises(h.CommerceError, match="PRINTIFY_API_TOKEN"):
        pr.shops_list()
    monkeypatch.setenv("PRINTIFY_API_TOKEN", PF_TOKEN)
    monkeypatch.delenv("PRINTIFY_SHOP_ID")
    assert pr.shops_list()["configured_shop_id"] is None
    with pytest.raises(h.CommerceError, match="PRINTIFY_SHOP_ID"):
        pr.products_list()


# --- the vault entry names, the owner's choice in Settings (2026-10-06, to-do #55) -----------------------------

def test_every_entry_name_is_a_setting_with_the_standard_name_as_default():
    from tools import commerce
    for key, (standard, _) in commerce.VAULT_NAMES.items():
        spec = rs.SCHEMA[key]
        assert spec["default"] == standard and spec["group"] == "Online shops" and spec["type"] == "str"


def test_the_tools_read_the_names_the_owner_chose(fake, monkeypatch):
    on("etsy", "printify")
    for name in ("ETSY_KEYSTRING", "ETSY_SHOP_ID", "PRINTIFY_API_TOKEN"):
        monkeypatch.setenv(f"MY_{name}", NAMES[name])
        monkeypatch.delenv(name)
    rs.update({"commerce_etsy_keystring_name": "MY_ETSY_KEYSTRING", "commerce_etsy_shop_id_name": "MY_ETSY_SHOP_ID",
               "commerce_printify_api_token_name": "MY_PRINTIFY_API_TOKEN"})
    assert er.shop_get()["shop_name"] == "DemoShop" and pr.shops_list()["shops"]
    rs.update({"commerce_etsy_shop_id_name": "NOT_SET_ANYWHERE"})
    with pytest.raises(h.CommerceError, match="NOT_SET_ANYWHERE"):  # errors name the entry it looked for
        er.shop_get()


def test_a_bad_name_falls_back_to_the_standard_one(fake):
    from tools import commerce
    on("printify")
    rs.update({"commerce_printify_api_token_name": "lower-case-name"})  # the setting's own check allows it; the tool doesn't
    assert commerce.vault_name("commerce_printify_api_token_name") == "PRINTIFY_API_TOKEN"
    assert pr.shops_list()["shops"]


def test_the_rotated_refresh_token_goes_back_under_the_chosen_name(fake, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("ETSY_REFRESH_TOKEN")
    vault.set_credential("SHOP_REFRESH", REFRESH_OLD, actor="owner")
    rs.update({"commerce_etsy_refresh_token_name": "SHOP_REFRESH"})
    on("etsy")
    er.orders_list()
    assert vault.secret("SHOP_REFRESH") == REFRESH_NEW and vault.secret("ETSY_REFRESH_TOKEN") is None


# --- Etsy tokens ---------------------------------------------------------------------------------------------

def test_access_token_is_cached_in_memory(fake):
    on("etsy")
    er.orders_list()
    er.listings_list()
    assert fake.token_calls == 1


def test_refresh_after_expiry_and_on_a_stale_401(fake, monkeypatch):
    on("etsy")
    er.orders_list()
    monkeypatch.setitem(et._state, "expires", time.time() - 1)
    er.orders_list()
    assert fake.token_calls == 2
    fake.expire_access_once = True
    er.orders_list()
    assert fake.token_calls == 3


def test_rotated_refresh_token_is_written_back_to_the_vault_audited(fake, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    vault.set_credential("ETSY_REFRESH_TOKEN", REFRESH_OLD, actor="owner")
    on("etsy")
    out = er.orders_list()
    assert vault.secret("ETSY_REFRESH_TOKEN") == REFRESH_NEW and "warning" not in out
    trail = vault.audit_trail(5)
    assert trail[0]["name"] == "ETSY_REFRESH_TOKEN" and trail[0]["by"] == "etsy-token-refresh"
    no_secrets((vault.AUDIT_FILE).read_text())
    assert REFRESH_NEW not in (vault.VAULT_FILE).read_text()  # encrypted at rest
    et.forget()
    er.orders_list()  # the next refresh uses the saved, rotated token
    assert f"refresh_token={REFRESH_NEW}" in fake.calls[-2].content.decode()


def test_when_the_vault_cant_save_it_the_token_is_kept_in_memory_and_said(fake):
    on("etsy")  # vault off: VAULT_KEY unset
    out = er.orders_list()
    assert "couldn't save" in out["warning"] and et._state["refresh"] == REFRESH_NEW
    no_secrets(out["warning"])
    et._state["access"] = None
    er.orders_list()
    assert f"refresh_token={REFRESH_NEW}" in fake.calls[-2].content.decode()


def test_refused_refresh_is_scrubbed(fake):
    on("etsy")
    fake.fail["/oauth/token"] = httpx.Response(400, json={"error": "invalid_grant",
                                                          "error_description": f"bad {REFRESH_OLD} see https://x/?t=1"})
    with pytest.raises(h.CommerceError) as exc:
        er.orders_list()
    assert "HTTP 400" in str(exc.value) and "https://" not in str(exc.value)
    no_secrets(str(exc.value))


# --- host lock, redirects, timeouts, errors --------------------------------------------------------------------

def test_requests_only_go_to_the_two_hosts_over_https(fake):
    on("etsy", "printify")
    er.shop_get(), er.orders_list(), pr.shops_list(), pr.products_list()
    assert {(c.url.scheme, c.url.host) for c in fake.calls} == {("https", "api.etsy.com"), ("https", "api.printify.com")}


def test_redirects_are_refused(fake):
    on("printify")
    fake.fail["/v1/shops.json"] = httpx.Response(302, headers={"location": "https://evil.example/steal"})
    with pytest.raises(h.CommerceError, match="redirects are refused"):
        pr.shops_list()
    assert len(fake.calls) == 1


def test_unexpected_paths_are_refused_before_any_request():
    for bad in ("https://evil.example/x", "/v1/../admin", "//evil.example/x", "/v1/shops.json?x=1", "/a b"):
        with pytest.raises(h.CommerceError, match="unexpected"):
            h.request("printify", "GET", bad, headers={}, secrets=())


def test_ids_are_validated_before_any_request(fake):
    on("etsy", "printify")
    for call, bad in ((er.listing_get, "77/../x"), (er.order_get, "abc"), (pr.product_get, "p1.json?x"),
                      (pr.order_get, "../../etc"), (pr.print_providers_list, "5x")):
        with pytest.raises(h.CommerceError):
            call(bad)
    assert fake.calls == []


def test_timeouts_and_network_errors(fake, monkeypatch):
    on("printify")

    def slow(req):
        raise httpx.ReadTimeout("slow", request=req)
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(slow))
    with pytest.raises(h.CommerceError, match="didn't answer within 15 seconds"):
        pr.shops_list()

    def down(req):
        raise httpx.ConnectError(f"cannot reach https://api.printify.com with {PF_TOKEN}", request=req)
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(down))
    with pytest.raises(h.CommerceError) as exc:
        pr.shops_list()
    no_secrets(str(exc.value))
    assert "https://" not in str(exc.value)


@pytest.mark.parametrize("code,hint", [(401, "refused"), (403, "scopes"), (429, "rate limited")])
def test_http_errors_say_why_without_secrets(fake, code, hint):
    on("printify")
    fake.fail["/v1/shops.json"] = httpx.Response(code, json={"message": f"nope {PF_TOKEN} https://a/?b=c"})
    with pytest.raises(h.CommerceError) as exc:
        pr.shops_list()
    assert f"HTTP {code}" in str(exc.value) and hint in str(exc.value) and "https://" not in str(exc.value)
    no_secrets(str(exc.value))


def test_bad_arguments(fake):
    on("etsy", "printify")
    with pytest.raises(h.CommerceError, match="state must be"):
        er.listings_list(state="all")
    with pytest.raises(h.CommerceError, match="limit must be 1 to 20"):
        er.listings_list(limit=500)
    with pytest.raises(h.CommerceError, match="was_paid"):
        er.orders_list(was_paid="maybe")
    with pytest.raises(h.CommerceError, match="limit must be 1 to 10"):
        pr.orders_list(limit=50)
    with pytest.raises(h.CommerceError, match="status"):
        pr.orders_list(status="x&y=1")


# --- results: compact, no personal details, no secrets ------------------------------------------------------------

ETSY_CALLS = [(er.shop_get, {}), (er.listings_list, {"limit": 20}), (er.listing_get, {"listing_id": "77"}),
              (er.orders_list, {"limit": 20}), (er.order_get, {"receipt_id": "9001"})]
PF_CALLS = [(pr.shops_list, {}), (pr.products_list, {"limit": 20}), (pr.product_get, {"product_id": "p1"}),
            (pr.blueprints_list, {"limit": 40}), (pr.print_providers_list, {"blueprint_id": "5"}),
            (pr.orders_list, {"limit": 10}), (pr.order_get, {"order_id": "5a96f649b2439217d070f507"})]


@pytest.mark.parametrize("call,kwargs", ETSY_CALLS + PF_CALLS)
def test_every_result_is_under_5k_with_no_personal_details_or_secrets(fake, call, kwargs):
    on("etsy", "printify")
    out = blob(call(**kwargs))
    assert len(out) <= 5000, f"{call.__name__} returned {len(out)} characters"
    for p in PII + ["secret street"]:
        assert p not in out
    no_secrets(out)


def test_etsy_shapes(fake):
    on("etsy")
    lst = er.listings_list(limit=5, offset=10)
    assert lst["total"] == 300 and lst["next_offset"] == 15 and len(lst["listings"]) == 5
    assert lst["listings"][0]["price"] == {"amount": 12.99, "currency": "GBP"}
    q = fake.calls[-1].url.params
    assert q["state"] == "active" and q["limit"] == "5" and q["offset"] == "10"
    order = er.order_get(receipt_id="9001")
    assert order["total"] == {"amount": 25.99, "currency": "GBP"} and order["items"][0]["sku"] == "MUG-1"
    assert order["item_count"] == 2 and order["country"] == "GB" and order["shipments"][0]["tracking_code"] == "RM1"
    paid = er.orders_list(was_paid="yes", was_shipped="no")
    assert fake.calls[-1].url.params["was_paid"] == "true" and fake.calls[-1].url.params["was_shipped"] == "false"
    assert paid["next_offset"] == 20


def test_printify_shapes(fake):
    on("printify")
    prods = pr.products_list(limit=3)
    assert prods["total"] == 120 and len(prods["products"]) == 3
    assert prods["products"][0]["variants_enabled"] == 50 and prods["products"][0]["min_price"] == 15.0
    bp = pr.blueprints_list(query="mug", limit=10, offset=5)
    assert bp["matches"] == 500 and len(bp["blueprints"]) == 10 and bp["next_offset"] == 15
    assert all("Mug" in b["title"] for b in bp["blueprints"])
    providers = pr.print_providers_list(blueprint_id="5")
    assert providers["print_providers"][0] == {"id": 0, "title": "Provider 0", "country": "US"}
    order = pr.order_get(order_id="5a96f649b2439217d070f507")
    assert order["total_price"] == 22.0 and order["items"][0]["cost"] == 9.0 and order["shop_order_id"] == 9001
    assert "address_to" not in order


def test_tools_never_write(fake):
    on("etsy", "printify")
    for call, kwargs in ETSY_CALLS + PF_CALLS:
        call(**kwargs)
    assert {c.method for c in fake.calls if c.url.path != "/v3/public/oauth/token"} == {"GET"}
