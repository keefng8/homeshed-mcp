"""web.watch.check: first look, two checks agreeing before an alert, ignore patterns, trigger text, small changes;
web.watch makes a when_flagged schedule. web.read is replaced by a fake page."""
from __future__ import annotations

import pytest

import runtime_settings as rs
from tools.schedule import engine as eng
from tools.web import watch as ww


@pytest.fixture
def page(monkeypatch, tmp_path):
    import registry
    registry.discover()
    monkeypatch.setenv("WEB_WATCH_FILE", str(tmp_path / "watch.json"))
    monkeypatch.setenv("SCHEDULES_FILE", str(tmp_path / "schedules.json"))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    state = {"text": "Blue mug\nPrice: 12.00\nOut of stock\nUpdated 3 min ago"}
    monkeypatch.setattr(ww, "web_read", lambda url: {"url": "https://shop.example.com/mug", "content": state["text"]})
    return state


def check(**kw):
    return ww.check("shop.example.com/mug", **kw)


def test_first_look_then_same(page):
    assert check()["status"] == "first_look"
    out = check()
    assert out["status"] == "same" and out["alert"] is False


def test_a_change_alerts_only_when_the_next_check_agrees(page):
    check()
    page["text"] = page["text"].replace("12.00", "9.50 SALE now on")
    once = check()
    assert once["status"] == "changed_once" and once["alert"] is False
    twice = check()
    assert twice["status"] == "changed" and twice["alert"] is True
    assert "Price: 9.50 SALE now on" in twice["added"] and "Price: 12.00" in twice["removed"]
    assert check()["status"] == "same"  # the new version is the baseline now


def test_a_one_off_odd_load_never_alerts(page):
    check()
    good = page["text"]
    page["text"] = "Service temporarily unavailable, please retry later"
    assert check()["status"] == "changed_once"
    page["text"] = good
    assert check()["status"] == "same"


def test_ignored_lines_and_small_changes_dont_alert(page):
    check(ignore=r"min ago")
    page["text"] = page["text"].replace("3 min ago", "9 min ago")
    assert check(ignore=r"min ago")["status"] == "same"
    page["text"] = page["text"].replace("Blue mug", "Blue mugs")
    check(ignore=r"min ago")
    out = check(ignore=r"min ago")
    assert out["status"] == "changed" and out["alert"] is False  # 1 character: under min_change_chars


def test_trigger_text_alerts_only_when_it_appears(page):
    check(trigger="in stock")
    page["text"] = page["text"].replace("Price: 12.00", "Price: 13.00 new price today")
    check(trigger="in stock")
    assert check(trigger="in stock")["alert"] is False
    page["text"] = page["text"].replace("Out of stock", "In stock now")
    check(trigger="in stock")
    out = check(trigger="in stock")
    assert out["alert"] is True and '"in stock" is now on' in out["summary"]


def test_bad_options(page):
    with pytest.raises(ww.WatchError, match="valid pattern"):
        check(ignore="(")
    with pytest.raises(ww.WatchError, match="min_change_chars"):
        check(min_change_chars=-1)


def test_watch_makes_a_flagged_schedule_and_alerts_through_it(page, monkeypatch):
    alerts = []
    monkeypatch.setattr(eng, "_alert", lambda title, msg, priority="default": alerts.append((title, msg)))
    s = ww.watch("shop.example.com/mug", every="every 30 minutes", trigger="in stock")
    assert s["capability"] == "web.watch.check" and s["notify"] == "when_flagged" and "First look" in s["first_look"]
    page["text"] = page["text"].replace("Out of stock", "In stock")
    eng.run_one(s["id"])
    assert alerts == []
    eng.run_one(s["id"])
    assert len(alerts) == 1 and '"in stock" is now on' in alerts[0][1]
