"""Threads: connect (code -> 1-hour token -> 60-day token, saved, never shown), renewal, and publish's guards.
Meta is faked with httpx.MockTransport through the commerce helper."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

APP_ID, SECRET = "1234567890", "app-s3cret"  # secret-scan: allow (test placeholders)
SHORT, LONG, NEW = "short-tok-111", "long-tok-222", "renewed-tok-333"


@pytest.fixture
def meta(monkeypatch, tmp_path):
    from tools.commerce import _http as h
    from tools.threads import _client as th
    from tools.threads import publish as pub
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setenv("THREADS_APP_ID", APP_ID)
    monkeypatch.setenv("THREADS_APP_SECRET", SECRET)
    monkeypatch.setattr(pub, "POSTS_FILE", tmp_path / "posts.json")
    monkeypatch.setattr(pub, "WAIT_STEP_S", 0)
    th._pending.clear()
    import vault
    saved, rows = {}, {}

    def fake_set(name, value=None, **kw):
        assert kw.get("kind", "token") in vault.KINDS and isinstance(kw.get("used_by", []), list)
        saved[name] = value
        rows[name] = {"name": name, "updated": rows.get("_age", 0) or __import__("time").time(), "note": kw.get("note", "")}
    monkeypatch.setattr(vault, "set_credential", fake_set)
    monkeypatch.setattr(vault, "list_credentials", lambda: [r for k, r in rows.items() if k != "_age"])
    calls = []
    state = {"container": "FINISHED"}

    def handler(req):
        calls.append(req)
        p = req.url.path
        form = parse_qs(req.content.decode()) if req.content else {}
        if p == "/oauth/access_token":
            if form.get("code") == ["bad"]:
                return httpx.Response(400, json={"error": {"message": f"Invalid code for {SECRET}"}})
            assert form["code"] == ["the-code"] and form["client_secret"] == [SECRET]
            return httpx.Response(200, json={"access_token": SHORT, "user_id": 42})
        if p == "/access_token":
            return httpx.Response(200, json={"access_token": LONG, "expires_in": 5184000})
        if p == "/refresh_access_token":
            return httpx.Response(200, json={"access_token": NEW})
        if p == "/v1.0/me":
            return httpx.Response(200, json={"username": "myshop"})
        if p == "/v1.0/42/threads":
            return httpx.Response(200, json={"id": "c1"})
        if p == "/v1.0/c1":
            return httpx.Response(200, json={"status": state["container"]})
        if p == "/v1.0/42/threads_publish":
            return httpx.Response(200, json={"id": "p9"})
        if p == "/v1.0/p9":
            return httpx.Response(200, json={"permalink": "https://www.threads.com/@myshop/post/p9"})
        return httpx.Response(404, json={})
    monkeypatch.setattr(h, "_TRANSPORT", httpx.MockTransport(handler))
    return th, pub, saved, rows, calls, state, monkeypatch


def _connect(th):
    url = th.start("https://panel.example.com/api/threads/callback")["url"]
    st = parse_qs(urlsplit(url).query)["state"][0]
    return th.finish("the-code#_", st)


def test_connect_swaps_for_a_60_day_token_and_shows_none(meta):
    th, _, saved, _, calls, _, _ = meta
    url = th.start("https://panel.example.com/api/threads/callback")["url"]
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://threads.com/oauth/authorize?") and q["scope"] == ["threads_basic,threads_content_publish,threads_manage_insights,threads_read_replies"]
    out = th.finish("the-code#_", q["state"][0])
    assert out == {"connected": True, "username": "myshop"}
    assert saved == {"THREADS_ACCESS_TOKEN": LONG, "THREADS_USER_ID": "42"}
    assert parse_qs(urlsplit(str(calls[1].url)).query)["grant_type"] == ["th_exchange_token"]
    assert LONG not in json.dumps(out) and SECRET not in json.dumps(out)


def test_state_single_use_https_only_and_errors_hide_the_secret(meta):
    th, *_ = meta
    with pytest.raises(Exception, match="https"):
        th.start("http://panel.lan/cb")
    url = th.start("https://x.example/cb")["url"]
    s = parse_qs(urlsplit(url).query)["state"][0]
    with pytest.raises(Exception) as e:
        th.finish("bad", s)
    assert SECRET not in str(e.value)
    with pytest.raises(Exception, match="already used"):
        th.finish("the-code", s)
    with pytest.raises(Exception, match="no code"):
        th.finish_url("https://x.example/cb")


def test_token_renews_after_a_week(meta):
    th, _, saved, rows, _, _, mp = meta
    mp.setenv("THREADS_ACCESS_TOKEN", LONG)
    mp.setenv("THREADS_USER_ID", "42")
    rows["THREADS_ACCESS_TOKEN"] = {"name": "THREADS_ACCESS_TOKEN", "updated": 1.0}
    assert th.token() == (NEW, "42") and saved["THREADS_ACCESS_TOKEN"] == NEW


def test_publish_guards(meta):
    th, pub, _, _, calls, _, mp = meta
    with pytest.raises(Exception, match="Threads posting"):
        pub.publish("hello")
    mp.setattr(pub.h, "require", lambda setting, label: None)
    with pytest.raises(Exception, match="empty"):
        pub.publish("  ")
    with pytest.raises(Exception, match="over 500"):
        pub.publish("x" * 501)
    with pytest.raises(Exception, match="https"):
        pub.publish("hi", link="ftp://x")
    with pytest.raises(Exception, match="isn't connected"):
        pub.publish("hi")
    assert not [c for c in calls if "/threads" in c.url.path]


def test_publish_posts_returns_the_link_and_counts(meta):
    th, pub, _, rows, calls, _, mp = meta
    mp.setattr(pub.h, "require", lambda setting, label: None)
    mp.setenv("THREADS_ACCESS_TOKEN", LONG)
    mp.setenv("THREADS_USER_ID", "42")
    import time
    rows["THREADS_ACCESS_TOKEN"] = {"name": "THREADS_ACCESS_TOKEN", "updated": time.time()}
    out = pub.publish("New designs are live!", link="https://example.com/shop")
    assert out["posted"] and out["permalink"].endswith("/p9") and out["posted_today"] == 1
    made = parse_qs(next(c for c in calls if c.url.path == "/v1.0/42/threads").content.decode())
    assert made["media_type"] == ["TEXT"] and made["link_attachment"] == ["https://example.com/shop"]
    assert LONG not in json.dumps(out)
    mp.setenv("THREADS_DAILY_MAX", "1")
    with pytest.raises(Exception, match="daily limit"):
        pub.publish("again")


PNG_800 = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (800).to_bytes(4, "big") + (800).to_bytes(4, "big") + b"x" * 40
JPEG_2000 = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x11\x08" + (2000).to_bytes(2, "big") + (2000).to_bytes(2, "big") + b"x" * 30


def _ready(meta_tuple, staged):
    th, pub, _, rows, calls, state, mp = meta_tuple
    mp.setattr(pub.h, "require", lambda setting, label: None)
    mp.setenv("THREADS_ACCESS_TOKEN", LONG)
    mp.setenv("THREADS_USER_ID", "42")
    import time
    rows["THREADS_ACCESS_TOKEN"] = {"name": "THREADS_ACCESS_TOKEN", "updated": time.time()}
    import r2
    mp.setattr(r2, "put", lambda key, data, mime: staged.append(("put", key, mime)))
    mp.setattr(r2, "presign_get", lambda key, s: f"https://acct.r2.cloudflarestorage.com/b/{key}?X-Amz-Signature=zz")
    mp.setattr(r2, "delete", lambda key: staged.append(("delete", key)) or True)
    return pub, calls


def test_publish_one_image_then_deletes_the_staged_copy(meta, tmp_path, monkeypatch):
    staged = []
    pub, calls = _ready(meta, staged)
    monkeypatch.setattr(pub, "_media", lambda n: (PNG_800, "image/png"))
    out = pub.publish("Frenchie season", image_names=["20261007-1-xai-abc.png"], alt_text="A French bulldog design")
    made = parse_qs(next(c for c in calls if c.url.path == "/v1.0/42/threads").content.decode())
    assert out["media_type"] == "IMAGE" and made["media_type"] == ["IMAGE"] and made["alt_text"] == ["A French bulldog design"]
    assert made["image_url"][0].startswith("https://acct.r2.cloudflarestorage.com/b/threads/")
    assert [s[0] for s in staged] == ["put", "delete"] and staged[0][1] == staged[1][1]
    assert "X-Amz-Signature" not in json.dumps(out)


def test_publish_carousel_makes_items_then_the_carousel(meta, monkeypatch):
    staged = []
    pub, calls = _ready(meta, staged)
    monkeypatch.setattr(pub, "_media", lambda n: (PNG_800, "image/png"))
    out = pub.publish("Which one's yours?", image_names=["a.png", "b.png", "c.png"])
    made = [parse_qs(c.content.decode()) for c in calls if c.url.path == "/v1.0/42/threads"]
    assert [m["media_type"][0] for m in made] == ["IMAGE", "IMAGE", "IMAGE", "CAROUSEL"]
    assert all(m["is_carousel_item"] == ["true"] for m in made[:3]) and made[3]["children"] == ["c1,c1,c1"]
    assert out["media_type"] == "CAROUSEL" and sum(1 for s in staged if s[0] == "delete") == 3


def test_publish_media_failures_upload_and_post_nothing(meta, monkeypatch):
    staged = []
    pub, calls = _ready(meta, staged)
    with pytest.raises(Exception, match="at most 10"):
        pub.publish("x", image_names=[f"{i}.png" for i in range(11)])
    with pytest.raises(Exception, match="isn't an image file name"):
        pub.publish("x", image_names=["../etc/passwd"])
    assert not staged and not [c for c in calls if "/threads" in c.url.path]


def test_media_checks_type_size_and_width(meta, tmp_path, monkeypatch):
    _, pub, *_ = meta
    from tools.image import providers as prov
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path)
    (tmp_path / "ok.png").write_bytes(PNG_800)
    (tmp_path / "wide.jpg").write_bytes(JPEG_2000)
    (tmp_path / "gif.png").write_bytes(b"GIF89a" + b"x" * 40)
    assert pub._media("ok.png") == (PNG_800, "image/png")
    with pytest.raises(Exception, match="2000 px wide"):
        pub._media("wide.jpg")
    with pytest.raises(Exception, match="JPEG or PNG"):
        pub._media("gif.png")
    assert pub._width(JPEG_2000) == 2000 and pub._width(PNG_800) == 800 and pub._width(b"junk") is None


def test_r2_presign_and_put_sign_without_leaking(monkeypatch):
    import r2
    vals = {"R2_ACCESS_KEY_ID": "AKID123", "R2_SECRET_ACCESS_KEY": "s3cr3t-value",  # secret-scan: allow (test placeholders)
            "R2_CLOUDFLARE_S3": "https://0123abcd.r2.cloudflarestorage.com"}
    monkeypatch.setattr(r2.h, "secret", lambda n: vals.get(n))
    url = r2.presign_get("threads/20261007/a.png", 3600)
    q = parse_qs(urlsplit(url).query)
    assert url.startswith("https://0123abcd.r2.cloudflarestorage.com/homeshed-studio/threads/20261007/a.png?")
    assert q["X-Amz-Expires"] == ["3600"] and q["X-Amz-SignedHeaders"] == ["host"] and len(q["X-Amz-Signature"][0]) == 64
    assert "s3cr3t-value" not in url
    seen = {}
    monkeypatch.setattr(r2.httpx, "request", lambda m, u, headers, content, timeout: seen.update(m=m, u=u, h=headers)
                        or httpx.Response(200))
    r2.put("threads/x.jpg", b"data", "image/jpeg")
    assert seen["m"] == "PUT" and seen["h"]["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID123/")
    with pytest.raises(r2.R2Error):
        r2.presign_get("../x")
    vals.pop("R2_SECRET_ACCESS_KEY")
    with pytest.raises(Exception, match="R2_SECRET_ACCESS_KEY"):
        r2.presign_get("a.png")


def test_insights_reads_metrics_and_explains_a_missing_permission(meta, monkeypatch):
    staged = []
    pub, calls = _ready(meta, staged)
    seen = {}

    def fake_request(host, method, path, headers, secrets, params=None, **k):
        seen.update(path=path, params=params)
        if path.startswith("/v1.0/999/"):
            raise pub.h.CommerceError("Threads refused: (#10) Application does not have permission for this action")
        return httpx.Response(200, json={"data": [{"name": "views", "values": [{"value": 120}]},
                                                  {"name": "likes", "values": [{"value": 7}]}]})
    monkeypatch.setattr(pub.h, "request", fake_request)
    out = pub.insights("17890")
    assert out == {"post_id": "17890", "metrics": {"views": 120, "likes": 7, "replies": None, "reposts": None,
                                                   "quotes": None, "shares": None}}
    assert seen["path"] == "/v1.0/17890/insights" and "views" in seen["params"]["metric"] and LONG not in json.dumps(out)
    with pytest.raises(Exception, match="Connect again"):
        pub.insights("999")
    with pytest.raises(Exception, match="number"):
        pub.insights("../me")


def test_account_followers_and_replies(meta, monkeypatch):
    pub, _ = _ready(meta, [])
    seen = []

    def fake_request(host, method, path, headers, secrets, params=None, **k):
        seen.append((path, params))
        if path == "/v1.0/42/threads_insights":
            return httpx.Response(200, json={"data": [{"name": "followers_count", "total_value": {"value": 311}}]})
        return httpx.Response(200, json={"data": [{"id": 5, "username": "fan", "text": "do a Cocker Spaniel! " * 40,
                                                   "timestamp": "2026-10-07T12:00:00+0000"}]})
    monkeypatch.setattr(pub.h, "request", fake_request)
    acct = pub.insights()
    assert acct["account"] is True and acct["followers"] == 311 and acct["at"].endswith("Z")
    out = pub.replies("17890", limit=500)
    assert seen[-1][0] == "/v1.0/17890/replies" and seen[-1][1]["limit"] == 50 and "text" in seen[-1][1]["fields"]
    assert out["count"] == 1 and out["replies"][0]["username"] == "fan" and len(out["replies"][0]["text"]) <= 501
    assert LONG not in json.dumps(out)


def test_publish_stops_on_a_failed_container(meta):
    th, pub, _, rows, calls, state, mp = meta
    mp.setattr(pub.h, "require", lambda setting, label: None)
    mp.setenv("THREADS_ACCESS_TOKEN", LONG)
    mp.setenv("THREADS_USER_ID", "42")
    import time
    rows["THREADS_ACCESS_TOKEN"] = {"name": "THREADS_ACCESS_TOKEN", "updated": time.time()}
    state["container"] = "ERROR"
    with pytest.raises(Exception, match="couldn't prepare"):
        pub.publish("hi")
    assert not [c for c in calls if c.url.path.endswith("threads_publish")]


def test_topic_tag_goes_on_the_post_container_only(meta, monkeypatch):
    staged = []
    pub, calls = _ready(meta, staged)
    monkeypatch.setattr(pub, "_media", lambda n: (PNG_800, "image/png"))
    pub.publish("Which breed next?", image_names=["a.png", "b.png"], topic_tag="#Dogs   of Threads")
    made = [parse_qs(c.content.decode()) for c in calls if c.url.path == "/v1.0/42/threads"]
    assert [m.get("topic_tag") for m in made] == [None, None, ["Dogs of Threads"]]  # carousel parent only
    pub.publish("Plain text post", topic_tag="Dog lovers")
    last = parse_qs([c for c in calls if c.url.path == "/v1.0/42/threads"][-1].content.decode())
    assert last["media_type"] == ["TEXT"] and last["topic_tag"] == ["Dog lovers"]


def test_bad_topic_tag_posts_nothing(meta):
    staged = []
    pub, calls = _ready(meta, staged)
    before = len(calls)
    for bad in ("Kind Retro Co.", "Rock & Roll", "x" * 51):
        with pytest.raises(Exception, match="topic_tag"):
            pub.publish("hi", topic_tag=bad)
    assert len(calls) == before and not staged


def test_video_post_from_a_studio_video(meta, monkeypatch, tmp_path):
    staged = []
    pub, calls = _ready(meta, staged)
    monkeypatch.setattr(pub, "VIDEO_DIR", tmp_path)
    (tmp_path / "homeshed-x-reels.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 100)
    (tmp_path / "homeshed-x-reels.json").write_text('{"seconds": 42.0}', encoding="utf-8")
    out = pub.publish("HomeShed in 40 seconds", video_name="homeshed-x-reels", topic_tag="Self hosting")
    made = parse_qs(next(c for c in calls if c.url.path == "/v1.0/42/threads").content.decode())
    assert out["media_type"] == "VIDEO" and made["media_type"] == ["VIDEO"] and made["topic_tag"] == ["Self hosting"]
    assert made["video_url"][0].startswith("https://acct.r2.cloudflarestorage.com/b/threads/")
    assert staged[0][0] == "put" and staged[0][2] == "video/mp4" and staged[-1][0] == "delete"


def test_video_post_refusals_post_nothing(meta, monkeypatch, tmp_path):
    staged = []
    pub, calls = _ready(meta, staged)
    monkeypatch.setattr(pub, "VIDEO_DIR", tmp_path)
    (tmp_path / "long.mp4").write_bytes(b"v")
    (tmp_path / "long.json").write_text('{"seconds": 400}', encoding="utf-8")
    before = len(calls)
    for kw, msg in (({"video_name": "long"}, "up to 300"), ({"video_name": "missing"}, "no Studio video"),
                    ({"video_name": "../etc"}, "letters, numbers"),
                    ({"video_name": "long", "image_names": ["a.png"]}, "not both")):
        with pytest.raises(Exception, match=msg):
            pub.publish("x", **kw)
    assert len(calls) == before and not staged
