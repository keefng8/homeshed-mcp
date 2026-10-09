"""bugs.report / bugs.find / bugs.list: the same error is recognised again, fixed bugs stay fixed, nothing secret is
stored, and a broken store is refused rather than overwritten. The module was drafted by the local model; these tests
pin the four bugs found in that draft on review."""
import json

import pytest

import tools.bugs.known as kb


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    return tmp_path / "known_bugs.json"


def test_the_same_error_with_different_details_is_one_bug():
    first = kb.report("Deploy piped to tail hides failure", error_text="exit 1 at /opt/app/deploy.sh line 42")
    again = kb.report("x" * 5, error_text="exit 2 at /opt/other/deploy.sh line 7")
    assert again["matched"] and again["id"] == first["id"] and again["occurrences"] == 2
    assert again["title"] == "Deploy piped to tail hides failure"          # a repeat never renames the bug


def test_bugs_without_error_text_never_merge():
    a, b = kb.report("First bug with no error text"), kb.report("Second bug with no error text")
    assert a["id"] != b["id"] and not b["matched"]


def test_a_new_occurrence_keeps_a_fixed_bug_fixed():
    kb.report("RTK grep hang", error_text="rtk grep timed out after 120s", fix_status="fixed", fix="use the Grep tool")
    again = kb.report("RTK grep hang", error_text="rtk grep timed out after 300s")
    assert again["fix_status"] == "fixed" and again["fix"] == "use the Grep tool"


def test_find_puts_the_same_error_first_then_shared_words():
    kb.report("Unrelated", symptom="dashboard slow", tags=["dashboard"])
    target = kb.report("Memory recall empty", error_text="total_considered: 0 for session abc123def456",
                       root_cause="reads one session's history only")
    kb.report("Recall words", symptom="memory recall returns nothing")
    out = kb.find("total_considered: 0 for session 99ffee00aa11")["matches"]
    assert out[0]["id"] == target["id"] and out[0]["score"] == 100
    assert kb.find("memory recall nothing")["matches"][0]["title"] == "Recall words"


def test_nothing_secret_is_stored(store):
    fake = "github_pat_" + "11ABCDEFG0" + "a1B2c3D4e5" * 6                 # built at run time
    kb.report("Token in a command", error_text=f"git push https://x:{fake}@github.com/o/r.git failed",
              fix="export MEMORY_CORE_BEARER=" + "ab12" * 12 + " was typed in")
    text = store.read_text(encoding="utf-8")
    assert fake not in text and "ab12ab12" not in text and "[hidden]" in text


def test_ordinary_words_are_not_mistaken_for_keys():
    assert kb.redact("mask-rcnn and task-runner are fine") == "mask-rcnn and task-runner are fine"


def test_a_broken_store_is_refused_not_overwritten(store):
    store.write_text("{not json", encoding="utf-8")
    with pytest.raises(kb.BugsError, match="unreadable"):
        kb.report("Should not be written", error_text="x")
    assert store.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("call, says", [
    (lambda: kb.report("no"), "3 to 120"),
    (lambda: kb.report("A real title", fix_status="done"), "fix_status"),
    (lambda: kb.find(""), "describe"),
    (lambda: kb.find("x", k=50), "1 to 20"),
    (lambda: kb.list_bugs(limit=0), "1 to 100"),
])
def test_bad_input_gives_a_plain_error(call, says):
    with pytest.raises(kb.BugsError, match=says):
        call()


def test_list_filters_and_counts_occurrences():
    kb.report("Open one", error_text="a failure 1")
    kb.report("Fixed one", error_text="b failure 1", fix_status="fixed")
    kb.report("Fixed one", error_text="b failure 2")
    out = kb.list_bugs(fix_status="fixed", detail="full")
    assert out["count"] == 1 and out["bugs"][0]["occurrences"] == 2 and "fingerprint" in out["bugs"][0]
    assert json.dumps(out)  # plain JSON all the way through


def test_the_list_is_compact_unless_asked():
    words = "the deploy hid its failure behind tail " * 14  # real sentences: a long unbroken run is redacted as a secret
    kb.report("A long one", symptom=words, root_cause=words, workaround=words)
    [b] = kb.list_bugs()["bugs"]
    assert set(b) <= set(kb.COMPACT) and {"id", "title", "fix_status", "occurrences"} <= set(b)
    assert len(json.dumps(b)) < 300 and len(json.dumps(kb.list_bugs(detail="full")["bugs"][0])) > 1500
    with pytest.raises(kb.BugsError, match="compact"):
        kb.list_bugs(detail="all")


def test_a_bug_is_updated_by_id_when_it_has_no_error_text():
    first = kb.report("Router says local for everything", symptom="scores 5-18 vs 35")
    fixed = kb.report("Router says local for everything", id=first["id"], fix_status="fixed", fix="judgement signal")
    # marking a fix is an edit, not the bug happening again
    assert fixed["id"] == first["id"] and fixed["fix_status"] == "fixed" and fixed["occurrences"] == 1
    with pytest.raises(kb.BugsError, match="isn't a known bug"):
        kb.report("Nope", id="KB-9999")


# --- Shedkeeper semantic fallback (2026-09-29) ---

def test_shedkeeper_moves_a_paraphrased_match_to_the_top(monkeypatch):
    kb.report("Memory recall returns nothing", error_text="total_considered: 0")
    target = kb.report("Router says local for everything", symptom="routing sends security work to the local model")
    monkeypatch.setattr(kb, "_shedkeeper_pick", lambda query, cands, bugs: (target["id"], "strong"))
    out = kb.find("the model picker keeps choosing the cheap option for risky jobs")["matches"]
    assert out[0]["id"] == target["id"] and out[0]["matched_by"] == "shedkeeper" and out[0]["score"] == 60


def test_without_shedkeeper_or_when_unsure_nothing_changes(monkeypatch):
    kb.report("Memory recall returns nothing", error_text="total_considered: 0")
    monkeypatch.setattr(kb, "_shedkeeper_pick", lambda query, cands, bugs: None)
    out = kb.find("memory recall")["matches"]
    assert "matched_by" not in out[0]


def test_an_exact_error_never_asks_shedkeeper(monkeypatch):
    kb.report("Memory recall returns nothing", error_text="total_considered: 0 for session 0a1b2c3d4e5f")
    monkeypatch.setattr(kb, "_shedkeeper_pick", lambda query, cands, bugs: (_ for _ in ()).throw(AssertionError("asked Shedkeeper")))
    assert kb.find("total_considered: 0 for session 9f8e7d6c5b4a")["matches"][0]["score"] == 100


def test_a_clear_leader_is_shown_as_a_possible_match(monkeypatch):
    """Live 2026-09-29: the right bug 0.28, the next 0.08, "none" 0.48 -> shown as possible, not certain."""
    d = pytest.importorskip("tools.local_ai.decide")  # the public copy has no Shedkeeper (a companion service)
    kb.report("Memory recall returns nothing", symptom="every search finds zero results")
    other = kb.report("Deploy piped to tail hides failure")
    target_id = [b for b in kb.list_bugs()["bugs"] if b["title"].startswith("Memory")][0]["id"]
    monkeypatch.setattr(d, "_shedkeeper_url", lambda: "http://shedkeeper")
    monkeypatch.setattr(d, "_shedkeeper_choice", lambda url, state, instr, crit: {
        "choice": "none", "probabilities": {target_id: 0.28, other["id"]: 0.08, "none": 0.48}})
    top = kb.find("my assistant forgets everything, zero results")["matches"][0]
    assert top["id"] == target_id and top["matched_by"] == "shedkeeper-possible" and top["score"] == 45


def test_a_bug_id_finds_that_bug():
    """A guard's block message says bugs.find("KB-0031") (2026-09-29): the id alone returns that bug."""
    bug = kb.report("Doubled backslashes collapse in inline code", workaround="Write the file with the Write tool.")
    for query in (bug["id"], bug["id"].lower(), f"  {bug['id']} "):
        [m] = kb.find(query)["matches"]
        assert m["id"] == bug["id"] and m["score"] == 100 and m["workaround"] == "Write the file with the Write tool."
    assert kb.find("KB-9999")["matches"] == []
