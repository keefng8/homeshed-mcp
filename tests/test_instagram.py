"""Instagram: connect (code -> 1-hour token -> 60-day token, saved, never shown; personal accounts refused), renewal,
and publish (image, carousel, guards, JPEG/shape checks, staged copies deleted). Meta is faked with
httpx.MockTransport through the commerce helper."""
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

APP_ID, SECRET = "9876543210", "ig-app-s3cret"  # secret-scan: allow (test placeholders)
SHORT, LONG, NEW = "short-ig-111", "long-ig-222", "renewed-ig-333"


def _jpeg(w, h):
    return b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"x" * 30


@pytest.fixture
def meta(monkeypatch, tmp_path):
    from tools.commerce import _http as h
    from tools.instagram import _client as ig
    from tools.instagram import publish as pub
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setenv("INSTAGRAM_APP_ID", APP_ID)
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", SECRET)
    monkeypatch.setattr(pub, "POSTS_FILE", tmp_path / "posts.json")
    monkeypatch.setattr(pub, "WAIT_STEP_S", 0)
    ig._pending.clear()
    import vault
    saved, rows = {}, {}

    def fake_set(name, value=None, **kw):
        assert kw.get("kind", "token") in vault.KINDS and isinstance(kw.get("used_by", []), list)
        saved[name] = value
        rows[name] = {"name": name, "updated": time.time(), "note": kw.get("note", "")}
    monkeypatch.setattr(vault, "set_credential", fake_set)
    monkeypatch.setattr(vault, "list_credentials", lambda: list(rows.values()))
    calls, state = [], {"status_code": "FINISHED", "account_type": "BUSINESS", "n": 0}

    def handler(req):
        calls.append(req)
        p, host = req.url.path, req.url.host
        form = parse_qs(req.content.decode()) if req.content else {}
        if host == "api.instagram.com" and p == "/oauth/access_token":
            if form.get("code") == ["bad"]:
                return httpx.Response(400, json={"error_message": f"Invalid code for {SECRET}"})
            assert form["code"] == ["the-code"] and form["client_secret"] == [SECRET]
            return httpx.Response(200, json={"data": [{"access_token": SHORT, "user_id": 111}]})
        assert host == "graph.instagram.com", host
        if p == "/access_token":
            return httpx.Response(200, json={"access_token": LONG, "expires_in": 5184000})
        if p == "/refresh_access_token":
            return httpx.Response(200, json={"access_token": NEW})
        if p == "/v21.0/me":
            return httpx.Response(200, json={"user_id": "1784", "username": "kindretroco",
                                             "account_type": state["account_type"]})
        if p == "/v21.0/1784/media":
            state["n"] += 1
            return httpx.Response(200, json={"id": f"c{state['n']}"})
        if p.startswith("/v21.0/c"):
            return httpx.Response(200, json={"status_code": state["status_code"], "status": "Error: bad image"})
        if p == "/v21.0/1784/media_publish":
            return httpx.Response(200, json={"id": "m9"})
        if p == "/v21.0/m9":
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/p/m9/"})
        return httpx.Response(404, json={})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))
    return ig, pub, saved, rows, calls, state, monkeypatch


def test_connect_swaps_for_a_60_day_token_uses_the_professional_id_and_shows_none(meta):
    ig, _, saved, _, calls, _, _ = meta
    url = ig.start("https://panel.example.com/api/instagram/callback")["url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://www.instagram.com/oauth/authorize?")
    assert q["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    out = ig.finish("the-code#_", q["state"][0])
    assert out == {"connected": True, "username": "kindretroco"}
    assert saved == {"INSTAGRAM_ACCESS_TOKEN": LONG, "INSTAGRAM_USER_ID": "1784"}  # /me's user_id, not the auth one
    assert parse_qs(urlsplit(str(calls[1].url)).query)["grant_type"] == ["ig_exchange_token"]
    assert LONG not in json.dumps(out) and SECRET not in json.dumps(out)


def test_a_personal_account_is_refused_and_state_is_single_use(meta):
    ig, _, saved, _, _, state, _ = meta
    state["account_type"] = "PERSONAL"
    url = ig.start("https://x.example/cb")["url"]
    s = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(Exception, match="Business or Creator"):
        ig.finish("the-code", s)
    assert saved == {}
    with pytest.raises(Exception, match="already used"):
        ig.finish("the-code", s)
    with pytest.raises(Exception, match="https"):
        ig.start("http://panel.lan/cb")
    s2 = parse_qs(urlsplit(ig.start("https://x.example/cb")["url"]).query)["state"][0]
    with pytest.raises(Exception) as e:
        ig.finish("bad", s2)
    assert SECRET not in str(e.value)


def test_token_renews_after_a_week(meta):
    ig, _, saved, rows, _, _, mp = meta
    mp.setenv("INSTAGRAM_ACCESS_TOKEN", LONG)
    mp.setenv("INSTAGRAM_USER_ID", "1784")
    rows["INSTAGRAM_ACCESS_TOKEN"] = {"name": "INSTAGRAM_ACCESS_TOKEN", "updated": 1.0}
    assert ig.token() == (NEW, "1784") and saved["INSTAGRAM_ACCESS_TOKEN"] == NEW


def _ready(meta_tuple, staged):
    ig, pub, _, rows, calls, state, mp = meta_tuple
    mp.setattr(pub.h, "require", lambda setting, label: None)
    mp.setenv("INSTAGRAM_ACCESS_TOKEN", LONG)
    mp.setenv("INSTAGRAM_USER_ID", "1784")
    rows["INSTAGRAM_ACCESS_TOKEN"] = {"name": "INSTAGRAM_ACCESS_TOKEN", "updated": time.time()}
    import r2
    mp.setattr(r2, "put", lambda key, data, mime: staged.append(("put", key, mime)))
    mp.setattr(r2, "presign_get", lambda key, s: f"https://acct.r2.cloudflarestorage.com/b/{key}?X-Amz-Signature=zz")
    mp.setattr(r2, "delete", lambda key: staged.append(("delete", key)) or True)
    mp.setattr(pub, "_media", lambda n: _jpeg(1080, 1350))
    return pub, calls, state


def test_publish_guards_post_nothing(meta):
    _, pub, _, _, calls, _, mp = meta
    with pytest.raises(Exception, match="Instagram posting"):
        pub.publish("hello", ai_generated=False, image_names=["a.png"])
    mp.setattr(pub.h, "require", lambda setting, label: None)
    for kw, match in ((dict(text=" "), "empty"), (dict(text="x" * 2201), "over 2200"),
                      (dict(text=" ".join(f"#t{i}" for i in range(6))), "5 #hashtags"),
                      (dict(image_names=[]), "1-10 images"), (dict(image_names=[f"{i}.png" for i in range(11)]), "1-10"),
                      (dict(ai_generated="yes"), "ai_generated")):
        args = {"text": "hi", "ai_generated": False, "image_names": ["a.png"], **kw}
        with pytest.raises(Exception, match=match):
            pub.publish(**args)
    with pytest.raises(Exception, match="isn't an image file name"):
        pub.publish("hi", ai_generated=False, image_names=["../x"])
    assert not [c for c in calls if "/media" in c.url.path]


def test_publish_one_image_adds_the_ai_line_and_deletes_the_staged_copy(meta):
    staged = []
    pub, calls, _ = _ready(meta, staged)
    out = pub.publish("Retro dogs, new drop. Link in bio.", ai_generated=True, image_names=["slide.png"],
                      alt_text="A retro dog tee design")
    made = parse_qs(next(c for c in calls if c.url.path == "/v21.0/1784/media").content.decode())
    assert made["caption"][0].endswith("\n\nImage made with AI.") and made["alt_text"] == ["A retro dog tee design"]
    assert made["image_url"][0].startswith("https://acct.r2.cloudflarestorage.com/b/instagram/") and "media_type" not in made
    assert out["media_type"] == "IMAGE" and out["permalink"].endswith("/m9/") and out["ai_line"] == "Image made with AI."
    assert [s[0] for s in staged] == ["put", "delete"] and staged[0][2] == "image/jpeg"
    assert "X-Amz-Signature" not in json.dumps(out) and LONG not in json.dumps(out)


def test_publish_carousel_makes_items_then_the_carousel_and_counts(meta):
    staged = []
    pub, calls, _ = _ready(meta, staged)
    out = pub.publish("Which one's yours?", ai_generated=False, image_names=["a.png", "b.png", "c.png"])
    made = [parse_qs(c.content.decode()) for c in calls if c.url.path == "/v21.0/1784/media"]
    assert all(m["is_carousel_item"] == ["true"] for m in made[:3]) and "caption" not in made[0]
    assert made[3]["media_type"] == ["CAROUSEL"] and made[3]["children"] == ["c1,c2,c3"]
    assert out["media_type"] == "CAROUSEL" and out["posted_today"] == 1 and sum(s[0] == "delete" for s in staged) == 3
    meta[-1].setenv("INSTAGRAM_DAILY_MAX", "1")
    with pytest.raises(Exception, match="daily limit"):
        pub.publish("again", ai_generated=False, image_names=["a.png"])


def test_a_post_meta_doesnt_keep_is_reported_not_posted(meta, monkeypatch):
    """2026-10-20: media_publish gave an id, then Meta said the object doesn't exist (the post never showed)."""
    staged = []
    pub, calls, state = _ready(meta, staged)
    from tools.commerce import _http as h
    real = h.request

    def request(service, method, path, **kw):
        if path == "/v21.0/m9":
            raise h.CommerceError("Instagram answered HTTP 400: Unsupported get request. Object with ID 'm9' does not exist")
        return real(service, method, path, **kw)
    monkeypatch.setattr(h, "request", request)
    monkeypatch.setattr(pub.time, "sleep", lambda s: None)
    out = pub.publish("x", ai_generated=False, image_names=["a.png"])
    assert out["posted"] is False and out["permalink"] is None and "doesn't show the post" in out["warning"]


def test_a_failed_container_posts_nothing_and_still_deletes_the_copy(meta):
    staged = []
    pub, calls, state = _ready(meta, staged)
    state["status_code"] = "ERROR"
    with pytest.raises(Exception, match="couldn't prepare"):
        pub.publish("x", ai_generated=False, image_names=["a.png"])
    assert not [c for c in calls if c.url.path.endswith("/media_publish")] and staged[-1][0] == "delete"


def test_media_takes_jpeg_only_in_instagrams_shapes(meta, tmp_path, monkeypatch):
    _, pub, *_ = meta
    from tools.image import providers as prov
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path)
    (tmp_path / "feed.jpg").write_bytes(_jpeg(1080, 1350))
    (tmp_path / "square.jpg").write_bytes(_jpeg(1440, 1440))
    (tmp_path / "story.jpg").write_bytes(_jpeg(1080, 1920))
    (tmp_path / "wide.jpg").write_bytes(_jpeg(2000, 2000))
    (tmp_path / "p.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
    assert pub._media("feed.jpg") and pub._media("square.jpg")
    with pytest.raises(Exception, match="4:5"):
        pub._media("story.jpg")
    with pytest.raises(Exception, match="2000 px wide"):
        pub._media("wide.jpg")
    with pytest.raises(Exception, match="isn't a JPEG"):
        pub._media("p.png")
