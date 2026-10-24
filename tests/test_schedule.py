"""schedule.*: parsing "when", only read-risk tools, runs as the maker, failure pause, alerts, the real scheduler's
triggers. Runs call run_one directly; the scheduler is started with a temp store and its jobs are only inspected."""
from __future__ import annotations

import pytest

import clients
import runtime_settings as rs
from tools.schedule import engine as eng
from tools.schedule import tools as st


@pytest.fixture
def store(monkeypatch, tmp_path):
    import registry
    registry.discover()  # the real tools, so a schedule names one that exists
    monkeypatch.setenv("SCHEDULES_FILE", str(tmp_path / "schedules.json"))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    alerts: list[tuple] = []
    monkeypatch.setattr(eng, "_alert", lambda title, msg, priority="default": alerts.append((title, msg, priority)))
    yield alerts
    eng.stop()


@pytest.mark.parametrize("when, kind, extra", [
    ("daily 08:00", "cron", {"hour": "8", "minute": "0", "day_of_week": "*"}),
    ("Weekdays 7:30", "cron", {"hour": "7", "minute": "30", "day_of_week": "mon-fri"}),
    ("weekends 10:00", "cron", {"day_of_week": "sat,sun"}),
    ("weekly monday 09:15", "cron", {"day_of_week": "mon", "minute": "15"}),
    ("monthly 1 09:00", "cron", {"day": "1"}),
    ("hourly", "cron", {"minute": "0", "hour": "*"}),
    ("every 30 minutes", "interval", {"minutes": 30}),
    ("every 2 hours", "interval", {"minutes": 120}),
    ("0 8 * * 1-5", "cron", {"minute": "0", "hour": "8", "day_of_week": "1-5"}),
])
def test_when_in_plain_words_or_cron(when, kind, extra):
    f = eng.parse_when(when)
    assert f["kind"] == kind and extra.items() <= f.items()


@pytest.mark.parametrize("when, says", [
    ("every 5 minutes", "15 minutes"), ("* * * * *", "15 minutes"), ("*/5 * * * *", "15 minutes"),
    ("", "empty"), ("at teatime", "isn't a time"), ("daily 25:00", "isn't a time"), ("monthly 31 09:00", "isn't a time"),
    ("0 99 * * *", "cron line doesn't work"), ("every 9 hours" * 1, None),
])
def test_bad_whens_are_refused(when, says):
    if says is None:
        assert eng.parse_when(when)["minutes"] == 540
        return
    with pytest.raises(eng.ScheduleError, match=says):
        eng.parse_when(when)


def test_write_tools_and_schedule_tools_are_refused(store):
    with pytest.raises(eng.ScheduleError, match="changes something real"):
        st.add("Post daily", "notify.send", "daily 08:00", {"message": "hi"})
    with pytest.raises(eng.ScheduleError, match="schedule tools"):
        st.add("Loop", "schedule.list", "daily 08:00")
    with pytest.raises(eng.ScheduleError, match="no tool called"):
        st.add("Typo", "system.helth", "daily 08:00")
    with pytest.raises(eng.ScheduleError, match="notify.send changes something real"):
        st.add("Sneaky", "workflow.run", "daily 08:00", {"steps": [{"capability": "system.info"},
                                                                     {"capability": "notify.send"}]})
    assert st.list_schedules()["count"] == 0


def test_add_list_pause_remove(store):
    s = st.add("Morning check", "system.info", "daily 08:00", notify="never")
    assert s["id"].startswith("s-") and s["paused"] is False and s["owner"] is None
    assert [r["id"] for r in st.list_schedules()["schedules"]] == [s["id"]]
    assert st.pause(s["id"])["paused"] is True
    assert st.pause(s["id"], paused=False)["paused"] is False
    assert st.remove(s["id"])["removed"] == s["id"]
    with pytest.raises(eng.ScheduleError, match="no schedule"):
        st.remove(s["id"])


def test_a_client_sees_only_its_own_and_runs_as_itself(store, monkeypatch):
    mine = st.add("Owner's", "system.info", "daily 08:00", notify="never")
    tok = clients.current_client.set("shop-bot")
    try:
        theirs = st.add("Bot's", "system.info", "daily 09:00", notify="never")
        assert [r["id"] for r in st.list_schedules()["schedules"]] == [theirs["id"]]
        with pytest.raises(eng.ScheduleError, match="no schedule"):
            st.remove(mine["id"])
    finally:
        clients.current_client.reset(tok)
    assert st.list_schedules()["count"] == 2  # the owner sees both
    seen = []
    from registry import get_capability
    cap = get_capability("system.info")
    monkeypatch.setattr(cap, "func", lambda **kw: seen.append(clients.current_client.get()) or {"ok": 1})
    eng.run_one(theirs["id"])
    eng.run_one(mine["id"])
    assert seen == ["shop-bot", None] and clients.current_client.get() is None


def test_three_failures_pause_it_with_one_alert(store, monkeypatch):
    s = st.add("Flaky", "system.info", "daily 08:00", notify="never")
    from registry import get_capability
    cap = get_capability("system.info")

    def boom(**kw):
        raise RuntimeError("service down")
    monkeypatch.setattr(cap, "func", boom)
    for _ in range(3):
        eng.run_one(s["id"])
    row = st.list_schedules()["schedules"][0]
    assert row["paused"] is True and "3 failures in a row" in row["paused_why"] and row["runs"] == 3
    assert row["history"][-1]["ok"] is False and "service down" in row["history"][-1]["error"]
    assert len(store) == 1 and store[0][2] == "high"
    assert eng.run_one(s["id"]) is None  # paused: nothing runs
    assert st.pause(s["id"], paused=False)["fails_in_a_row"] == 0


def test_on_change_alerts_only_when_the_result_changes(store, monkeypatch):
    s = st.add("Watch", "system.info", "every 2 hours")  # notify defaults to on_change
    from registry import get_capability
    cap = get_capability("system.info")
    answers = iter([{"v": 1}, {"v": 1}, {"v": 2}])
    monkeypatch.setattr(cap, "func", lambda **kw: next(answers))
    for _ in range(3):
        eng.run_one(s["id"])
    assert len(store) == 1 and "changed" in store[0][0] and '"v": 2' in store[0][1]
    row = st.list_schedules()["schedules"][0]
    assert row["runs"] == 3 and "result" not in str(row["history"])  # only summaries are kept


def test_the_scheduler_arms_jobs_in_the_owners_time_zone(store):
    rs.update({"schedule_timezone": "Europe/London"})
    eng.start()
    s = st.add("Morning", "system.info", "daily 08:00", notify="never")
    job = eng._sched.get_job(s["id"])
    assert str(job.trigger.timezone) == "Europe/London" and job.max_instances == 1 and job.coalesce is True
    assert job.misfire_grace_time == eng.MISFIRE_S and s["next_run"].endswith(("+00:00", "+01:00"))
    st.pause(s["id"])
    assert eng._sched.get_job(s["id"]) is None
    st.pause(s["id"], paused=False)
    rs.update({"schedule_timezone": "Asia/Tokyo"})
    st.list_schedules()  # follows the new zone
    assert str(eng._sched.get_job(s["id"]).trigger.timezone) == "Asia/Tokyo"


def test_the_limit(store, monkeypatch):
    monkeypatch.setattr(eng, "MAX_SCHEDULES", 2)
    st.add("a", "system.info", "daily 08:00")
    st.add("b", "system.info", "daily 09:00")
    with pytest.raises(eng.ScheduleError, match="2 schedules already"):
        st.add("c", "system.info", "daily 10:00")
