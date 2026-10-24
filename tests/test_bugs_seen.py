"""bugs.seen: a failure is sorted (noise / misuse / platform), only platform failures seen in 2 different sessions are
suggested, a known bug gets one occurrence per session, and the owner's keep makes a real known bug."""
import json

import pytest

import runtime_settings
import tools.bugs.known as kb
import tools.bugs.seen as seen

TRACE = "Traceback (most recent call last):\n  File \"/app/x.py\", line 12\nConnectionRefusedError: [Errno 111] refused"


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    monkeypatch.setattr(seen, "SEEN_FILE", tmp_path / "bug_candidates.json")
    monkeypatch.setattr(runtime_settings, "get", lambda key: None)
    return tmp_path


@pytest.mark.parametrize("text, tool, cls", [
    ("Next: Grep(...). Guard shell.grep-command (RULE-X): use the Grep tool", "Bash:grep", "noise:guard"),
    ("Permission to use Bash has been denied.", "Bash:rm", "noise:denied"),
    ("The user doesn't want to proceed with this tool use.", "Edit", "noise:denied"),
    ("PreToolUse:Write hook error: blocked", "Write", "noise:guard"),
    ("String to replace not found in file.", "Edit", "misuse:edit-mismatch"),
    ("File has not been read yet. Read it first before writing to it.", "Write", "misuse:not-read"),
    ("File does not exist. Note: your current working directory is D:\\x", "Read", "misuse:missing-file"),
    ("InputValidationError: required parameter task is missing", "mcp__homeshed-library__library_find", "misuse:bad-arguments"),
    ("=== 3 failed, 210 passed in 12.1s ===", "Bash:python", "misuse:tests-failing"),
    ("Exit code 1", "Bash:docker", "misuse:bare-exit"),
    ("rtk: command timed out after 120 seconds while searching", "Bash:rtk", "misuse:shell-search"),
    (TRACE, "Bash:python", "platform"),
    ("No such file or directory: /opt/app/config.yaml while starting the service", "Bash:docker", "platform"),
])
def test_each_failure_is_sorted(text, tool, cls):
    assert seen.classify(text, tool) == cls


def test_a_stopped_call_is_noise():
    assert seen.classify(TRACE, "Bash:python", interrupted=True) == "noise:interrupted"


def test_only_a_second_session_makes_a_suggestion():
    for _ in range(5):  # one session's retries are never enough
        assert seen.seen(TRACE, "Bash:python", session="a")["status"] == "watching"
    other = TRACE.replace("line 12", "line 40").replace("/app/x.py", "/app/y.py")  # same error, other details
    out = seen.seen(other, "Bash:python", session="b")
    assert out == {"class": "platform", "status": "suggested", "sessions": 2}
    [item] = seen.suggestions()["items"]
    assert item["count"] == 6 and item["sessions"] == 2 and item["tool"] == "Bash:python"


def test_noise_and_misuse_are_counted_with_one_example_but_never_watched():
    for s in "abc":
        seen.seen("String to replace not found in file.", "Edit", session=s)
    out = seen.suggestions()
    assert out["items"] == [] and out["watching"] == 0
    assert out["classes"]["misuse:edit-mismatch"]["count"] == 3
    assert out["classes"]["misuse:edit-mismatch"]["example"] == "String to replace not found in file."


def test_a_known_bug_gets_one_occurrence_per_session():
    bug = kb.report("Service refuses connections", error_text=TRACE)
    for s in ("a", "a", "a", "b"):
        seen.seen(TRACE, "Bash:python", session=s, known_id=bug["id"])
    assert kb.list_bugs()["bugs"][0]["occurrences"] == 3  # its report, then a and b
    assert seen.suggestions()["watching"] == 0             # a known bug is never suggested again


def test_keep_makes_a_private_open_known_bug_and_dismiss_silences_it():
    for s in "ab":
        seen.seen(TRACE, "Bash:python", session=s)
    key = seen.suggestions()["items"][0]["key"]
    kept = seen.decide(key, "keep", title="Service refuses connections after deploy")
    bug = kb.find(kept["kb"])["matches"][0]
    assert bug["title"] == "Service refuses connections after deploy" and bug["fix_status"] == "open"
    assert seen.suggestions()["items"] == []
    with pytest.raises(seen.SeenError):
        seen.decide(key, "dismiss")

    other = "RuntimeError: the queue worker stopped unexpectedly"
    for s in "ab":
        seen.seen(other, "Bash:python", session=s)
    key = seen.suggestions()["items"][0]["key"]
    seen.decide(key, "dismiss")
    seen.seen(other, "Bash:python", session="c")
    assert seen.suggestions()["items"] == [] and seen.suggestions()["dismissed"] == 1


def test_secrets_never_reach_the_file(stores):
    leak = "AuthError: bad token ghp_" + "A" * 30 + " for http://192.168.1.50:8770"
    for s in "ab":
        seen.seen(leak, "Bash:curl", session=s)
    raw = (stores / "bug_candidates.json").read_text(encoding="utf-8")
    assert "ghp_" not in raw and "192.168.1.50" not in raw and "[hidden]" in raw
    assert '"a"' not in raw  # session ids are stored only as hashes


def test_switched_off_it_records_nothing(stores, monkeypatch):
    monkeypatch.setattr(runtime_settings, "get", lambda key: False if key == "bug_capture" else None)
    assert seen.seen(TRACE, "Bash:python", session="a") == {"class": "off"}
    assert not (stores / "bug_candidates.json").exists()


def test_old_watched_errors_are_forgotten(stores):
    seen.seen(TRACE, "Bash:python", session="a")
    data = json.loads((stores / "bug_candidates.json").read_text(encoding="utf-8"))
    for w in data["watched"].values():
        w["last"] = "2026-01-01T00:00:00+00:00"
    (stores / "bug_candidates.json").write_text(json.dumps(data), encoding="utf-8")
    seen.seen("RuntimeError: something else entirely broke", "Bash:python", session="a")
    assert seen.suggestions()["watching"] == 1


def test_the_shared_contracts_fingerprint_examples_hold():
    """The library implements the same fingerprint (capabilities/bugs/sync-contract.md); both sides check these."""
    from pathlib import Path
    vectors = json.loads((Path(__file__).parents[1] / "tests/fixtures/bug_contract_v1_vectors.json").read_text("utf-8"))
    assert vectors["fp_version"] == 1
    for case in vectors["cases"]:
        assert kb.fingerprint(case["error_text"]) == case["fp"], case["error_text"]


def test_a_broken_file_is_refused_not_overwritten(stores):
    (stores / "bug_candidates.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(seen.SeenError):
        seen.seen(TRACE, "Bash:python", session="a")
    assert (stores / "bug_candidates.json").read_text(encoding="utf-8") == "{not json"
