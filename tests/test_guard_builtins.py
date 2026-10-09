"""guard_engine's built-in checks (2026-10-01): the checks a pattern can't do, which a pack names as data ("kind":
"builtin", "check": ...), so Token Saver and Team Sessions work fully in the public engine. Ported from the owner's
platform hooks; the cases follow theirs, plus the gaps found porting them. Case ideas drafted by the local model
(qwen3-coder-30b) and corrected. Every state file and transcript here is in a temp folder."""
import json
import subprocess
import sys
import types
from datetime import datetime, timezone

import pytest

import guard_engine as ge

NOW = 1_790_000_000.0
RULE = {"id": "R1", "title": "A rule", "do": "Do it", "why": "Because", "scope": "global"}


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """The engine with its packs, modes and state in a temp folder, Claude Code's own folder too, and a clock."""
    (tmp_path / "packs").mkdir()
    monkeypatch.setattr(ge, "PACKS_DIR", tmp_path / "packs")
    monkeypatch.setattr(ge, "MODES_FILE", tmp_path / "guard-modes.json")
    monkeypatch.setattr(ge, "STATE_DIR", tmp_path / "state")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for name in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_CODE_DISABLE_1M_CONTEXT", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ge, "_SESSION_PAIRS", None)  # the per-process cache of Claude Code's session list
    clock = Clock()
    monkeypatch.setattr(ge, "time", types.SimpleNamespace(time=clock, perf_counter=__import__("time").perf_counter))
    return types.SimpleNamespace(dir=tmp_path, clock=clock)


def save(folder, pack, source="pro", notes_ok=False):
    """As the installer saves a pack: the file, then its entry (source and hash) in the index. source=None: a file
    dropped into the folder by hand, which the engine treats as unverified."""
    raw = json.dumps(pack).encode("utf-8")
    (folder / f"{pack['slug']}.json").write_bytes(raw)
    if source:
        index = ge.read_index(folder)
        index[pack["slug"]] = {"source": source, "sha256": ge.file_hash(raw), **({"notes_ok": True} if notes_ok else {})}
        (folder / ge.INDEX).write_text(json.dumps(index), encoding="utf-8")


def install(engine, *guards, slug="test-pack", source="pro", notes_ok=False):
    pack = {"formatVersion": 1, "slug": slug, "title": "Test", "version": "1.0.0", "tier": "pro", "summary": "Tests.",
            "rules": [RULE], "guards": [{"rule": "R1", "kind": "builtin", **g} for g in guards]}
    assert ge.validate_pack(pack) == [], ge.validate_pack(pack)
    save(engine.dir / "packs", pack, source, notes_ok)


def transcript(engine, records, name="t.jsonl"):
    path = engine.dir / name
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(path)


def use(name, uid, **args):
    return {"type": "tool_use", "id": uid, "name": name, "input": args}


def said(*uses, t=NOW, usage=None, model="claude-opus-5-5"):
    message = {"model": model, "content": list(uses)}
    if usage is not None:
        message["usage"] = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": usage - 10,
                            "output_tokens": 50}
    return {"type": "assistant", "timestamp": iso(t), "message": message}


def result(uid, error=False, t=NOW):
    return {"type": "user", "timestamp": iso(t),
            "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "is_error": error, "content": "ok"}]}}


def prompt(text, t=NOW):
    return {"type": "user", "timestamp": iso(t), "message": {"content": text}}


def peer(text, name="R&D", address="uds:pipe-rnd"):
    return f'<cross-session-message from="{address}" from-name="{name}" from-mode="prompting">\n{text}\n</cross-session-message>'


def queued(text, t, name="R&D", address="uds:pipe-rnd"):
    return {"type": "attachment", "timestamp": iso(t), "attachment": {
        "type": "queued_command", "prompt": peer(text, name, address), "isMeta": True,
        "origin": {"kind": "peer", "from": address, "name": name}}}


def hook(event, session="s-1", **fields):
    out = ge.decide({"hook_event_name": event, "session_id": session, **fields})
    return json.loads(out) if out else None


def tool(name, session="s-1", path=None, **args):
    got = hook("PreToolUse", session, tool_name=name, tool_input=args, transcript_path=path)
    return got["hookSpecificOutput"] if got else None


def note(answer) -> str:
    return (answer or {}).get("additionalContext") or ""


# --- polling ---------------------------------------------------------------------------------------------------------------
POLLING = {"id": "polling.guard", "check": "polling", "decision": "block"}


@pytest.mark.parametrize("name, command, blocked", [
    ("Bash", "sleep 30 && curl -s localhost:8080/health", True),
    ("Bash", "sleep 3 && docker compose up -d", False),                        # a short pause between steps
    ("PowerShell", "Start-Sleep -Seconds 20; Get-Process node", True),
    ("Bash", "for i in $(seq 1 30); do curl -sf localhost/ready && break; sleep 5; done", False),  # one bounded wait
    ("Bash", "for i in $(seq 1 30); do curl -sf localhost/ready && break; sleep 15; done", True),  # its step too long
    ("Bash", "while true; do sleep 8; done", True),                               # no bound: a bare 8 s sleep
    ("Bash", "sleep 60  # poll-required: the vendor API rate-limits retries", False),
    ("Bash", "python -c 'import time; time.sleep(30)'", False),                  # not a shell sleep (as the platform)
])
def test_long_sleeps_are_refused_unless_bounded_or_explained(engine, name, command, blocked):
    install(engine, POLLING)
    got = tool(name, command=command)
    assert (got is not None and got.get("permissionDecision") == "deny") is blocked


@pytest.mark.parametrize("command, blocked", [
    ("ssh host 'docker exec -d web sh -c \"while true; do sleep 30; done\"'", False),   # detached on the server
    ("nohup sh -c 'sleep 60; ./job.sh' > job.log 2>&1 &", False),
    ("ssh host 'sleep 60; ./job.sh'", True),                                            # this session waits for it
])
def test_a_sleep_inside_a_detached_job_isnt_polling(engine, command, blocked):
    """R&D's rules review (2026-10-01): a detached remote wait loop was refused, though nothing waited on it."""
    install(engine, POLLING)
    got = tool("Bash", command=command)
    assert (got is not None and got.get("permissionDecision") == "deny") is blocked


@pytest.mark.parametrize("text, owed", [
    ("Got it, will hold off on local_ai until the window ends.", False),
    ("Understood: no GPU service restarts until you say.", False),
    ("Can you check the deploy log?", True), ("Heads-up: the local model is off from 21:15.", False),
    ("Your guards, your call.", True), ("Confirmed. Anything else you need?", True),
    ("Noted, holding redeploys.", False), ("I'm waiting for your go on the release.", True),
])
def test_only_questions_and_requests_are_owed_a_reply(text, owed):
    assert ge.asks_for_reply(text) is owed


def test_a_heads_up_to_another_session_is_not_a_hand_off(engine):
    install(engine, HANDOFF)
    path = transcript(engine, [prompt("ship it", NOW)])
    assert tool("SendMessage", path=path, to="prepper", message="Heads-up: the local model is off from 21:15.") is None
    assert tool("SendMessage", path=path, to="prepper", message="Please package v0.1")["permissionDecision"] == "deny"


def test_a_background_command_is_never_polling(engine):
    install(engine, POLLING)
    assert tool("Bash", command="sleep 120 && ./deploy.sh", run_in_background=True) is None


def test_the_fourth_same_status_check_in_five_minutes_is_refused_per_session(engine):
    install(engine, POLLING)
    for _ in range(3):
        assert tool("Bash", command="curl -s localhost:8082/health") is None
    got = tool("Bash", command="curl -s  localhost:8082/health")            # spacing doesn't make it a new check
    assert got["permissionDecision"] == "deny" and "3 times in the last 5 minutes" in got["permissionDecisionReason"]
    assert got["permissionDecisionReason"].startswith('Rule pack "test-pack" (HomeShed Pro), guard polling.guard: ')
    assert tool("Bash", command="curl -s localhost:8082/health", session="s-2") is None  # another session's own count
    assert tool("Bash", command="docker ps") is None                          # a different check
    engine.clock.t += 301                                                      # five minutes on: allowed again
    assert tool("Bash", command="curl -s localhost:8082/health") is None


def test_polling_in_warn_mode_tells_claude_and_counts_the_call(engine):
    install(engine, {**POLLING, "decision": "warn", "settings": {"max_repeats": 1}})
    assert tool("Bash", command="docker logs web") is None
    got = tool("Bash", command="docker logs web")
    assert "permissionDecision" not in got and "No-polling check" in note(got)
    assert len(ge.load_state("s-1")["polling"]) == 2                          # it ran, so it counts


# --- context-alarm ---------------------------------------------------------------------------------------------------------
CONTEXT = {"id": "context.alarm", "check": "context-alarm", "decision": "warn"}


def context_at(engine, tokens, model="claude-opus-5-5", extra=()):
    path = transcript(engine, [*extra, said(usage=tokens, model=model)])
    engine.clock.t += 31  # past the check's 30 s gap
    return note(tool("Bash", command="ls", path=path))


def test_the_context_note_comes_once_per_crossing_and_again_after_compaction(engine):
    install(engine, CONTEXT)
    assert context_at(engine, 800_000) == ""                                   # under 90% of ~967k
    first = context_at(engine, 880_000)
    assert "about 880k tokens" in first and "near 967k" in first and "Save decisions" in first
    assert context_at(engine, 900_000) == ""                                   # told once per crossing
    assert context_at(engine, 120_000) == ""                                   # compacted: armed again
    assert "about 890k tokens" in context_at(engine, 890_000)


def test_the_context_check_looks_at_most_every_30_seconds(engine):
    install(engine, CONTEXT)
    path = transcript(engine, [said(usage=950_000)])
    engine.clock.t += 31
    assert "950k" in note(tool("Bash", command="ls", path=path))
    ge.save_state("s-1", {**ge.load_state("s-1"), "context_told": None})
    engine.clock.t += 5
    assert note(tool("Bash", command="ls", path=path)) == ""                   # too soon to look again


@pytest.mark.parametrize("model, env, settings, tokens, fires", [
    ("claude-haiku-4-5-20251001", {}, None, 185_000, True),                    # a 200k window
    ("claude-haiku-4-5-20251001", {}, None, 150_000, False),
    ("claude-opus-5-5", {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}, None, 185_000, True),
    ("claude-opus-5-5", {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "500000"}, None, 460_000, True),
    ("claude-opus-5-5", {}, {"autoCompactWindow": "400k"}, 365_000, True),     # /autocompact 400k
    ("claude-opus-5-5", {}, {"autoCompactWindow": "400k"}, 300_000, False),
    ("claude-opus-5-5", {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "50"}, None, 460_000, True),
    ("claude-sonnet-4-6", {}, None, 185_000, True),                            # no [1m] in the id: 200k
])
def test_where_claude_code_compacts_follows_its_documented_settings(engine, monkeypatch, model, env, settings, tokens,
                                                                     fires):
    install(engine, CONTEXT)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    if settings is not None:
        (engine.dir / "claude").mkdir()
        (engine.dir / "claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    assert bool(context_at(engine, tokens, model)) is fires


def test_where_this_model_really_compacted_is_learned(engine):
    install(engine, CONTEXT)
    seen = [said(usage=590_000, model="claude-opus-4-6"),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto", "preTokens": 600_000}}]
    assert context_at(engine, 100_000, "claude-opus-4-6", extra=seen) == ""   # a [1m] session compacted at 600k
    assert ge.compaction_point("claude-opus-4-6") == 600_000
    assert "near 600k" in context_at(engine, 560_000, "claude-opus-4-6")


@pytest.mark.parametrize("model, one_m", [
    ("claude-opus-5-5", True), ("claude-opus-4-7", True), ("claude-opus-4-6", False), ("claude-opus-4-20250514", False),
    ("claude-sonnet-5", True), ("claude-sonnet-4-5-20250929", False), ("claude-fable-5-1", True),
    ("claude-haiku-4-5-20251001", False), ("us.anthropic.claude-opus-4-8-v1:0", False),
    ("claude-opus-4-8@20260101", False), ("", False),
])
def test_which_models_have_a_native_1m_window(model, one_m):
    assert ge._native_1m(model) is one_m


@pytest.mark.parametrize("size, tokens", [(200_000, 200_000), ("500k", 500_000), ("1M", 1_000_000), (300, 300_000),
                                          ("auto", None), (True, None), ("12", None), (None, None)])
def test_context_sizes_are_read_as_claude_code_writes_them(size, tokens):
    assert ge._tokens(size) == tokens


# --- delegation ------------------------------------------------------------------------------------------------------------
DELEGATION = {"id": "delegation.check", "check": "delegation", "decision": "warn"}
ASK = "mcp__homeshed__local_ai_ask"


def writes(n, start=0, folder="/work/app"):
    return [said(use("Edit", f"w{i}", file_path=f"{folder}/f{i}.py")) for i in range(start, start + n)]


def test_after_twelve_project_files_claude_is_told_once_per_twelve(engine):
    install(engine, DELEGATION)
    path = transcript(engine, [prompt("build it"), *writes(11)])
    assert tool("Write", path=path, file_path="/work/app/new.py") is None
    path = transcript(engine, [prompt("build it"), *writes(12)])
    got = tool("Write", path=path, file_path="/work/app/new.py")
    assert "permissionDecision" not in got and "12 project files changed" in note(got)
    path = transcript(engine, [prompt("build it"), *writes(20)])
    assert tool("Write", path=path, file_path="/work/app/new.py") is None     # told at 12: next at 24
    path = transcript(engine, [prompt("build it"), *writes(24)])
    assert "24 project files" in note(tool("Write", path=path, file_path="/work/app/new.py"))


def test_block_mode_holds_writes_until_the_local_model_does_some_work(engine):
    install(engine, {**DELEGATION, "decision": "block"})
    path = transcript(engine, [prompt("build it"), *writes(12)])
    assert tool("Edit", path=path, file_path="/work/app/x.py")["permissionDecision"] == "deny"
    path = transcript(engine, [prompt("build it"), *writes(12), said(use(ASK, "d1", prompt="draft the tests")),
                               result("d1"), *writes(2, 20)])
    assert tool("Edit", path=path, file_path="/work/app/x.py") is None


@pytest.mark.parametrize("records", [
    [said(use("Edit", f"w{i}", file_path="/work/app/same.py")) for i in range(15)],          # one file, many edits
    [said(use("Write", f"w{i}", file_path=f"/home/u/.claude/projects/p/memory/m{i}.md")) for i in range(15)],
    [said(use("Write", f"w{i}", file_path=f"/tmp/claude/scratch{i}.py")) for i in range(15)],  # the scratchpad
    [r for i in range(15) for r in (said(use("Edit", f"w{i}", file_path=f"/a/f{i}.py")), result(f"w{i}", error=True))],
])
def test_what_isnt_project_work_never_counts(engine, records):
    install(engine, {**DELEGATION, "decision": "block"})
    assert tool("Edit", path=transcript(engine, [prompt("go"), *records]), file_path="/work/app/x.py") is None


def test_it_stands_down_while_the_local_model_is_failing(engine):
    install(engine, {**DELEGATION, "decision": "block"})
    path = transcript(engine, [prompt("go"), said(use(ASK, "d1", prompt="x")), result("d1", error=True), *writes(14)])
    assert tool("Edit", path=path, file_path="/work/app/x.py") is None


def test_a_busy_turn_with_nothing_delegated_is_told_at_the_next_prompt_for_free(engine):
    install(engine, DELEGATION)
    turn = [prompt("refactor the module"), *[said(use("Read", f"r{i}", file_path=f"/a/{i}")) for i in range(7)],
            *writes(3)]
    path = transcript(engine, turn)
    assert hook("Stop", transcript_path=path, stop_hook_active=False) is None   # warn: nothing costs a turn now
    got = hook("UserPromptSubmit", prompt="next thing", transcript_path=path)
    assert "Delegation note from your last turn: 10 tool calls and 3 file writes" in got["hookSpecificOutput"]["additionalContext"]
    assert hook("UserPromptSubmit", prompt="and another", transcript_path=path) is None  # told once


def test_block_mode_holds_the_turn_once(engine):
    install(engine, {**DELEGATION, "decision": "block"})
    path = transcript(engine, [prompt("go"), *writes(8)])
    got = hook("Stop", transcript_path=path, stop_hook_active=False)
    assert got["decision"] == "block" and "8 file writes" in got["reason"]
    assert hook("Stop", transcript_path=path, stop_hook_active=True) is None


# --- Team Sessions ---------------------------------------------------------------------------------------------------------
LENGTH = {"id": "team.message-length", "check": "peer-message-length", "decision": "block"}
REQUEST = {"id": "team.peer-request", "check": "peer-request", "decision": "warn"}
HANDOFF = {"id": "team.peer-handoff", "check": "peer-handoff", "decision": "block"}
OWED = {"id": "team.owed-reply", "check": "owed-reply", "decision": "block"}


@pytest.mark.parametrize("message, blocked", [
    ("x" * 1500, False), ("x" * 1501, True),
    ("[long] " + "x" * 4493, False),                       # [long] buys up to three times the limit
    ("[long] " + "x" * 4494, True),                        # and no more (R&D's review)
])
def test_long_messages_between_sessions(engine, message, blocked):
    install(engine, LENGTH)
    got = tool("SendMessage", to="R&D", message=message)
    assert (got is not None and got["permissionDecision"] == "deny") is blocked
    if "[long]" in message and not blocked:
        assert ge.load_state("s-1")["overrides"] == {"long": 1}


@pytest.mark.parametrize("text, noted", [
    (peer("Can you check the deploy?"), True),
    (peer("Deployed and green. No reply needed."), False),
    (peer("FYI the build is done"), False),
    ("Please check the deploy", False),                                      # the user's own prompt
])
def test_a_peers_message_is_a_new_request(engine, text, noted):
    install(engine, REQUEST)
    got = hook("UserPromptSubmit", prompt=text)
    assert ("Request from another Claude session (R&D)" in json.dumps(got)) is noted


def steps(t, failed=False):
    out = [said(use("mcp__homeshed__memory_recall_relevant", "m1", query="x"), t=t), result("m1", failed, t=t),
           said(use("mcp__homeshed__reasoning_route", "r1", text="x"), t=t), result("r1", t=t)]
    return out


@pytest.mark.parametrize("records, tool_name, args, blocked", [
    ([prompt("ship it", NOW)], "SendMessage", {"to": "prepper", "message": "please package v0.1"}, True),
    ([prompt("ship it", NOW), *steps(NOW + 1)], "SendMessage", {"to": "prepper", "message": "please package"}, False),
    ([prompt("ship it", NOW)], "Agent", {"description": "x", "prompt": "write the docs"}, True),
    ([prompt("ship it", NOW)], "SendMessage", {"to": "prepper", "message": "unrouted: no HomeShed here. Package it"},
     False),                                                                    # its tools haven't worked here
    ([prompt("ship it", NOW)], "SendMessage", {"to": "prepper", "message": "unrouted: busy now"}, True),  # 2 words
    ([*steps(NOW - 1200), prompt("ship it", NOW)], "SendMessage",
     {"to": "prepper", "message": "unrouted: no HomeShed here. Package it"}, True),  # they worked earlier: use them
    ([prompt("ship it", NOW)], "SendMessage", {"to": "prepper", "message": ""}, False),  # a notify-when-idle subscription
    ([prompt(peer("Can you review this?"), NOW)], "SendMessage", {"to": "R&D [ab12]", "message": "Looks fine"}, False),
    ([*steps(NOW - 300), prompt("and now the docs", NOW)], "SendMessage", {"to": "prepper", "message": "docs please"},
     False),                                                                    # a follow-up 5 min after the steps
    ([*steps(NOW - 1200), prompt("and now the docs", NOW)], "SendMessage", {"to": "prepper", "message": "docs please"},
     True),                                                                     # 20 min: a new request
    ([*steps(NOW - 1200), prompt(peer("next one?"), NOW)], "SendMessage", {"to": "prepper", "message": "docs please"},
     False),                                                                    # a peer's request keeps them 30 min
    ([prompt("ship it", NOW), *steps(NOW + 1, failed=True)[:2]], "SendMessage", {"to": "prepper", "message": "pkg"},
     False),                                                                    # HomeShed's tools are failing
])
def test_work_handed_to_another_claude_waits_for_recall_and_routing(engine, records, tool_name, args, blocked):
    install(engine, HANDOFF)
    got = tool(tool_name, path=transcript(engine, records), **args)
    assert (got is not None and got.get("permissionDecision") == "deny") is blocked
    if blocked:
        reason, worked = got["permissionDecisionReason"], any(r.get("type") == "assistant" for r in records)
        assert "recall memory" in reason or "route the task" in reason
        assert ("unrouted: <reason>" in reason) is not worked  # offered only while HomeShed's tools haven't worked
    elif "unrouted:" in str(args.get("message")):
        assert ge.load_state("s-1")["overrides"] == {"unrouted": 1}  # counted, for the user to see


def test_a_peer_waiting_on_an_answer_holds_the_turn_once_until_answered(engine):
    install(engine, OWED)
    hook("UserPromptSubmit", prompt=peer("Can you confirm the release plan?"))
    path = transcript(engine, [prompt(peer("Can you confirm the release plan?"))])
    got = hook("Stop", transcript_path=path, stop_hook_active=False)
    assert got["decision"] == "block" and 'R&D (0 min ago: "Can you confirm the release plan?")' in got["reason"]
    assert hook("Stop", transcript_path=path, stop_hook_active=True) is None   # held once, never a loop
    tool("SendMessage", to="r&d [77aa]", message="Confirmed, with two amendments.", path=path)
    assert hook("Stop", transcript_path=path, stop_hook_active=False) is None


def test_messages_that_arrive_mid_turn_count_and_a_reply_in_the_transcript_settles_them(engine):
    install(engine, OWED)
    asked = queued("Is that done?", NOW - 60)
    path = transcript(engine, [prompt("work on the packs", NOW - 600), asked])
    got = hook("Stop", transcript_path=path, stop_hook_active=False)
    assert got["decision"] == "block" and "Is that done?" in got["reason"]
    replied = said(use("SendMessage", "s1", to="R&D", message="Not yet: R2 is still off."), t=NOW - 30)
    path = transcript(engine, [prompt("work on the packs", NOW - 600), asked, replied])
    ge.save_state("s-1", {})                                                    # a fresh session sees only the transcript
    assert hook("Stop", transcript_path=path, stop_hook_active=False) is None


def test_an_update_needs_no_reply_and_an_old_ask_lapses(engine):
    install(engine, OWED)
    path = transcript(engine, [queued("Deployed. No reply needed.", NOW - 60)])
    assert hook("Stop", transcript_path=path, stop_hook_active=False) is None
    hook("UserPromptSubmit", prompt=peer("Can you check?"))
    engine.clock.t += ge.OWED_KEEP_S + 1
    assert hook("Stop", transcript_path=transcript(engine, []), stop_hook_active=False) is None


def test_a_reply_to_the_address_settles_what_was_owed_to_the_name(engine):
    """Claude Code's running-session list pairs a session's name with its messaging address."""
    install(engine, OWED)
    (engine.dir / "claude" / "sessions").mkdir(parents=True)
    (engine.dir / "claude" / "sessions" / "1.json").write_text(
        json.dumps({"name": "Github prepper", "messagingSocketPath": "uds:pipe-prepper"}), encoding="utf-8")
    hook("UserPromptSubmit", prompt=peer("Ready to pack?", name="Github prepper", address="uds:pipe-prepper"))
    tool("SendMessage", to="uds:pipe-prepper", message="Yes, go.")
    assert hook("Stop", transcript_path=transcript(engine, []), stop_hook_active=False) is None


def test_a_refused_reply_settles_nothing(engine):
    install(engine, OWED, LENGTH)
    hook("UserPromptSubmit", prompt=peer("Status?"))
    assert tool("SendMessage", to="R&D", message="x" * 2000)["permissionDecision"] == "deny"   # too long: not sent
    assert hook("Stop", transcript_path=transcript(engine, []), stop_hook_active=False)["decision"] == "block"


# --- session-note ----------------------------------------------------------------------------------------------------------
NOTE = {"id": "beginner.plain-words", "check": "session-note",
        "message": "Beginner Mode: explain each step in plain words, one step at a time."}


def test_a_session_note_is_added_at_start_and_after_compaction(engine):
    install(engine, NOTE)
    for source in ("startup", "resume", "clear", "compact"):
        got = hook("SessionStart", source=source)["hookSpecificOutput"]
        assert got["hookEventName"] == "SessionStart" and "one step at a time" in got["additionalContext"]
        assert got["additionalContext"].startswith('Heads-up from Rule pack "test-pack" (HomeShed Pro)')


@pytest.mark.parametrize("source, notes_ok, told", [
    ("pro", False, True),          # a verified Pro pack
    ("file", True, True),          # added from a file, its note read and accepted at `packs add`
    ("file", False, False),        # added from a file, its note not accepted
    (None, False, False),          # dropped into the folder by hand: unverified
])
def test_only_a_verified_pack_or_an_accepted_note_reaches_claude(engine, source, notes_ok, told):
    """R&D's review: this text reaches Claude every session, so its label has to be true."""
    install(engine, NOTE, source=source, notes_ok=notes_ok)
    assert (hook("SessionStart", source="startup") is not None) is told


# --- the engine around them ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("guard, words", [
    ({"check": "teleport"}, "no built-in check called"),
    ({"check": "polling", "decision": "ask"}, "decision must be one of"),
    ({"check": "polling", "settings": {"max_sleep_s": 0}}, "out of range"),
    ({"check": "polling", "settings": {"max_sleep_s": True}}, "out of range"),
    ({"check": "polling", "settings": {"script": "x.py"}}, "takes only these settings"),
    ({"check": "delegation", "settings": {"tools": ["../evil"]}}, "out of range"),
    ({"check": "session-note"}, "needs a message"),
    ({"check": "session-note", "message": "two\nlines"}, "plain text"),
    ({"check": "context-alarm", "settings": {"alarm_pct": 99}}, "out of range"),
])
def test_a_built_in_guard_is_validated_like_any_other(guard, words):
    pack = {"formatVersion": 1, "slug": "p", "title": "P", "version": "1", "tier": "pro", "summary": "s",
            "rules": [RULE], "guards": [{"id": "g1", "rule": "R1", "kind": "builtin", **guard}]}
    assert any(words in problem for problem in ge.validate_pack(pack)), ge.validate_pack(pack)


def test_modes_switch_a_built_in_check_off_or_soften_it(engine):
    install(engine, POLLING)
    modes = engine.dir / "guard-modes.json"
    modes.write_text(json.dumps({"polling.guard": "off"}), encoding="utf-8")
    assert tool("Bash", command="sleep 30") is None
    modes.write_text(json.dumps({"polling.guard": "warn"}), encoding="utf-8")
    assert "No-polling check" in note(tool("Bash", command="sleep 30"))
    modes.write_text(json.dumps({"polling.guard": "ask"}), encoding="utf-8")   # not one polling supports: the pack's own
    assert tool("Bash", command="sleep 30")["permissionDecision"] == "deny"


def test_a_check_that_breaks_never_stops_the_others_or_the_call(engine, monkeypatch):
    install(engine, POLLING, LENGTH)
    monkeypatch.setitem(ge.BUILTINS["polling"], "run", lambda *a: 1 / 0)
    assert tool("Bash", command="sleep 30") is None
    assert tool("SendMessage", to="x", message="y" * 2000)["permissionDecision"] == "deny"


def test_a_broken_state_file_is_a_fresh_start(engine):
    install(engine, POLLING)
    (engine.dir / "state").mkdir()
    (engine.dir / "state" / "s-1.json").write_text("{broken", encoding="utf-8")
    assert tool("Bash", command="docker ps") is None and ge.load_state("s-1")["polling"]


def test_old_sessions_state_is_tidied_away(engine):
    import os
    install(engine, POLLING)
    (engine.dir / "state").mkdir()
    old = engine.dir / "state" / "gone.json"
    old.write_text("{}", encoding="utf-8")
    os.utime(old, (NOW - 3 * 86400, NOW - 3 * 86400))
    tool("Bash", command="docker ps", session="brand-new")   # a new session's first state file tidies up
    assert not old.exists() and (engine.dir / "state" / "brand-new.json").exists()


ASKING = {"formatVersion": 1, "slug": "asks", "title": "A", "version": "1", "tier": "free", "summary": "s",
          "rules": [RULE], "guards": [{"id": "ask-install", "rule": "R1", "kind": "command", "tools": ["Bash"],
                                       "match_any": [r"\bpip\s+install\b"], "decision": "ask",
                                       "message": "This installs software. Allow it?",
                                       "examples": {"block": ["pip install requests"], "allow": ["pip list"]}}]}


def test_ask_has_the_user_confirm_in_the_engines_words_and_a_block_elsewhere_wins(engine):
    assert ge.validate_pack(ASKING) == []
    save(engine.dir / "packs", ASKING, "file")
    got = tool("Bash", command="pip install requests")
    assert got["permissionDecision"] == "ask"
    assert got["permissionDecisionReason"] == ('Rule pack "asks" (added from a file) wants you to confirm this. '
                                               "Its note: This installs software. Allow it?")
    install(engine, POLLING, slug="zz-polling")
    assert tool("Bash", command="sleep 30; pip install requests")["permissionDecision"] == "deny"


def test_an_unverified_pack_cant_ask_the_user_anything(engine):
    """R&D's review: a file dropped into the folder (or edited after install) mustn't put words in front of the user."""
    save(engine.dir / "packs", ASKING, source=None)
    got = tool("Bash", command="pip install requests")
    assert "permissionDecision" not in got and "(unverified)" in got["additionalContext"]


@pytest.mark.parametrize("tamper, label", [
    (lambda folder: None, "HomeShed Pro"),                                                 # as installed
    (lambda folder: (folder / "test-pack.json").write_text(                               # edited after install
        (folder / "test-pack.json").read_text().replace('"summary": "s"', '"summary": "edited"')), "unverified"),
    (lambda folder: (folder / ge.INDEX).unlink(), "unverified"),                          # the index is gone
    (lambda folder: (folder / ge.INDEX).write_text("{corrupt"), "unverified"),
    (lambda folder: (folder / ge.INDEX).write_text("[" * 50_000), "unverified"),           # nested too deeply
    (lambda folder: (folder / ge.INDEX).write_text(json.dumps(
        {"test-pack": {"source": "gold", "sha256": ge.file_hash((folder / "test-pack.json").read_bytes())}})),
     "unverified"),                                                                        # an unknown source
    (lambda folder: (folder / ge.INDEX).write_text(json.dumps(
        {"test-pack": {"source": "pro", "sha256": ge.file_hash(b"another file")}})), "unverified"),
])
def test_where_a_pack_came_from_is_the_installers_record_never_the_packs_own(engine, tamper, label):
    pack = {"formatVersion": 1, "slug": "test-pack", "title": "T", "version": "1", "tier": "pro", "summary": "s",
            "rules": [RULE], "guards": [{**POLLING, "rule": "R1", "kind": "builtin"}],
            "_installed_from": "pro", "_source": "pro"}                                    # a pack's own claims
    save(engine.dir / "packs", pack, "pro")
    tamper(engine.dir / "packs")
    got = tool("Bash", command="sleep 30")
    assert f'Rule pack "test-pack" ({label}), guard polling.guard' in got["permissionDecisionReason"]


def test_a_transcript_line_nested_too_deeply_is_skipped_not_fatal(engine):
    install(engine, CONTEXT)
    path = engine.dir / "deep.jsonl"
    path.write_text("[" * 50_000 + "\n" + json.dumps(said(usage=950_000)) + "\n", encoding="utf-8")
    engine.clock.t += 31
    assert "950k" in note(tool("Bash", command="ls", path=str(path)))


@pytest.mark.parametrize("event, expect", [
    ({"hook_event_name": "Stop", "session_id": "s-9", "stop_hook_active": False}, "block"),
    ({"hook_event_name": "SessionStart", "session_id": "s-9", "source": "compact"}, "Mind the owed replies"),
    ({"hook_event_name": "PostToolUse", "session_id": "s-9"}, ""),               # not an event it listens to
])
def test_the_real_hook_process_answers_each_event_and_exits_0(tmp_path, event, expect):
    data = tmp_path / "data"
    (data / "packs").mkdir(parents=True)
    pack = {"formatVersion": 1, "slug": "team", "title": "T", "version": "1", "tier": "pro", "summary": "s",
            "rules": [RULE], "guards": [{"id": "owed", "rule": "R1", "kind": "builtin", "check": "owed-reply"},
                                        {"id": "note", "rule": "R1", "kind": "builtin", "check": "session-note",
                                         "message": "Mind the owed replies."}]}
    save(data / "packs", pack, "pro")
    (data / "guard-state").mkdir()
    (data / "guard-state" / "s-9.json").write_text(json.dumps(
        {"owed": {"r&d": {"names": ["R&D"], "at": __import__("time").time(), "said": "Ready?"}}}), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", "-S", ge.__file__, "--packs", str(data / "packs")], input=json.dumps(event),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and expect in r.stdout


# --- the self-learning loop (2026-10-01): the stop record, the repeat line, the next call, lessons at session start ---
GREP = {"id": "t.grep", "title": "grep in the shell", "rule": "R1", "kind": "command", "tools": ["Bash"],
        "match_any": [r"^\s*grep\b"], "message": "grep can hang here.", "next": "use the Grep tool.",
        "examples": {"block": ["grep x f"], "allow": ["ls"]}}


def _command_pack(engine, *guards, slug="loop-pack"):
    pack = {"formatVersion": 1, "slug": slug, "title": "Loop", "version": "1.0.0", "tier": "pro", "summary": "Tests.",
            "rules": [RULE], "guards": list(guards)}
    save(engine.dir / "packs", pack)
    return pack


def _bash(command: str, session: str = "s1") -> dict:
    out = ge.decide({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command},
                     "session_id": session})
    return json.loads(out)["hookSpecificOutput"] if out else {}


def test_a_stop_is_recorded_and_the_second_says_every_variant_is_stopped(engine):
    _command_pack(engine, GREP)
    first = _bash("grep x f")["permissionDecisionReason"]
    assert 'guard t.grep: Next: use the Grep tool. grep can hang here.' in first  # labelled, the next call first
    assert "times this session" not in first
    second = _bash("grep -n y g")["permissionDecisionReason"]  # another variant of the same slip
    assert "Stopped 2 times this session: every variant of this is stopped the same way" in second
    assert "times this session" not in _bash("grep z h", session="s2")["permissionDecisionReason"]  # per session
    events = [json.loads(line) for f in (engine.dir / "guard-events").glob("*.jsonl")
              for line in f.read_text(encoding="utf-8").splitlines()]
    assert [e["decision"] for e in events] == ["blocked"] * 3
    # never the command itself: its class and a fingerprint of its form (R&D's rules review v2), and retry_of on a
    # second stop of the same form in the same session
    assert all(set(e) <= {"t", "session", "guard", "class", "decision", "fp", "retry_of"} for e in events)
    assert all(e["class"] == e["guard"] and len(e["fp"]) == 12 for e in events)
    assert not any("grep" in json.dumps({k: v for k, v in e.items() if k not in ("guard", "class")}) for e in events)


def test_the_same_form_again_in_a_session_is_a_retry(engine):
    """retry_of: the block message didn't teach (R&D's rules review v2). Same session, same form, within 10 minutes."""
    _command_pack(engine, GREP)
    _bash("grep x f")
    _bash("grep y g")                     # the same form: a retry
    _bash("grep -n z h")                  # another form: not a retry
    events = [json.loads(line) for f in (engine.dir / "guard-events").glob("*.jsonl")
              for line in f.read_text(encoding="utf-8").splitlines()]
    assert "retry_of" not in events[0] and events[1]["retry_of"] == events[0]["t"] and "retry_of" not in events[2]


def test_lessons_at_session_start_quote_the_next_call_of_repeated_stops(engine):
    _command_pack(engine, GREP)
    install(engine, {"id": "t.lessons", "check": "lessons"}, slug="core-like")
    start = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "new"}
    _bash("grep x f", session="a")
    assert ge.decide(start) is None  # one stop isn't a lesson yet
    _bash("grep x f", session="b")
    _bash("grep y f", session="c")
    text = json.loads(ge.decide(start))["hookSpecificOutput"]["additionalContext"]
    assert ("Lessons from guard stops on this machine: grep in the shell (t.grep, pack loop-pack): stopped 3 times in "
            "7 days. Next time: use the Grep tool.") in text
    engine.clock.t += 8 * 86400  # a week later the lesson has aged out
    assert ge.decide(start) is None


@pytest.mark.parametrize("bad", ["", "x" * 161, "line one\nline two", 5])
def test_next_must_be_short_plain_text(bad):
    pack = {"formatVersion": 1, "slug": "p", "title": "P", "version": "1.0.0", "tier": "free", "summary": "S.",
            "rules": [RULE], "guards": [{**GREP, "next": bad}]}
    assert any("next must be plain text" in p for p in ge.validate_pack(pack))



def test_answer_length_counts_words_like_the_platform_hook():
    """to-do #37: the engine's prose_words and .claude/hooks/answer-length.py must agree (the pack's check)."""
    import importlib.util
    from pathlib import Path as _P
    import guard_engine as ge
    hook = _P(__file__).resolve().parents[2] / ".claude" / "hooks" / "answer-length.py"
    if not hook.is_file():
        import pytest
        pytest.skip("the platform hook isn't in this copy")
    spec = importlib.util.spec_from_file_location("answer_length_hook", hook)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    samples = ["Done. Deployed.", "Here is `code` and more words.\n```\nlots of code here\n```\n| a | b |\n> quoted",
               "word " * 300, "", "Don't it's re-run ok"]
    for s in samples:
        assert ge.prose_words(s) == mod.prose_words(s), s[:30]
    assert ge.ASKED_FOR_DETAIL.pattern == mod.ASKED_FOR_DETAIL.pattern


def test_answer_length_is_a_builtin_with_bounded_max_words():
    import guard_engine as ge
    b = ge.BUILTINS["answer-length"]
    assert b["events"] == ("Stop", "UserPromptSubmit") and b["params"]["max_words"] == (250, 50, 2000)
