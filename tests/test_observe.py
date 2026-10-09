"""observe.* store tests. Case list first-drafted by local_ai.ask (qwen-coder), then corrected:
the draft's fixture didn't take monkeypatch and it asserted exact timestamps."""
from __future__ import annotations

import pytest

from tools.observe import _store


@pytest.fixture(autouse=True)
def obs_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVATIONS_DIR", str(tmp_path))
    return tmp_path


def test_ids_increment_and_file_created(obs_dir):
    a = _store.create("First thing", "issue a")
    b = _store.create("Second thing", "issue b")
    assert (a["id"], b["id"]) == ("0001", "0002")
    assert (obs_dir / a["file"]).exists()
    assert a["file"] == "0001-first-thing.md"
    assert a["status"] == "open" and a["escalate"] is False and a["hint"] is None


@pytest.mark.parametrize("title,issue", [("", "x"), ("   ", "x"), ("t", ""), ("t", "  \n")])
def test_empty_title_or_issue_raises(title, issue):
    with pytest.raises(ValueError):
        _store.create(title, issue)


def test_second_same_class_escalates():
    first = _store.create("t1", "i", action_class="shell.grep-command")
    second = _store.create("t2", "other words", action_class="shell.grep-command")
    assert first["repeat_count"] == 0 and first["escalate"] is False
    assert second["repeat_count"] == 1 and second["escalate"] is True and second["class"] == "shell.grep-command"
    assert "RULE-D-PROJECTS-007" in second["hint"]


def test_a_shared_rule_label_alone_never_counts_as_a_repeat():
    """R&D's rules review v2: RULE-009 was a catch-all, so unrelated slips shared its count."""
    _store.create("grep in bash", "used grep in the shell", rule="RULE-D-PROJECTS-009")
    assert _store.create("env printed", "cat of a dot env file", rule="RULE-D-PROJECTS-009")["escalate"] is False


def test_declined_does_not_count_toward_repeats():
    _store.create("t1", "i", action_class="c.one")
    _store.update("0001", "declined")
    again = _store.create("t2", "i", action_class="c.one")
    assert again["repeat_count"] == 0 and again["escalate"] is False


def test_actioned_still_counts_and_says_its_fix_did_not_hold():
    # A slip back after its "fix" landed is exactly the case that needs escalation (R&D: not a new one-off).
    _store.create("t1", "i", action_class="c.one")
    _store.update("1", "actioned", resolution="reworded the rule")
    again = _store.create("t2", "i", action_class="c.one")
    assert again["escalate"] is True and again["bypassed"] == ["0001"] and "#0001 didn't hold" in again["hint"]


def test_different_words_never_escalate():
    _store.create("t1", "the deploy forgot a file")
    assert _store.create("t2", "a peer message ran long")["escalate"] is False


def test_the_same_words_join_the_earlier_class():
    """No class given: 3-word Jaccard 0.6 or more with an earlier observation makes it the same slip."""
    issue = "Claude ran grep as a shell command instead of the Grep tool and RTK hung for minutes"
    first = _store.create("grep in the shell", issue)
    again = _store.create("grep in the shell again", issue + " again")
    assert first["class"] == "obs-0001" and again["class"] == "obs-0001" and again["escalate"] is True


@pytest.mark.parametrize("rule, kept", [("RULE-D-PROJECTS-009", True), ("RULE-GLOBAL-009", True), ("CORE-NO-POLLING", True),
                                        ("15", False), ("#0002 Grunt work goes local", False), ("rule 9", False)])
def test_only_a_rule_id_is_kept_as_the_rule(obs_dir, rule, kept):
    out = _store.create("t", "i", rule=rule)
    listed = _store.list_all()["observations"][0]
    assert (listed["rule"] == rule) is kept and (listed["rule"] == "") is not kept
    assert ("## Rule given" in (obs_dir / out["file"]).read_text(encoding="utf-8")) is not kept


def test_bad_class_raises():
    with pytest.raises(ValueError, match="action_class"):
        _store.create("t", "i", action_class="Not A Class!")


def test_guard_stops_make_the_class_observation_once_a_day(obs_dir):
    first = _store.record_repeat("shell.grep-command", "2026-10-01", 9, sessions=3, retries=1, guards=["shell.grep-command"])
    assert first["created"] is True and first["stops"] == 9
    assert _store.record_repeat("shell.grep-command", "2026-10-01", 9)["unchanged"] is True   # the same day again
    second = _store.record_repeat("shell.grep-command", "2026-10-02", 4, sessions=2, retries=0)
    assert second == {"id": first["id"], "created": False, "status": "open", "stops": 13, "reopened": False}
    text = next(obs_dir.glob(f"{first['id']}-*.md")).read_text(encoding="utf-8")
    assert "- 2026-10-01: stopped 9 times in 3 session(s), 1 retried (shell.grep-command)" in text
    assert "- 2026-10-02: stopped 4 times" in text and "last_seen: 2026-10-02" in text and "source: self-review" in text


def test_guard_stops_count_toward_a_logged_repeat():
    """RULE-D-PROJECTS-007 fires without anyone having logged the slip before."""
    _store.record_repeat("shell.grep-command", "2026-10-01", 5)
    out = _store.create("grep again", "i", action_class="shell.grep-command")
    assert out["repeat_count"] == 6 and out["escalate"] is True


def test_an_actioned_class_reopens_when_its_stops_are_mostly_retries():
    first = _store.record_repeat("c.nag", "2026-10-01", 4)
    _store.update(first["id"], "actioned", resolution="guard added")
    assert _store.record_repeat("c.nag", "2026-10-02", 6, retries=2)["reopened"] is False    # it teaches
    again = _store.record_repeat("c.nag", "2026-10-03", 6, retries=3)
    assert again["reopened"] is True and again["status"] == "open"


@pytest.mark.parametrize("bad", [dict(action_class="", date="2026-10-01", stops=1),
                                 dict(action_class="c.x", date="1 Oct", stops=1),
                                 dict(action_class="c.x", date="2026-10-01", stops=0),
                                 dict(action_class="c.x", date="2026-10-01", stops=2, retries=3)])
def test_record_repeat_refuses_bad_input(bad):
    with pytest.raises(ValueError):
        _store.record_repeat(**bad)


@pytest.mark.parametrize("word, status", [("fixed", "actioned"), ("Done", "actioned"), ("wontfix", "declined"),
                                          ("duplicate", "superseded")])
def test_status_words_mean_their_status(word, status):
    _store.create("t", "i")
    assert _store.update("1", word)["status"] == status


def test_list_filters_by_status_and_rule():
    _store.create("a", "i", rule="RULE-A-001")
    _store.create("b", "i", rule="RULE-B-002")
    _store.update("0002", "parked")
    assert [o["title"] for o in _store.list_all()["observations"]] == ["a"]
    assert [o["title"] for o in _store.list_all(status="parked")["observations"]] == ["b"]
    assert _store.list_all(status="all")["count"] == 2
    assert [o["title"] for o in _store.list_all(status="all", rule="RULE-B-002")["observations"]] == ["b"]
    assert [o["title"] for o in _store.list_all(status="all", action_class="obs-0002")["observations"]] == ["b"]


def test_list_limit_keeps_full_count():
    for n in range(3):
        _store.create(f"t{n}", "i")
    out = _store.list_all(limit=1)
    assert out["count"] == 3 and len(out["observations"]) == 1


def test_invalid_status_raises():
    with pytest.raises(ValueError):
        _store.list_all(status="bogus")
    _store.create("t", "i")
    with pytest.raises(ValueError):
        _store.update("0001", "all")


def test_update_changes_status_and_appends_resolution(obs_dir):
    _store.create("t", "i")
    out = _store.update("0001", "actioned", resolution="added a hook")
    assert out == {"id": "0001", "status": "actioned", "previous_status": "open"}
    text = (obs_dir / "0001-t.md").read_text(encoding="utf-8")
    assert "status: actioned" in text and "## Resolution (actioned," in text and "added a hook" in text
    assert "## Issue\ni" in text  # original body preserved


def test_update_unknown_id_raises():
    with pytest.raises(ValueError, match="no observation"):
        _store.update("0042", "actioned")


def test_newlines_in_frontmatter_fields_are_sanitized():
    _store.create("line one\nstatus: actioned", "i", rule="R\n1")
    listed = _store.list_all()["observations"][0]
    assert listed["title"] == "line one status: actioned"
    assert listed["status"] == "open"  # injected frontmatter line didn't take effect
    assert listed["rule"] == ""  # "R 1" isn't a rule id: kept in the body, not the field


def test_issue_body_with_dashes_does_not_break_parsing():
    _store.create("t", "before\n---\nafter")
    assert _store.list_all()["observations"][0]["title"] == "t"


def test_summary_counts():
    _store.create("a", "i")
    _store.create("b", "i")
    _store.update("0001", "actioned")
    assert _store.summary() == {"open": 1, "by_status": {"actioned": 1, "open": 1}, "open_titles": ["b"]}


def test_capability_wrappers_registered():
    import tools.observe.list as list_mod
    import tools.observe.log as log_mod
    import tools.observe.update as update_mod

    out = log_mod.log(title="t", issue="i", rule="R")
    assert out["id"] == "0001"
    assert list_mod.list_observations()["count"] == 1
    assert update_mod.update(id="1", status="parked")["previous_status"] == "open"


def test_the_repeat_route_records_a_day_and_refuses_bad_input(monkeypatch):
    """POST /observations/repeat: the daily self-review's door into the store (counts and ids only)."""
    import asyncio
    import importlib
    import json
    import sys

    monkeypatch.setenv("MCP_AUTH_TOKEN", "owner-secret")  # secret-scan: allow (fake)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "localhost")
    sys.modules.pop("server", None)
    server = importlib.import_module("server")

    class Req:
        def __init__(self, body):
            self._body = body

        async def json(self):
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    def post(body):
        resp = asyncio.run(server.observations_repeat(Req(body)))
        return resp.status_code, json.loads(resp.body)

    status, out = post({"class": "shell.grep-command", "date": "2026-10-01", "stops": 7, "sessions": 2, "retries": 1,
                        "guards": ["shell.grep-command"]})
    assert status == 200 and out["created"] is True and out["stops"] == 7
    for bad in ([1, 2], {"class": "x y", "date": "2026-10-01", "stops": 1}, {"class": "c.x", "date": "2026-10-01"},
                ValueError("not json")):
        assert post(bad)[0] == 400
