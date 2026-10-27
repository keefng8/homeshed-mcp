"""Known bugs shared with the HomeShed Library (capabilities/bugs/sync-contract.md): the merge rules, no echo, a
library that's down costs nothing, and nothing is sent when no library is linked."""
import pytest

import runtime_settings
import vault
import tools.bugs.exchange as ex
import tools.bugs.known as kb
import tools.bugs.seen as seen

ERR = "ModuleNotFoundError: No module named 'httpx'"


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    monkeypatch.setattr(ex, "STATE_FILE", tmp_path / "bug_exchange.json")
    monkeypatch.setattr(seen, "SEEN_FILE", tmp_path / "bug_candidates.json")
    monkeypatch.setattr(ex, "soon", lambda only_if_stale=False: None)  # no background threads in tests
    monkeypatch.setattr(runtime_settings, "get", lambda key: "http://library.test:8770" if key == "library_url" else None)
    monkeypatch.setattr(vault, "secret", lambda name: "hll_test" if name == "LIBRARY_BUGS_KEY" else "")
    return tmp_path


def lib(origin_id="dev.module-missing", **over):
    return {"origin": "library", "origin_id": origin_id, "title": "Module not installed", "error_text": ERR,
            "fix": "pip install it", "fix_status": "workaround-only", "rev": 1, "occurrences": {"library": 4}, **over}


def test_a_library_bug_is_added_and_found():
    bugs = {}
    bid = ex.merge(bugs, lib())
    assert bid == "LIB-dev.module-missing" and bugs[bid]["remote_occurrences"] == {"library": 4}
    assert bugs[bid]["fingerprint"] == kb.fingerprint(ERR)


def test_the_same_error_from_the_library_merges_into_ours():
    mine = kb.report("httpx missing", error_text=ERR, fix_status="open")
    bugs = kb._load()
    assert ex.merge(bugs, lib()) == mine["id"]
    b = bugs[mine["id"]]
    assert b["fix_status"] == "workaround-only"          # status only moves forward
    assert b["title"] == "httpx missing"                  # same rev: our text stays
    assert b["conflicts"] == [{"title": "Module not installed", "fix": "pip install it"}]  # theirs kept for the owner


def test_status_never_goes_back_and_higher_rev_text_wins():
    bugs = {}
    bid = ex.merge(bugs, lib(fix_status="fixed"))
    ex.merge(bugs, lib(fix_status="open", rev=2, fix="uv pip install it"))
    assert bugs[bid]["fix_status"] == "fixed" and bugs[bid]["fix"] == "uv pip install it"


def test_counts_are_per_store_and_never_doubled():
    mine = kb.report("httpx missing", error_text=ERR)
    bugs = kb._load()
    for _ in range(3):  # the same library record arriving again changes nothing
        ex.merge(bugs, lib(occurrences={"library": 4, "homeshed": 99}))
    assert bugs[mine["id"]]["remote_occurrences"] == {"library": 4}  # our own count is never taken from them
    assert ex.record(bugs[mine["id"]])["occurrences"] == {"library": 4, "homeshed": 1}


def test_a_tombstone_wins_at_an_equal_rev_and_hides_the_bug():
    bugs = {}
    bid = ex.merge(bugs, lib())
    ex.merge(bugs, lib(deleted=True))
    kb._save(bugs)
    assert bugs[bid]["deleted"] and kb.find("ModuleNotFoundError httpx")["matches"] == []
    assert ex.merge({}, lib(deleted=True)) is None  # never added just to be deleted


def test_secrets_are_cleaned_on_the_way_in():
    bugs = {}
    bid = ex.merge(bugs, lib(fix="export TOKEN=abcdef123456 then retry"))
    assert "abcdef123456" not in bugs[bid]["fix"]


def test_an_exchange_sends_changes_once_and_never_echoes_what_came_back():
    kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use")
    calls = []

    def post(url, key, pack):
        calls.append(pack)
        return {"bugs": [lib()], "cursor": "c1"}
    assert ex.exchange(post) == {"ok": True, "sent": 1, "received": 1}
    assert calls[0]["formatVersion"] == 2 and calls[0]["from"] == "homeshed" and calls[0]["since"] is None
    assert [b["origin_id"] for b in calls[0]["bugs"]] == ["KB-0001"]
    assert ex.exchange(post)["sent"] == 0              # nothing changed here, and the library's bug isn't sent back
    assert calls[1]["since"] == "c1"


def test_a_new_count_here_is_sent_though_the_text_is_the_same():
    mine = kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use")
    post = lambda u, k, p: {"bugs": []}  # noqa: E731
    assert ex.exchange(post)["sent"] == 1
    bugs = kb._load()
    bugs[mine["id"]]["occurrences"].append({"session": "s2"})  # it happened again here, in another session
    kb._save(bugs)
    assert ex.exchange(post)["sent"] == 1 and ex.exchange(post)["sent"] == 0


def test_a_record_the_library_skipped_is_sent_again():
    mine = kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use")
    assert ex.exchange(lambda u, k, p: {"bugs": [], "skipped": {mine["id"]: "card too long"}})["sent"] == 1
    assert ex.exchange(lambda u, k, p: {"bugs": []})["sent"] == 1  # sent again, and now stored
    assert ex.exchange(lambda u, k, p: {"bugs": []})["sent"] == 0


def test_a_library_that_is_down_costs_nothing_and_its_changes_go_next_time():
    kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use")

    def down(url, key, pack):
        raise OSError("connection refused")
    out = ex.exchange(down)
    assert out["ok"] is False and out["pending"] == 1
    assert ex.exchange(lambda u, k, p: {"bugs": []})["sent"] == 1


def test_nothing_is_sent_without_a_linked_library(monkeypatch):
    monkeypatch.setattr(runtime_settings, "get", lambda key: None)
    assert "skipped" in ex.exchange(lambda u, k, p: pytest.fail("sent with no library linked"))


def test_a_fixed_bug_that_comes_back_is_counted_as_regressed():
    kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use", fix_status="fixed")
    kb.report("x" * 5, error_text="OSError: [Errno 98] Address already in use")
    [m] = kb.find("OSError: [Errno 98] Address already in use")["matches"]
    assert m["fix_status"] == "fixed" and m["regressed"] == 1


def test_an_edit_raises_the_rev_and_a_bare_repeat_doesnt():
    first = kb.report("Port in use", error_text="OSError: [Errno 98] Address already in use")
    kb.report("x" * 5, error_text="OSError: [Errno 98] Address already in use")
    assert kb._load()[first["id"]]["rev"] == 1
    kb.report("x" * 5, id=first["id"], fix="stop the old server")
    assert kb._load()[first["id"]]["rev"] == 2


def test_the_catches_counter_rides_along_and_the_reply_is_kept(tmp_path):
    packs = []
    ex.exchange(lambda url, key, pack: packs.append(pack) or {"cursor": "c1", "catches": "kept"})
    assert packs[0]["catches"]["week"] == 0 and packs[0]["since"] is None
    assert ex._state()["catches"] == "kept"
    (tmp_path / "bug_candidates.json").write_text("not json", encoding="utf-8")
    ex.exchange(lambda url, key, pack: packs.append(pack) or {})
    assert "catches" not in packs[1] and packs[1]["since"] == "c1"
