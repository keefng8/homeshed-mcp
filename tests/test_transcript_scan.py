"""secrets.transcript_scan on a fake Claude config folder. Every secret is built at runtime from random characters,
never written as a literal, so the repo's own pre-commit secret scanner has nothing to find in this file."""
import json
import os
import random
import string
import time

import pytest

from tools.secrets import transcript_scan as mod

scan = getattr(mod.transcript_scan, "__wrapped__", mod.transcript_scan)
RNG = random.Random(7)


def rand(n, alphabet=string.ascii_letters + string.digits):
    return "".join(RNG.choice(alphabet) for _ in range(n))


def fake(kind):
    prefixes = {"anthropic": "sk-" + "ant-", "github": "gh" + "p_", "groq": "gs" + "k_", "aws": "AK" + "IA"}
    body = rand(16, string.ascii_uppercase + string.digits) if kind == "aws" else rand(40)
    return prefixes[kind] + body


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return tmp_path


def transcript(config, project, name, *records):
    folder = config / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with path.open("a", encoding="utf-8") as f:
        for text, day in records:
            f.write(json.dumps({"timestamp": f"{day}T10:00:00Z", "message": {"content": [{"type": "text", "text": text}]}}) + "\n")
    return path


def test_no_secret_ever_comes_back(config):
    key = fake("anthropic")
    transcript(config, "D--proj", "a.jsonl", (f"export ANTHROPIC_API_KEY={key}", "2026-09-01"))
    out = scan()
    assert key not in json.dumps(out) and key[4:12] not in json.dumps(out)
    row = out["checks"][0]
    assert (row["kind"], row["prefix"], row["length"]) == ("Anthropic API key", key[:4], len(key))
    assert len(row["fingerprint"]) == 12 and "console.anthropic.com" in out["actions"]["Anthropic API key"]
    assert "action" not in row  # said once per kind in `actions`, not in every row


def test_one_secret_in_many_files_counts_once_with_dates_and_projects(config):
    key = fake("github")
    transcript(config, "D--one", "a.jsonl", (f"git remote set-url https://x:{key}@github.com", "2026-08-01"))
    transcript(config, "D--one", "b.jsonl", (f"token {key}", "2026-09-10"), (f"again {key}", "2026-09-12"))
    transcript(config, "G--two", "c.jsonl", (f"{key}", "2026-09-20"))
    out = scan()
    assert out["failed"] == 1 and out["summary"] == {"GitHub token": 1}
    row = out["checks"][0]
    assert (row["files"], row["occurrences"]) == (3, 4)
    assert (row["first_seen"], row["last_seen"], row["projects"]) == ("2026-08-01", "2026-09-20", ["D--one", "G--two"])


def test_passwords_show_no_prefix_and_placeholders_are_ignored(config):
    pw, q = rand(18), '"'  # the quote is joined at runtime, so the repo's secret scanner sees no key = "value" here
    transcript(config, "p", "a.jsonl",
               ("password = " + q + pw + q, "2026-09-01"),
               ("api_key = " + q + "your-api-key-here-please" + q, "2026-09-01"),
               ("secret: " + q + "[value hidden]" + q, "2026-09-01"),
               ("token = " + q + "a" * 20 + q, "2026-09-01"))
    out = scan()
    assert out["failed"] == 1 and out["checks"][0]["prefix"] == "" and out["checks"][0]["kind"] == "password or secret"


def test_a_value_counts_as_its_most_specific_kind(config):
    key = fake("groq")
    transcript(config, "p", "a.jsonl", (f'GROQ_API_KEY="{key}"', "2026-09-01"))
    assert scan()["summary"] == {"Groq key": 1}


def test_days_limits_to_recent_transcripts(config):
    old = transcript(config, "p", "old.jsonl", (fake("aws"), "2025-01-01"))
    os.utime(old, (time.time() - 90 * 86400,) * 2)
    transcript(config, "p", "new.jsonl", (fake("aws"), "2026-09-29"))
    assert scan(days=30)["transcripts"] == 1 and scan()["transcripts"] == 2


def test_it_reads_only_the_transcript_folder(config):
    (config / "notes.txt").write_text(fake("anthropic"))
    (config / "projects").mkdir()
    (config / "projects" / "readme.md").write_text(fake("anthropic"))
    assert scan()["failed"] == 0


def test_no_transcripts_and_bad_input(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "none"))
    assert "no Claude Code transcripts" in scan()["note"]
    with pytest.raises(mod.TranscriptScanError):
        scan(days=-1)
    with pytest.raises(mod.TranscriptScanError):
        scan(detail="all")


def test_compact_caps_findings(config):
    transcript(config, "p", "a.jsonl", *[(fake("github"), "2026-09-01") for _ in range(30)])
    out = scan()
    assert out["failed"] == 30 and len(out["checks"]) == 20 and "10 more" in out["more"]
    assert len(scan(detail="full")["checks"]) == 30
