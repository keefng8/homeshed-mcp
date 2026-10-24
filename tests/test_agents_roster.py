import pytest

import agents_roster as ar


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "STORE_FILE", tmp_path / "roster.json")


def test_an_agent_adds_itself_and_waits_for_the_owner():
    assert ar.set_row("Environment setup", "library builder", "cloud", "d-projects")["status"].startswith("added")
    [row] = ar.roster()["agents"]
    assert (row["name"], row["role"], row["reach"], row["confirmed"], row["by"]) == (
        "Environment setup", "library builder", "cloud", False, "d-projects")
    assert ar.waiting() == [row]
    assert ar.brief() == "- Environment setup: library builder (cloud, unconfirmed)"


def test_the_owners_wording_wins_over_a_later_change():
    ar.set_row("Helper one", "site builder", "session", "helper")
    ar.decide("helper one", "confirm", role="site builder (web)")
    out = ar.set_row("Helper one", "repo audit only", "session", "helper")
    assert out["status"].startswith("proposed")
    [row] = ar.roster()["agents"]
    assert row["role"] == "site builder (web)" and row["proposed"]["role"] == "repo audit only"
    assert ar.brief() == "- Helper one: site builder (web) (session)"  # the confirmed words, until the owner says
    ar.decide("Helper one", "confirm")
    [row] = ar.roster()["agents"]
    assert row["role"] == "repo audit only" and "proposed" not in row and ar.waiting() == []


def test_decline_drops_a_proposal_but_keeps_the_confirmed_row():
    ar.set_row("Director bot", "runs the other bots", "session", "director")
    ar.decide("Director bot", "confirm")
    ar.set_row("Director bot", "something else", "session", "x")
    ar.decide("Director bot", "decline")
    [row] = ar.roster()["agents"]
    assert row["role"] == "runs the other bots" and row["confirmed"] and "proposed" not in row
    ar.set_row("Stray", "never confirmed", "mailbox", "x")
    ar.decide("Stray", "decline")
    assert [r["name"] for r in ar.roster()["agents"]] == ["Director bot"]


def test_same_words_only_mark_it_seen_and_old_rows_show_gone():
    ar.set_row("Director bot", "runs the other bots", "session", "director", now=1000.0)
    assert ar.set_row("director bot", "runs the other bots", "session", "director", now=2000.0)["status"] == "unchanged"
    [row] = ar.roster(now=2000.0 + 8 * 86400)["agents"]
    assert row["gone"] and row["updated"] == 2000.0


@pytest.mark.parametrize("name, role, reach", [("", "x", "session"), ("a", "", "session"), ("a", "x", "email"),
                                               ("a" * 61, "x", "session"), ("a", "x" * 201, "session")])
def test_bad_rows_are_refused(name, role, reach):
    with pytest.raises(ar.RosterError):
        ar.set_row(name, role, reach, "t")


def test_control_characters_are_removed_and_remove_works():
    ar.set_row("App‮ builder", "the app\nand website", "session", "t")
    [row] = ar.roster()["agents"]
    assert (row["name"], row["role"]) == ("App builder", "the app and website")
    ar.decide("App builder", "remove")
    assert ar.roster()["count"] == 0
    with pytest.raises(ar.RosterError):
        ar.decide("App builder", "remove")


def test_the_roster_is_bounded(monkeypatch):
    monkeypatch.setattr(ar, "MAX_ROWS", 2)
    ar.set_row("a", "x", "session", "t")
    ar.set_row("b", "x", "session", "t")
    with pytest.raises(ar.RosterError):
        ar.set_row("c", "x", "session", "t")
