"""The known-bugs Pro feed: only public bugs leave, cleaned of anything private; a Pro install merges the feed as
PRO-KB-..., refreshes it on the next sync, and never trusts the download. Cleaning cases listed by the local model
(qwen3-coder-30b), fixed on review."""
import json

import pytest

import tools.bugs.known as kb


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "BUGS_FILE", tmp_path / "known_bugs.json")
    return tmp_path / "known_bugs.json"


@pytest.mark.parametrize("raw, cleaned", [
    ("ssh root@192.168.1.10 failed", "ssh <user>@<host> failed"),
    ("curl http://10.0.0.5:8080/health", "curl http://<ip>:8080/health"),
    ("bound to 172.20.1.9", "bound to <ip>"),
    ("172.32.0.1 is public", "172.32.0.1 is public"),
    ("dns 8.8.8.8 works", "dns 8.8.8.8 works"),
    ("version 1.2.3.4 fixed it", "version 1.2.3.4 fixed it"),
    (r"C:\Users\alex\AppData\x.log", r"C:\Users\<you>\AppData\x.log"),
    ("/home/alex/.ssh/config", "/home/<you>/.ssh/config"),
    ("see https://github.com/o/r/issues/1", "see https://github.com/o/r/issues/1"),
])
def test_sanitize(raw, cleaned):
    assert kb.sanitize(raw) == cleaned


def test_sanitize_hides_secrets():
    fake = "hlc_" + "A1b2C3d4E5f6G7h8I9j0K1"
    assert fake not in kb.sanitize(f"token {fake} and MEMORY_CORE_BEARER=abcdef123456 leaked")


def test_only_public_bugs_are_exported_and_they_are_cleaned():
    kb.report("Private bug", error_text="nas-box 192.168.1.10 refused")
    pub = kb.report("Deploy piped to tail hides failure", symptom="deploy on 192.168.1.10 looked fine",
                    fix="log to a file", audience="public", fix_status="fixed")
    pack = kb.export_pack()
    assert pack["kind"] == "known-bugs" and pack["slug"] == "known-bugs"
    assert [b["origin_id"] for b in pack["bugs"]] == [pub["id"]]
    assert "192.168" not in json.dumps(pack)


def test_a_bug_can_be_made_public_later_by_id():
    b = kb.report("Doubled backslashes collapse", symptom="regex broke")
    kb.report("Doubled backslashes collapse", id=b["id"], audience="public")
    assert [x["origin_id"] for x in kb.export_pack()["bugs"]] == [b["id"]]
    with pytest.raises(kb.BugsError, match="audience"):
        kb.report("x" * 5, audience="everyone")


def test_a_pro_install_merges_the_feed_then_refreshes_it():
    feed = {"kind": "known-bugs", "bugs": [
        {"origin_id": "KB-0031", "title": "Doubled backslashes collapse", "fix_status": "workaround-only",
         "workaround": "write code with a file", "error_text": "", "tags": ["windows"]},
        {"origin_id": "not-an-id", "title": "ignored"},
        {"origin_id": "KB-0002", "title": "x"},                     # too short a title: ignored
    ]}
    assert kb.import_feed(feed) == {"added": 1, "updated": 0, "total": 1}
    feed["bugs"][0]["fix_status"] = "fixed"
    assert kb.import_feed(feed)["updated"] == 1
    [b] = kb.list_bugs()["bugs"]
    assert b["id"] == "PRO-KB-0031" and b["fix_status"] == "fixed" and b["audience"] == "pro"
    assert kb.find("Doubled backslashes collapse")["matches"][0]["id"] == "PRO-KB-0031"
    local = kb.report("A local bug after the feed")
    assert local["id"] == "KB-0001"                                   # local numbering ignores PRO ids


def test_a_bad_download_is_refused():
    for bad in (None, [], {"kind": "rules"}, {"kind": "known-bugs", "bugs": "nope"}):
        with pytest.raises(kb.BugsError, match="known-bugs pack"):
            kb.import_feed(bad)
