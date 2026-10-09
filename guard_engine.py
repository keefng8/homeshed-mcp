"""Rule packs' guard engine: the Claude Code hook that runs every installed pack's checks before a command runs or a file
is written (PreToolUse) and, for the built-in checks that need them, when a prompt arrives (UserPromptSubmit), a session
starts or compacts (SessionStart) and a turn ends (Stop). HomeShed's public port (2026-10-01) of the engine its owner's
own platform runs; `homeshed-mcp packs on` adds it to Claude Code (rule_packs.py).

A pack is data, never code: a JSON file (formatVersion 1) of rules and guards.
- A command guard names the tools it watches, a field (command, file_path, content, or input: a tool-server call's
  arguments as sorted JSON), regexes (match_any, optional match_all / unless / paths / skip_paths), a decision (block,
  ask, warn or remind), a message in plain words, and examples it must block and allow. validate_pack proves every
  example before a pack is installed.
- A built-in guard names one of the checks in BUILTINS (code that ships in this file and is reviewed with it), with
  optional settings: the checks that count, remember or read the conversation, which a pattern can't.
Unlike the owner's platform, a public pack can never name a script (`kind: hook` is refused).

`packs on` copies this file (standard library only) into HomeShed's data folder, next to the packs, and Claude Code
runs that copy with the base Python, so an upgrade or a uv cache clean can't break the hook (the prepper's review):
    "<python>" -I -S "<data>/guard_engine.py" --packs "<data>/packs" --modes "<data>/guard-modes.json"
A pack file loads only when its slug matches its name; the modes file can set any guard to block, ask, warn, remind or
off. The engine FAILS OPEN: a broken pack, a bad pattern, a bad argument or any error lets the call through and prints
nothing, because a guard problem must never stop work. It reads the event from stdin and prints its answer; it sends
nothing anywhere. The built-in checks keep one small state file per session in <data>/guard-state.

Where a pack came from (every bit of its text reaching Claude or the user is labelled with it) is the installer's
record, packs/.installed.json: a source and the file's hash. A pack whose file no longer matches, or that was dropped
into the folder, is "unverified": its checks still run, but it can't ask the user anything or add a session note.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from time import gmtime as _gmtime, strftime as _strftime  # the event log's day names, whatever clock a test supplies
from datetime import datetime
from pathlib import Path

ENGINE_VERSION = "3"   # `packs on` copies this file; doctor compares the copy's version with the package's
MAX_TEXT = 100_000     # each check reads at most this much of a command or file: bounds the time a pattern can take
MAX_MESSAGE = 300      # a pack's message reaches Claude's context: short, plain text, always labelled (R&D's review)
MAX_PACK_BYTES = 256_000
WHICH_TTL = 3600       # a program installed just now is seen within the hour
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# A group with a repeat inside, repeated again without a bound: (a+)+, (\w*\s?)*, (?:x+){2,}. The classic catastrophic
# backtracking shape (R&D's review); the timed trial below catches the shapes this lint can't see.
NESTED = re.compile(r"\((?:[^()\\]|\\.)*[+*](?:[^()\\]|\\.)*\)\s*(?:[+*]|\{\d*,\})")
_GROUP_OPEN = re.compile(r"^\((?:\?(?::|P<\w+>|<\w+>))?")


def runaway_shape(pattern: str) -> bool:
    """A nested repeat that can backtrack for ever. Not when each round must begin with one fixed character, as in
    Safe Operator's (?:-\\w+\\s+)*: the "-" keeps the rounds apart, so there's only one way to split the text (found
    2026-10-01 checking the owner's packs; the timed trial agreed it's fast)."""
    for m in NESTED.finditer(pattern):
        body = _GROUP_OPEN.sub("", m.group(0))
        if body[:1] == "\\":
            if not body[1:2] or body[1:2].isalnum():
                return True  # \w, \s, \d...: a class of characters, not a fixed one
            after = body[2:3]  # an escaped mark such as \- or \. is a fixed character
        elif not body[:1] or body[:1] in "[(.^$|)":
            return True
        else:
            after = body[1:2]
        if after in ("*", "+", "?", "{"):
            return True  # the fixed character is itself optional or repeated, so it can't keep the rounds apart
    return False
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f]")  # ANSI sequences, then any control character
# Set from the command line (main), or by rule_packs.py when it checks packs in-process.
PACKS_DIR: Path | None = Path(os.environ["HOMESHED_PACKS_DIR"]) if os.environ.get("HOMESHED_PACKS_DIR") else None
MODES_FILE: Path | None = Path(os.environ["HOMESHED_GUARD_MODES"]) if os.environ.get("HOMESHED_GUARD_MODES") else None
STATE_DIR: Path | None = Path(os.environ["HOMESHED_GUARD_STATE"]) if os.environ.get("HOMESHED_GUARD_STATE") else None

FIELDS = {"command": ("command",), "file_path": ("file_path", "notebook_path"),
          "content": ("content", "new_string", "new_source")}
TOOLS = {"Bash", "PowerShell", "Write", "Edit", "NotebookEdit", "SendMessage", "Agent", "Read"}
MCP_TOOL = re.compile(r"^mcp__[A-Za-z0-9_-]+__[A-Za-z0-9_-]+$")  # a tool-server tool, e.g. mcp__homeshed__git_push
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
KNOWN_BUG = re.compile(r"^(?:PRO-)?KB-\d{4}$")
PLATFORMS = {"windows": "win32", "linux": "linux", "macos": "darwin"}
WHEN_KEYS = {"os", "installed"}
PROGRAM = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
MAX_PATTERN = 400
# block stops the call; ask has the user confirm it (Claude Code shows the reason in its permission prompt, even in
# auto mode); warn tells Claude and lets it run; remind stays silent.
DECISIONS = ("block", "ask", "warn", "remind")
MODES = DECISIONS + ("off",)
HOOK_EVENTS = ("PreToolUse", "UserPromptSubmit", "SessionStart", "Stop")


def strict_loads(text: str):
    """JSON with no NaN or Infinity and no repeated keys: a pack is read exactly one way (R&D's review)."""
    def refuse_constant(name):
        raise ValueError(f"{name} isn't allowed in a pack")

    def no_repeats(pairs):
        keys = [k for k, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("a key appears twice")
        return dict(pairs)
    return json.loads(text, parse_constant=refuse_constant, object_pairs_hook=no_repeats)


def clean(text, limit: int = MAX_MESSAGE) -> str:
    """Pack text bound for Claude's context: no control characters or terminal escapes, and short."""
    return _CONTROL.sub(" ", str(text or ""))[:limit].strip()


INDEX = ".installed.json"  # in the packs folder: what the installer saved, with each file's hash (R&D's review)
# "server": a Pro pack fetched through a HomeShed server (a Docker install's CLI). Until packs carry the Pro site's
# signature, its session notes, like a file pack's, wait for the user's OK (R&D's security review, 2026-10-01).
SOURCES = {"pro": "HomeShed Pro", "server": "HomeShed Pro, through your HomeShed server", "file": "added from a file"}


def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_index(folder: Path) -> dict:
    """{slug: {"source": "pro" | "file", "sha256", "notes_ok"?}}, written only by rule_packs.py's installer."""
    try:
        index = json.loads((Path(folder) / INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    return index if isinstance(index, dict) else {}


def load_packs(folder: Path | None = None) -> list[dict]:
    """Every readable formatVersion 1 pack whose slug matches its file name. Anything else is skipped, never fatal.
    Where a pack came from is never read from the pack itself, where anyone could write "pro": it comes from the
    installer's index, and only while the file's hash still matches. A pack dropped into the folder, or edited after
    install, is `unverified` (R&D's review): it still runs, but can't ask the user anything or add a session note."""
    folder = folder or PACKS_DIR
    if folder is None:
        return []
    folder, packs = Path(folder), []
    index = read_index(folder)
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        if f.name.startswith("."):
            continue
        try:
            raw = f.read_bytes()
            pack = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(pack, dict) and pack.get("formatVersion") == 1 and pack.get("slug") == f.stem:
            entry = index.get(f.stem) if isinstance(index.get(f.stem), dict) else {}
            verified = entry.get("source") in SOURCES and entry.get("sha256") == file_hash(raw)
            pack["_source"] = entry["source"] if verified else "unverified"
            pack["_notes_ok"] = verified and (entry["source"] == "pro" or entry.get("notes_ok") is True)
            packs.append(pack)
    return packs


def guard_modes() -> dict:
    if MODES_FILE is None:
        return {}
    try:
        modes = json.loads(Path(MODES_FILE).read_text(encoding="utf-8"))
        return {k: v for k, v in modes.items() if v in MODES} if isinstance(modes, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def value(tool_input: dict, field: str) -> str:
    """The text a guard reads, at most MAX_TEXT of it."""
    if field == "input":  # a tool-server call has no command text: match its arguments, e.g. "force": true
        return json.dumps(tool_input, sort_keys=True, ensure_ascii=False)[:MAX_TEXT]
    for key in FIELDS.get(field, ()):
        if isinstance(tool_input.get(key), str):
            return tool_input[key][:MAX_TEXT]
    return ""


def applies_here(guard: dict) -> bool:
    """A guard's optional `when`: {"os": [...]} and/or {"installed": "rtk"} (a program on PATH), so a guard about one
    environment's failure stays silent everywhere else. Checked only after a guard matched."""
    when = guard.get("when") or {}
    oses = when.get("os") or []
    if oses and not any(sys.platform.startswith(PLATFORMS.get(o, "?")) for o in oses):
        return False
    program = when.get("installed")
    return not program or installed_program(program)


def installed_program(program: str) -> bool:
    """shutil.which, remembered for an hour next to the packs: each hook call is a new process, and a PATH lookup is
    slow on Windows."""
    found = None
    cache_file = Path(PACKS_DIR).parent / "which-cache.json" if PACKS_DIR else None
    now, cache = time.time(), {}
    if cache_file:
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
            cache = cache if isinstance(cache, dict) else {}
        except (OSError, ValueError):
            cache = {}
        hit = cache.get(program)
        if isinstance(hit, list) and len(hit) == 2 and now - float(hit[1]) < WHICH_TTL:
            found = bool(hit[0])
    if found is None:
        found = shutil.which(program) is not None
        if cache_file:
            cache[program] = [found, now]
            try:
                tmp = cache_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(cache), encoding="utf-8")
                tmp.replace(cache_file)
            except OSError:
                pass
    return found


# A heredoc: the line that opens it, its body, and the line that closes it (the delimiter alone on a line).
_HEREDOC = re.compile(r"(<<-?[ \t]*(['\"]?)([A-Za-z_]\w*)\2[^\n]*\n)(?:.*\n)*?[ \t]*\3[ \t]*(?:\n|$)")


def strip_heredocs(text: str) -> str:
    """The command without its heredoc bodies (each opening line stays): a body is data for another program."""
    return _HEREDOC.sub(lambda m: m.group(1), text)


_QUOTED = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*\"""")


def strip_quoted(text: str) -> str:
    """The command with what's inside its quotes left out (`ignore_quoted`): words in a commit message or an echo
    aren't commands."""
    return _QUOTED.sub("''", text)


# --- backslash-collapse (KB-0031), a built-in check this engine runs itself -------------------------------------------
# Claude Code's Bash tool on Windows halves a run of backslashes before bash sees it, unless the run sits right before a
# double quote: 'a\\b' arrives as a\b, 'a\\\b' as a\\b, and \\\" is kept (measured with printf, 2026-10-01). Nothing
# errors; the text is just different. Where that changes what the command does (checked against real bash, every
# context x run length x next character; the platform's hook engine runs the same code, a test keeps them equal):
#   single quotes, a quoted heredoc's body: any run of 2 or more
#   double quotes, an unquoted heredoc's body: 3 or more, or 2 before $, ` or the line's end
#   unquoted: 2, 4 or more, or 3 before anything but a plain character (a letter, digit or _ . / : , = + @ % ^ -)
#   $'...': 3 or more, or 2 before one of its escape letters (n, t, x, a digit, the closing quote...)
# Comments are skipped. One regex-free pass, so a long command can't make it slow.
_RUNS = re.compile(r"\\{2,}")
_NEXT = re.compile(r"[\\'\"$()#<\n]")
_HEREDOC_OP = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|(\\?[A-Za-z_][\w.-]*))")
_WORD_START = " \t\n;&|()"
_PLAIN = re.compile(r"[A-Za-z0-9_./:,=+@%^-]")
_ANSI_ESCAPES = "abeEfnrtv\\'\"?01234567xuUc"
_WHERE = {"'": "in single quotes", "$'": "in $'...'", "<<'": "in a quoted heredoc", "u": "unquoted",
          '"': "in double quotes", "<<": "in a heredoc"}


def _changed(text: str, i: int, run: int, where: str) -> bool:
    """Whether halving the run of `run` backslashes at text[i] changes what bash makes of it, by context."""
    after = text[i + run] if i + run < len(text) else "\n"
    if after == '"':
        return False  # kept as typed
    if where in ('"', "<<"):
        return run >= 3 or after in "$`\n"
    if where == "u":
        return run != 3 or not _PLAIN.match(after)
    if where == "$'":
        return run >= 3 or after in _ANSI_ESCAPES
    return True  # single quotes and quoted heredocs keep every backslash


def _first_changed(text: str, where: str) -> int:
    for m in _RUNS.finditer(text):
        if _changed(text, m.start(), m.end() - m.start(), where):
            return m.start()
    return -1


# Python that reads its code from this command (a heredoc after `python -` or a bare `python`, or `python -c '...'`)
# changes only if Python parses the halved code differently: "D:\\Data" and "D:\Data" are the same string, "\\n"
# and "\n" aren't. Such code is compared as Python sees it (ast), and left out of the check when it's the same program
# (on 3 weeks of real calls this took 43 of 235 hits away, every one a program that parsed the same).
_PY = r"\bpython[\w.]*(?:\.exe)?(?:\s+-[A-Za-z]+)*"
_PY_HEREDOC = re.compile(_PY + r"(?:\s+-)?(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s<|;&\n]+(?<!\.py)))*?\s*<<(-?)[ \t]*"
                         r"(['\"]?)([A-Za-z_]\w*)\2[^\n]*\n(.*?)\n[ \t]*\3[ \t]*(?=\n|$)", re.S)
_PY_C = re.compile(_PY + r"\s+-c\s+(?:'([^']*)'|\"((?:[^\"\\]|\\.)*)\")", re.S)


def _halve(text: str) -> str:
    """What the Bash tool delivers: each run of 2+ backslashes not right before a double quote, halved (rounding up)."""
    return re.sub(r"\\{2,}(?!\\|\")", lambda m: "\\" * ((len(m.group()) + 1) // 2), text)


def _dq_unescape(text: str) -> str:
    r"""A double-quoted string's text as bash passes it on: \\ \" \$ \` and a backslash-newline are escapes."""
    return re.sub(r"\\([\\\"$`\n])", lambda m: "" if m.group(1) == "\n" else m.group(1), text)


def _heredoc_unescape(text: str) -> str:
    """An unquoted heredoc's body as bash passes it on (its $ expansions left as they are: both versions share them)."""
    return re.sub(r"\\([\\$`\n])", lambda m: "" if m.group(1) == "\n" else m.group(1), text)


def _same_python(typed: str, halved: str) -> bool:
    import ast
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # "\P" is an invalid-escape warning, not an error
            return ast.dump(ast.parse(typed)) == ast.dump(ast.parse(halved))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False  # can't prove it's the same program: check it like any other text


def _mask_same_python(command: str) -> str:
    """The command with the backslash runs of Python code that halving leaves unchanged turned into slashes (same
    length, same quoting), so the rest of the check skips them."""
    spans = []
    for m in _PY_HEREDOC.finditer(command):
        body = m.group(4)
        fix = (lambda t: t) if m.group(2) else _heredoc_unescape  # a quoted delimiter keeps the body as typed
        if _same_python(fix(body), fix(_halve(body))):
            spans.append(m.span(4))
    for m in _PY_C.finditer(command):
        if m.group(1) is not None:
            same, span = _same_python(m.group(1), _halve(m.group(1))), m.span(1)
        else:
            same, span = _same_python(_dq_unescape(m.group(2)), _dq_unescape(_halve(m.group(2)))), m.span(2)
        if same:
            spans.append(span)
    for start, end in spans:
        part = re.sub(r"\\{2,}(?!\\|\")", lambda r: "/" * len(r.group()), command[start:end])
        command = command[:start] + part + command[end:]
    return command


def halved_backslashes(command: str) -> str:
    """The first place this Bash command changes when its backslash runs are halved, as a short note; "" if none."""
    if "\\\\" not in command:
        return ""
    command = _mask_same_python(command) if "python" in command else command
    text, n, i = command, len(command), 0
    stack, heredocs = [], []  # open '"' and '(' ($( ), a subshell); heredocs opened on this line

    def note(at: int, where: str) -> str:
        start = max(0, at - 24)
        return f"{_WHERE[where]}: {text[start:at + 16]!r}"

    while i < n:
        m = _NEXT.search(text, i)
        if not m:
            break
        i, c = m.start(), m.group()
        in_dq = bool(stack) and stack[-1] == '"'
        if c == "\\":
            run = len(text) - i - len(text[i:].lstrip("\\")) if text.startswith("\\\\", i) else 1
            where = '"' if in_dq else "u"
            if run >= 2 and _changed(text, i, run, where):
                return note(i, where)
            i += run + run % 2  # an odd run escapes the character after it
            continue
        if in_dq:
            if c == '"':
                stack.pop()
            elif text.startswith("$(", i):
                stack.append("(")
                i += 1
            i += 1
            continue
        if c == "'":
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            at = _first_changed(text[i + 1:end], "'")
            if at >= 0:
                return note(i + 1 + at, "'")
            i = end + 1
            continue
        if text.startswith("$'", i):
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            at = _first_changed(text[i + 2:min(j + 1, n)], "$'")  # with its closing quote: \' is an escape there
            if at >= 0:
                return note(i + 2 + at, "$'")
            i = j + 1
            continue
        if c == '"' or c == "(":
            stack.append(c)
        elif c == ")" and stack and stack[-1] == "(":
            stack.pop()
        elif c == "#" and (i == 0 or text[i - 1] in _WORD_START):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            op = _HEREDOC_OP.match(text, i)
            if op:
                plain = op.group(4)
                word = op.group(2) if op.group(2) is not None else op.group(3) if op.group(3) is not None else plain
                heredocs.append((word.lstrip("\\"), plain is None or plain.startswith("\\"), op.group(1) == "-"))
                i = op.end()
                continue
        elif c == "\n" and heredocs:
            i += 1
            for word, quoted, strip in heredocs:
                while i < n:
                    end = text.find("\n", i)
                    end = n if end < 0 else end
                    start, line, i = i, text[i:end], end + 1
                    if (line.lstrip("\t") if strip else line).rstrip("\r") == word:
                        break
                    at = _first_changed(line, "<<'" if quoted else "<<")
                    if at >= 0:
                        return note(start + at, "<<'" if quoted else "<<")
            heredocs = []
            continue
        i += 1
    return ""


def matches(guard: dict, tool: str, tool_input: dict, here: bool = True) -> bool:
    """Whether the guard fires on this call. here=False ignores `when` (validation checks examples everywhere)."""
    if guard.get("kind") != "command" or tool not in (guard.get("tools") or []):
        return False
    flags = re.I if guard.get("ignore_case") else 0
    hit = lambda patterns, text: any(re.search(p, text, flags) for p in patterns or [])  # noqa: E731
    path = value(tool_input, "file_path")
    if guard.get("paths") and not hit(guard["paths"], path):
        return False
    if hit(guard.get("skip_paths"), path):
        return False
    text = value(tool_input, guard.get("field") or "command")
    if guard.get("ignore_heredocs"):
        text = strip_heredocs(text)
    if guard.get("ignore_quoted"):
        text = strip_quoted(text)
    if not text or not hit(guard.get("match_any"), text):
        return False
    if not all(re.search(p, text, flags) for p in guard.get("match_all") or []):
        return False
    if hit(guard.get("unless"), text):
        return False
    return not here or applies_here(guard)


def _source(pack: dict) -> str:
    """The label every bit of pack text carries: where the pack came from, as the installer's index says."""
    return SOURCES.get(pack.get("_source"), "unverified")


def _found(guard: dict, pack: dict, how: str) -> dict:
    return {**guard, "_pack": pack.get("slug"), "_how": how, "_from": _source(pack),
            "_verified": pack.get("_source") in SOURCES, "_notes_ok": pack.get("_notes_ok") is True}


def evaluate(tool: str, tool_input: dict, packs=None, modes=None, here: bool = True):
    """The command guards' verdict: (the guard that stops the call, or else the first that asks the user to confirm it,
    or None; [guards to warn or remind about]). Each guard found carries `_pack`, `_how` and `_from`. An unverified
    pack can't put words in front of the user: its `ask` becomes `warn` (R&D's review)."""
    packs = load_packs() if packs is None else packs
    modes = guard_modes() if modes is None else modes
    noted, asking = [], None
    for pack in packs:
        for guard in pack.get("guards") or []:
            try:
                if not matches(guard, tool, tool_input, here):
                    continue
            except (re.error, TypeError):
                continue  # a bad or non-text pattern is skipped: fail open (validate_pack reports it)
            how = modes.get(guard.get("id"), guard.get("decision", "block"))
            if how == "ask" and pack.get("_source") not in SOURCES and here:
                how = "warn"
            found = _found(guard, pack, how)
            if how in ("warn", "remind"):
                noted.append(found)
            elif how == "ask":
                asking = asking or found
            elif how != "off":
                return found, noted
    return asking, noted


# --- Built-in checks (2026-10-01) ---------------------------------------------------------------------------------------
# Rules a pattern can't check: they count, remember, or read the conversation so far. The checks ship here, reviewed
# with the rest of the engine, and a pack only names one, with optional settings inside the ranges in BUILTINS:
#     {"id": "polling.guard", "rule": "never-poll", "kind": "builtin", "check": "polling", "decision": "block"}
# so a pack still never carries code. Ported from the owner's platform hooks (no-polling-guard.py, context-monitor.py,
# delegation-check.py and task-protocol.py) for Token Saver and Team Sessions; session-note is for Beginner Mode.
# A check's memory is one small JSON file per session (a missing or broken one is a fresh start). It reads at most the
# end of the session's transcript, which Claude Code writes as it goes: the newest records can be missing when a hook
# runs (code.claude.com/docs/en/hooks, checked 2026-10-01), and every check allows for that.
STATE_KEEP_S = 2 * 86400
TAIL_BYTES = 400_000          # during a turn: the end of the transcript, where the current work is
CONTEXT_TAIL_BYTES = 200_000  # the context check needs only the last reply's token count
STOP_TAIL_BYTES = 1_000_000   # a turn's end, and a hand-off: the whole of a long turn
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
# HomeShed's own tool-server tools by name, under whatever name the user gave the server: mcp__<server>__<name>
DELEGATION_TOOLS = ("local_ai_ask", "local_ai_summarize_file", "reasoning_delegate", "reasoning_pipeline")
MEMORY_TOOLS = ("memory_recall", "memory_recall_facts", "memory_recall_relevant")
ROUTING_TOOLS = ("reasoning_route", "reasoning_pipeline", "reasoning_decompose_task", "local_ai_classify_complexity")
TOOL_END = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TEMP = tuple({d.replace("\\", "/").lower().rstrip("/") + "/"   # the temporary folders (never a whole drive)
               for d in [os.environ.get(k) for k in ("TMPDIR", "TEMP", "TMP")] + ["/tmp", "/var/folders"]
               if d and len(d.rstrip("/\\")) > 3})


def _state_path(session) -> Path | None:
    base = STATE_DIR or (Path(PACKS_DIR).parent / "guard-state" if PACKS_DIR else None)
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(session or ""))[:80]
    return base / f"{safe}.json" if base and safe else None


def load_state(session) -> dict:
    path = _state_path(session)
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path else {}
    except (OSError, ValueError, RecursionError):
        return {}
    return state if isinstance(state, dict) else {}


def save_state(session, state: dict) -> None:
    """Atomic (a temporary file, then a rename) and never raises: a check that can't remember starts afresh."""
    path = _state_path(session)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists()
        tmp = path.with_name(f".{path.stem}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
        if fresh:
            _forget_old_state(path.parent)
    except OSError:
        pass


def _forget_old_state(folder: Path) -> None:
    """A new session's first state file tidies away those of sessions idle for STATE_KEEP_S."""
    now = time.time()
    for f in list(folder.glob("*.json")) + list(folder.glob(".*.tmp")):
        try:
            if now - f.stat().st_mtime > STATE_KEEP_S:
                f.unlink()
        except OSError:
            pass


def transcript_tail(path, limit: int = TAIL_BYTES) -> list[dict]:
    """The last records of a session's transcript (one JSON object per line, oldest first), from at most `limit` bytes
    at its end."""
    if not path:
        return []
    try:
        with open(str(path), "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - limit))
            lines = f.read(limit).decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return []
    records = []
    for line in lines[1:] if size > limit else lines:  # the first line was cut part-way
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):  # deeply nested JSON raises RecursionError: skip that line only
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _content(record: dict):
    message = record.get("message")
    return message.get("content") if isinstance(message, dict) else None


def prompt_text(record: dict) -> str | None:
    """The text of a prompt that started a turn (the user's, or another session's message), else None. Tool results,
    slash-command output, compaction summaries and background-job notices don't start new work."""
    if record.get("type") != "user" or record.get("isMeta") or record.get("isCompactSummary"):
        return None
    content = _content(record)
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        content = " ".join(str(c.get("text") or "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    if not isinstance(content, str) or not content.strip():
        return None
    return None if content.lstrip().startswith(("<local-command", "<command-", "<task-notification")) else content


def _tool_uses(record: dict) -> list[dict]:
    content = _content(record)
    if record.get("type") != "assistant" or not isinstance(content, list):
        return []
    return [c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"]


def _args(use: dict) -> dict:
    return use.get("input") if isinstance(use.get("input"), dict) else {}


def _failed(records: list[dict]) -> set:
    """The ids of the tool calls that came back as errors: a refused or failed call changed nothing."""
    out = set()
    for record in records:
        content = _content(record)
        if record.get("type") == "user" and isinstance(content, list):
            out.update(c.get("tool_use_id") for c in content
                       if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"))
    return out


def _turn(records: list[dict]) -> tuple[list[dict], dict | None]:
    """(the records of the current turn, the prompt record that started it, or None when that's before the tail)."""
    for i in range(len(records) - 1, -1, -1):
        if prompt_text(records[i]) is not None:
            return records[i + 1:], records[i]
    return records, None


def _epoch(stamp) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _named(tool: str, names) -> bool:
    """One of HomeShed's tool-server tools, whatever the user called the server: mcp__<server>__<name>."""
    return tool.startswith("mcp__") and tool.rsplit("__", 1)[-1] in names


class Call:
    """One hook event as the built-in checks see it, with this session's state."""

    def __init__(self, event: dict):
        self.event = event
        self.name = str(event.get("hook_event_name") or "PreToolUse")
        self.session = str(event.get("session_id") or "")
        self.tool = str(event.get("tool_name") or "")
        self.input = event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {}
        self.now = time.time()
        self.state = load_state(self.session) if self.session else {}
        self.changed = False
        self._if_run: list = []
        self._tails: dict = {}

    def tail(self, limit: int = TAIL_BYTES) -> list[dict]:
        if limit not in self._tails:
            self._tails[limit] = transcript_tail(self.event.get("transcript_path"), limit)
        return self._tails[limit]

    def set(self, key: str, value) -> None:
        if self.state.get(key) != value:
            self.state[key], self.changed = value, True

    def drop(self, key: str) -> None:
        if key in self.state:
            del self.state[key]
            self.changed = True

    def if_it_runs(self, change) -> None:
        """A change to remember only if the tool call goes ahead: a refused call didn't happen."""
        self._if_run.append(change)

    def finish(self, ran: bool) -> None:
        for change in self._if_run if ran else []:
            try:
                change()
                self.changed = True
            except Exception:  # noqa: BLE001 - a state change that fails is forgotten, never fatal
                pass
        if self.changed and self.session:
            save_state(self.session, self.state)


# polling: Token Saver's never-poll ------------------------------------------------------------------------------------
_SLEEP = re.compile(r"\b(?:sleep|Start-Sleep)\s+(?:-(?:Seconds|s)\s+)?(\d{1,7}(?:\.\d{1,3})?)", re.I)
_LOOP = re.compile(r"\b(?:for|while|until)\b", re.I)
_LOOP_BOUND = re.compile(r"\bseq\s+\d+\s+\d+|\{\d+\.\.\d+\}|deadline|timeout|AddSeconds|\$i\s*-l[te]|\bi\s*<", re.I)
_STATUS_CHECK = re.compile(r"\b(?:curl|docker\s+(?:ps|inspect|logs)|netstat|Get-NetTCPConnection|Get-Process|schtasks"
                           r"|health|status)\b", re.I)
_POLL_NOTE = re.compile(r"poll-required:\s*\S", re.I)
# A sleep inside a detached job runs without this session waiting for it (R&D's rules review, 2026-10-01).
_DETACHED = re.compile(r"\bdocker\s+(?:exec|run)\b[^|;&\n]{0,120}?\s-d\b|\b(?:nohup|setsid|Start-Process)\b"
                       r"|\bstart\s+/b\b", re.I)


def _polling(call: Call, p: dict, how: str, guard: dict) -> list:
    """Waiting by sleeping, or asking the same thing over and over, instead of waiting to be told. Allowed: a short
    pause between steps, one bounded wait-until loop inside a single command, a command run in the background (it
    reports when it ends), and any command with a 'poll-required: <reason>' note."""
    if call.name != "PreToolUse" or call.tool not in ("Bash", "PowerShell") or call.input.get("run_in_background"):
        return []
    command = call.input.get("command")
    command = command[:MAX_TEXT] if isinstance(command, str) else ""
    if not command or _POLL_NOTE.search(command):
        return []
    why = None
    sleeps = [] if _DETACHED.search(command) else [float(s) for s in _SLEEP.findall(command)]
    if sleeps:
        longest, loop = max(sleeps), bool(_LOOP.search(command) and _LOOP_BOUND.search(command))
        if loop and longest > p["max_loop_step_s"]:
            why = f"a wait loop's step of {longest:g} s is longer than {p['max_loop_step_s']} s."
        elif not loop and longest > p["max_sleep_s"]:
            why = f"it sleeps {longest:g} s, more than a {p['max_sleep_s']} s pause between steps."
    if why is None and _STATUS_CHECK.search(command):
        key = " ".join(command.split())[:300]
        recent = [h for h in call.state.get("polling") or [] if isinstance(h, list) and len(h) == 2
                  and isinstance(h[0], (int, float)) and call.now - h[0] < p["window_s"]]
        if sum(1 for _, k in recent if k == key) >= p["max_repeats"]:
            why = (f"this same status check already ran {p['max_repeats']} times in the last "
                   f"{max(1, round(p['window_s'] / 60))} minutes.")
        call.if_it_runs(lambda: call.state.__setitem__("polling", (recent + [[call.now, key]])[-50:]))
    if why is None:
        return []
    return [("deny" if how == "block" else "note",
             f"No-polling check: {why} Wait for the completion notice instead: run it in the background, use one "
             "bounded wait-until loop inside a single command, or add a 'poll-required: <reason>' note when waiting "
             "here really is needed.")]


# context-alarm: Token Saver's save-before-compaction ------------------------------------------------------------------
def _native_1m(model: str) -> bool:
    """Models Claude Code runs with a 1M window on Anthropic's own API: Fable, Sonnet 5 and later, Opus 4.7 and later
    (code.claude.com/docs/en/model-config, checked 2026-10-01). A cloud provider's spelling (no leading "claude-", or
    an @date) runs at 200k there."""
    m = re.match(r"claude-(fable|sonnet|opus)-(\d{1,2})(?:-(\d{1,2}))?(?!\d)", model or "")
    if not m or "@" in model:
        return False
    family, version = m.group(1), (int(m.group(2)), int(m.group(3) or 0))
    return family == "fable" or (family == "sonnet" and version >= (5, 0)) or (family == "opus" and version >= (4, 7))


def _tokens(size) -> int | None:
    """A context size as Claude Code writes one (200000, "500k", "1M", or 100-1000 meaning thousands), in tokens."""
    if isinstance(size, bool):
        return None
    if isinstance(size, (int, float)):
        n = float(size)
    else:
        m = re.fullmatch(r"\s*(\d{1,9}(?:\.\d{1,3})?)\s*([kKmM]?)\s*", str(size or ""))
        if not m:
            return None
        n = float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()]
    if 100 <= n <= 1000:
        n *= 1000
    return int(n) if 50_000 <= n <= 2_000_000 else None


def _autocompact_setting() -> int | None:
    """/autocompact's window, which Claude Code saves in its user settings as autoCompactWindow."""
    folder = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        settings = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return _tokens(settings.get("autoCompactWindow")) if isinstance(settings, dict) else None


def compaction_point(model: str) -> int:
    """About where Claude Code compacts a session on this model, in tokens (code.claude.com/docs/en/model-config,
    checked 2026-10-01). First CLAUDE_CODE_AUTO_COMPACT_WINDOW, then /autocompact's setting, then where this model was
    last seen compacting here, then the default: near 967k on a native 1M window, else the 200k boundary.
    CLAUDE_CODE_DISABLE_1M_CONTEXT holds every model to 200k, and CLAUDE_AUTOCOMPACT_PCT_OVERRIDE can only lower it."""
    held = os.environ.get("CLAUDE_CODE_DISABLE_1M_CONTEXT", "").strip().lower() in ("1", "true")
    one_m = _native_1m(model) and not held
    learned = load_state("_context").get(model)
    if not (isinstance(learned, int) and not isinstance(learned, bool)
            and 50_000 <= learned <= (200_000 if held else 1_000_000)):
        learned = None
    window = 1_000_000 if one_m or (learned or 0) > 200_000 else 200_000  # seen compacting past 200k: a 1M window
    chosen = _tokens(os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW")) or _autocompact_setting()
    if chosen:
        point = base = min(chosen, window)
    else:
        point, base = learned or (967_000 if one_m else 200_000), window
    pct = os.environ.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "").strip()
    if pct.isdigit() and 1 <= int(pct) < 100:
        point = min(point, base * int(pct) // 100)
    return point


def _learn_compaction(records: list[dict]) -> None:
    """Where Claude Code really compacted, from an automatic compaction's size just before, kept for every session on
    that model: it covers the set-ups the rules above can't see (a gateway, a [1m] variant, a cloud provider)."""
    model = None
    for record in records:
        message = record.get("message")
        if record.get("type") == "assistant" and isinstance(message, dict) and message.get("model"):
            model = str(message["model"])[:80]
        meta = record.get("compactMetadata") if record.get("subtype") == "compact_boundary" else None
        before = meta.get("preTokens") if isinstance(meta, dict) and meta.get("trigger") == "auto" else None
        if model and isinstance(before, int) and not isinstance(before, bool):
            learned = load_state("_context")
            if learned.get(model) != before:
                learned[model] = before
                save_state("_context", learned)


def _context_alarm(call: Call, p: dict, how: str, guard: dict) -> list:
    """Context filling up: once per crossing, a note to save decisions and next steps to memory before Claude Code
    compacts the session and drops whatever wasn't saved. Looks at most every `every_s` seconds."""
    if call.name != "PreToolUse" or call.now - float(call.state.get("context_checked") or 0) < p["every_s"]:
        return []
    call.set("context_checked", call.now)
    records = call.tail(CONTEXT_TAIL_BYTES)
    _learn_compaction(records)
    used, model = 0, ""
    for record in reversed(records):
        message = record.get("message")
        usage = message.get("usage") if record.get("type") == "assistant" and isinstance(message, dict) else None
        if isinstance(usage, dict):
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                count = usage.get(key)
                used += count if isinstance(count, int) and not isinstance(count, bool) else 0
            model = str(message.get("model") or "")
            break
    if not used:
        return []
    point = compaction_point(model)
    if used < point * (p["alarm_pct"] - 10) / 100:
        call.drop("context_told")  # well under again (compacted or cleared): armed for next time
        return []
    if used < point * p["alarm_pct"] / 100 or call.state.get("context_told"):
        return []
    call.set("context_told", call.now)
    return [("note", f"Context check: this session holds about {used // 1000}k tokens, and Claude Code compacts it "
                     f"near {point // 1000}k. Save decisions and next steps to memory now, then carry on.")]


# delegation: Token Saver's local-model-first --------------------------------------------------------------------------
def _counts_as_work(path) -> bool:
    """A project file: not Claude's own memory or plan files, nor anything in the temporary folder (its scratchpad)."""
    p = str(path or "").replace("\\", "/").lower()
    return bool(p) and not (p.startswith(_TEMP) or ("/.claude/" in p and ("/memory/" in p or "/plans/" in p)))


def _files_since_delegation(records: list[dict], names) -> tuple[int, bool]:
    """(how many different project files changed since the local model last did any work, whether that last piece of
    work failed). Several edits to one file count once; a refused or failed write changed nothing."""
    failed, files = _failed(records), set()
    for record in reversed(records):
        for use in reversed(_tool_uses(record)):
            tool, path = str(use.get("name") or ""), _args(use).get("file_path") or _args(use).get("notebook_path")
            if _named(tool, names):
                return len(files), use.get("id") in failed
            if tool in WRITE_TOOLS and use.get("id") not in failed and isinstance(path, str) and _counts_as_work(path):
                files.add(path.replace("\\", "/").casefold())
    return len(files), False


def _delegation(call: Call, p: dict, how: str, guard: dict) -> list:
    """Grunt work Claude did itself when the local model could have. Two parts, as on the owner's platform: before a
    project file is written, the files changed since the local model last did any work (write_budget); and at a turn's
    end, a busy turn with too little given to it. In warn mode the turn's-end finding costs nothing: it's kept and told
    at the next prompt, riding that turn (feedback from a Stop hook always costs a turn). Stands down while the local
    model's last piece of work failed: there's nothing to give work to."""
    names = tuple(p["tools"])
    if call.name == "UserPromptSubmit":
        nudge = call.state.get("delegation_nudge")
        call.drop("delegation_nudge")
        if not isinstance(nudge, dict) or call.now - float(nudge.get("at") or 0) > 86400:
            return []
        return [("note", f"Delegation note from your last turn: {clean(nudge.get('why'), 200)} In this request, give "
                         "drafts, test lists and summaries to the local model before writing them yourself.")]
    if call.name == "PreToolUse" and call.tool in WRITE_TOOLS:
        path = call.input.get("file_path") or call.input.get("notebook_path")
        if not (isinstance(path, str) and _counts_as_work(path)):
            return []
        files, down = _files_since_delegation(call.tail(), names)
        noted = int(call.state.get("delegation_noted") or 0)
        if files < noted:
            call.drop("delegation_noted")  # the local model did some work since: counting from nothing again
            noted = 0
        if down or files < p["write_budget"] or (how == "warn" and noted and files < noted + p["write_budget"]):
            return []
        if how == "warn":
            call.set("delegation_noted", files)
        return [("deny" if how == "block" else "note",
                 f"Local model first: {files} project files changed since the local model last did any work. Give it "
                 "the next piece of grunt work (a draft, a test list, doc text or a summary: local_ai.ask, "
                 "local_ai.summarize_file or reasoning.delegate), check what it returns, then carry on.")]
    if call.name != "Stop" or call.event.get("stop_hook_active"):
        return []
    records = call.tail(STOP_TAIL_BYTES)
    calls = writes = delegations = 0
    for record in _turn(records)[0]:
        for use in _tool_uses(record):
            tool = str(use.get("name") or "")
            calls += 1
            writes += 1 if tool in WRITE_TOOLS else 0
            delegations += 1 if _named(tool, names) else 0
    if _files_since_delegation(records, names)[1]:
        return []
    if calls >= p["turn_calls"] and writes >= p["turn_writes"] and not delegations:
        why = f"{calls} tool calls and {writes} file writes, and nothing went to the local model."
    elif writes >= p["writes_per_delegation"] and delegations < writes // p["writes_per_delegation"]:
        why = f"{writes} file writes, and only {delegations} piece(s) of work went to the local model."
    else:
        return []
    if how == "block":
        return [("stop", f"Delegation check: {why} Give a remaining piece of grunt work to the local model, or say in "
                         "one line why none could go there.")]
    call.set("delegation_nudge", {"at": call.now, "why": why})
    return []


# Team Sessions --------------------------------------------------------------------------------------------------------
_PEER_ATTR = re.compile(r'\b(from|from-name)="([^"]{1,300})"')
# A message that asks or waits is owed a reply; a pure update needs none (the owner's platform, RULE-D-PROJECTS-012/014).
# Only a question or a direct request counts: bare "waiting" and "hold" made acknowledgements ("will hold off") count as
# asks (R&D's rules review, 2026-10-01). Heads-ups and short acknowledgements without a question are information.
ASKS = re.compile(r"\?|\b(?:waiting (?:on|for) (?:you|your)|your (?:go|call|ok|okay)|let me know|should i|can you|"
                  r"could you|would you|please)\b", re.I)
INFO_ONLY = re.compile(r"\bno (?:reply|response|answer|action) (?:is )?(?:needed|required)\b"
                       r"|\bnothing (?:is )?(?:needed|required) from (?:you|me)\b"
                       r"|\b(?:just letting you know|for your information|information only)\b|^\s*(?:fyi|heads[- ]up)\b"
                       r"|^(?=\s*(?:got it|understood|noted|thanks|thank you|confirmed|agreed)\b)[^?]*$", re.I)
_UNROUTED = re.compile(r"\bunrouted:\s*\S+\s+\S+\s+\S+", re.I)  # a reason of three words or more (R&D's review)
FOLLOW_UP_S, PEER_FOLLOW_UP_S = 600, 1800  # steps done this soon before a follow-up still count (the owner's platform)
OWED_KEEP_S = 7200


def peer_of(text) -> list[str] | None:
    """[address, name] of the Claude session that sent this, when it's another session's message (Claude Code wraps
    one in <cross-session-message from="..." from-name="...">), else None."""
    text = str(text or "").lstrip()
    if not text.startswith("<cross-session-message"):
        return None
    return [v for _, v in _PEER_ATTR.findall(text.split(">", 1)[0][:2000])] or ["peer"]


def peer_body(text) -> str:
    text = str(text or "")[:MAX_TEXT]
    if text.lstrip().startswith("<cross-session-message") and ">" in text:
        text = text.split(">", 1)[1]
    return " ".join(text.replace("</cross-session-message>", " ").split())


def asks_for_reply(body: str) -> bool:
    return bool(ASKS.search(body)) and not INFO_ONLY.search(body)


def _norm(name) -> str:
    """SendMessage may add " [ref]" to a name, names differ in case, and an address can carry "uds:"."""
    return re.sub(r"\s*\[[^\]]{0,40}\]$", "", str(name or "")).strip().lower().removeprefix("uds:")


_SESSION_PAIRS: list[set] | None = None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _session_pairs() -> list[set]:
    """{name, messaging address} of each session in Claude Code's list of running sessions, the newest 200, read once
    per hook process (R&D's review: the Stop check compared names in loops, re-reading every file each time)."""
    global _SESSION_PAIRS
    if _SESSION_PAIRS is None:
        _SESSION_PAIRS = []
        folder = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"
        try:
            files = sorted(folder.glob("*.json"), key=_mtime, reverse=True)[:200]
        except OSError:
            files = []
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                continue
            if isinstance(d, dict):
                pair = {_norm(d.get("name")), _norm(d.get("messagingSocketPath"))} - {""}
                if pair:
                    _SESSION_PAIRS.append(pair)
    return _SESSION_PAIRS


def aliases(name) -> set[str]:
    """A session's name and its messaging address are the same peer: Claude Code's list of running sessions pairs them
    (on the owner's platform a reply sent to the address didn't settle a reply owed to the name)."""
    out = {_norm(name)} - {""}
    for pair in _session_pairs():
        if out & pair:
            out |= pair
    return out


def same_session(a, b) -> bool:
    return bool(aliases(a) & aliases(b))


def _peer_messages(records: list[dict]) -> list[tuple]:
    """(when, [address, name], body) for each other session's message in these records: prompts that started a turn,
    and messages that arrived during one (Claude Code keeps those as queued_command attachments, with their origin)."""
    out = []
    for record in records:
        text, names = prompt_text(record), None
        attachment = record.get("attachment")
        if text is None and record.get("type") == "attachment" and isinstance(attachment, dict) \
                and attachment.get("type") == "queued_command" and isinstance(attachment.get("prompt"), str):
            text = attachment["prompt"]
            origin = attachment.get("origin")
            if isinstance(origin, dict) and origin.get("kind") == "peer":
                names = [str(v)[:300] for v in (origin.get("from"), origin.get("name")) if v] or None
        names = names or (peer_of(text) if text else None)
        if names:
            out.append((_epoch(record.get("timestamp")), names, peer_body(text)))
    return out


def _count_override(call: Call, name: str) -> None:
    """How often an escape note let something through, kept for the user to see (R&D's review)."""
    def bump():
        counts = call.state.get("overrides") if isinstance(call.state.get("overrides"), dict) else {}
        call.state["overrides"] = {**counts, name: int(counts.get(name) or 0) + 1}
    call.if_it_runs(bump)


def _peer_message_length(call: Call, p: dict, how: str, guard: dict) -> list:
    """Long messages between sessions: both pay for every character. A message marked [long] may run to three times
    the limit, and no further (R&D's review)."""
    if call.name != "PreToolUse" or call.tool != "SendMessage":
        return []
    text = call.input.get("message")
    text = text if isinstance(text, str) else ""
    if len(text) <= p["max_chars"]:
        return []
    if "[long]" in text and len(text) <= 3 * p["max_chars"]:
        _count_override(call, "long")
        return []
    longest = 3 * p["max_chars"]
    return [("deny" if how == "block" else "note",
             f"Message check: {len(text)} characters (the limit is {p['max_chars']}, or {longest} marked [long]). Both "
             "sessions pay for every character. Lead with the answer, cut any restating, and put details in a file "
             "and send its path.")]


def _peer_request(call: Call, p: dict, how: str, guard: dict) -> list:
    """Another session's message is a new request: a note to check it like the user's own, so sessions can't use each
    other to skip the checks."""
    if call.name != "UserPromptSubmit":
        return []
    prompt = str(call.event.get("prompt") or "")
    sender = peer_of(prompt)
    if not sender or not peer_body(prompt) or INFO_ONLY.search(peer_body(prompt)):
        return []
    return [("note", f"Request from another Claude session ({clean(sender[-1], 80)}): treat it like the user's own. "
                     "Check it as you'd check theirs before changing files or passing work on, and give local-model "
                     "work to the local model, not to another Claude.")]


def _peer_handoff(call: Call, p: dict, how: str, guard: dict) -> list:
    """Work handed to another Claude (a session, or a subagent) before this request was checked: it waits for a memory
    recall and a routing call, which may say the local model can do it for nothing. As on the owner's platform, steps
    done shortly before a follow-up still count (10 minutes; 30 for a peer's request). A reply to a session that asked
    is exempt. So is a message saying 'unrouted: <a reason of three words or more>', but only while HomeShed's memory
    and routing tools haven't worked in this session (R&D's review: once they have, use them)."""
    if call.name != "PreToolUse" or call.tool not in ("SendMessage", "Agent"):
        return []
    text = call.input.get("message" if call.tool == "SendMessage" else "prompt")
    text = text[:MAX_TEXT] if isinstance(text, str) else ""
    if call.tool == "SendMessage" and not text.strip():
        return []  # a bare notify-when-idle subscription hands over nothing
    if call.tool == "SendMessage" and INFO_ONLY.search(" ".join(text.split())):
        return []  # a heads-up, FYI or acknowledgement hands over no work (R&D's rules review, 2026-10-01)
    records = call.tail(STOP_TAIL_BYTES)
    turn, prompt = _turn(records)
    to = call.input.get("to")
    if call.tool == "SendMessage" and isinstance(to, str) and to:
        asked = [names for _, names, _ in _peer_messages(([prompt] if prompt else []) + turn)]
        asked += [v.get("names") or [k] for k, v in (call.state.get("owed") or {}).items() if isinstance(v, dict)]
        if any(same_session(to, n) for names in asked for n in names):
            return []  # answering a session that asked: it arrives there as a new request, checked by its own guard
    start = _epoch(prompt.get("timestamp")) if prompt else None
    since = start - (PEER_FOLLOW_UP_S if peer_of(prompt_text(prompt)) else FOLLOW_UP_S) if start else 0.0
    failed, recalled, routed, broken, worked = _failed(records), False, False, False, False
    for record in records:
        at = _epoch(record.get("timestamp")) or 0.0
        for use in _tool_uses(record):
            tool = str(use.get("name") or "")
            memory, routing = _named(tool, MEMORY_TOOLS), _named(tool, ROUTING_TOOLS)
            if not (memory or routing):
                continue
            broken = use.get("id") in failed
            worked = worked or not broken
            if not broken and at >= since:
                recalled, routed = recalled or memory, routed or routing
    if broken or (recalled and routed):
        return []  # done; or the last of those calls failed, so HomeShed's tools aren't working: don't hold work up
    if not worked and _UNROUTED.search(text):
        _count_override(call, "unrouted")
        return []
    missing = [what for what, done in (("recall memory (memory.recall_relevant)", recalled),
                                       ("route the task (reasoning.route)", routed)) if not done]
    target = "a subagent" if call.tool == "Agent" else f"session {clean(to, 80) or '?'}"
    unavailable = ("" if worked else " If HomeShed's memory and routing tools aren't available in this session, say "
                   "why in the message, as 'unrouted: <reason>'.")
    return [("deny" if how == "block" else "note",
             f"Hand-off check: before handing work to another Claude ({target}), {' and '.join(missing)}. If routing "
             "says the local model can do it, use local_ai.ask or reasoning.delegate: another Claude costs as much as "
             f"doing it here. Replies to a session that asked are exempt.{unavailable}")]


def _owed_reply(call: Call, p: dict, how: str, guard: dict) -> list:
    """Another session asked something and is waiting: before the turn ends, send a short reply with the answer, or
    when it'll come. A message that asks is owed a reply, one that says it needs none isn't, and a SendMessage to
    that session (by name or address) settles it. A turn is held once (stop_hook_active); an ask lapses after two
    hours."""
    owed = {k: v for k, v in (call.state.get("owed") or {}).items()
            if isinstance(v, dict) and call.now - float(v.get("at") or 0) < OWED_KEEP_S}
    if call.name == "UserPromptSubmit":
        prompt = str(call.event.get("prompt") or "")
        sender = peer_of(prompt)
        if sender and asks_for_reply(peer_body(prompt)):
            owed[_norm(sender[0])] = {"names": sender, "at": call.now, "said": peer_body(prompt)[:120]}
    elif call.name == "PreToolUse":
        to = call.input.get("to")
        if call.tool == "SendMessage" and isinstance(to, str) and to and str(call.input.get("message") or "").strip():
            def settle():
                call.state["owed"] = {k: v for k, v in (call.state.get("owed") or {}).items() if isinstance(v, dict)
                                      and not any(same_session(to, n) for n in v.get("names") or [k])}
                sent = [s for s in call.state.get("sent") or [] if isinstance(s, list) and len(s) == 2]
                call.state["sent"] = sent[-19:] + [[call.now, to[:300]]]
            call.if_it_runs(settle)
    elif call.name == "Stop":
        records = call.tail(STOP_TAIL_BYTES)
        since = float(call.state.get("peers_seen") or call.now - OWED_KEEP_S) - 120  # overlap: the transcript lags
        sent = [(float(s[0]), str(s[1])) for s in call.state.get("sent") or []
                if isinstance(s, list) and len(s) == 2 and isinstance(s[0], (int, float))]
        sent += [(_epoch(r.get("timestamp")) or 0.0, str(_args(u).get("to") or "")) for r in records
                 for u in _tool_uses(r) if u.get("name") == "SendMessage" and str(_args(u).get("message") or "").strip()]
        for at, names, body in _peer_messages(records):
            if at is None or at <= since or not asks_for_reply(body):
                continue
            if any(t >= at and to and any(same_session(to, n) for n in names) for t, to in sent):
                continue
            owed[_norm(names[0])] = {"names": names, "at": at, "said": body[:120]}
        call.set("peers_seen", call.now)
        call.set("owed", owed)
        if owed and not call.event.get("stop_hook_active"):
            waiting = "; ".join(
                f'{clean((v.get("names") or [k])[-1], 60)} '
                f'({max(0, round((call.now - float(v.get("at") or call.now)) / 60))} min ago: "{clean(v.get("said"), 80)}")'
                for k, v in list(owed.items())[:2])
            return [("stop", f"Don't leave a peer waiting: {waiting}. Send a short reply with the answer, or say when "
                             "you'll have it, before ending the turn.")]
        return []
    call.set("owed", owed)
    return []


# answer-length: quiet output (to-do #37) ------------------------------------------------------------------------------
ANSWER_FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)
ANSWER_INLINE = re.compile(r"`[^`\n]*`")
ANSWER_WORD = re.compile(r"[A-Za-z0-9][\w'’-]*")
ASKED_FOR_DETAIL = re.compile(
    r"(?i)\b(?:explain\w*|why|how (?:does|do|did|can|could|would|should|to|is|are|much|many)|walk me through|"
    r"in detail|details?|detailed|report|summar\w*|overview|everything|list (?:all|every)|guides?|steps|plan|"
    r"draft|write (?:a|an|the|up)|compare|pros and cons|review|what (?:changed|happened|did you))\b")


def prose_words(text: str) -> int:
    """Words of prose: fenced code, table lines (|...) and quoted lines (>...) left out; inline code counts once."""
    text = ANSWER_FENCE.sub(" ", text or "")
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(("|", ">"))]
    return len(ANSWER_WORD.findall(ANSWER_INLINE.sub(" x ", "\n".join(lines))))


def _final_answer(records: list) -> tuple[str, str]:
    """(the prompt that started the last turn, the turn's final answer: its text after the last tool call)."""
    answer: list[str] = []
    done = False
    for e in reversed(records):
        if not isinstance(e, dict):
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            if any(isinstance(c, dict) and c.get("type") == "tool_use" for c in content):
                done = True
            if not done:
                answer[:0] = [c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text"]
        elif e.get("type") == "user":
            text = content if isinstance(content, str) else " ".join(
                c.get("text") or "" for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                return text, "\n".join(answer)
    return "", "\n".join(answer)


def _answer_length(call: Call, p: dict, how: str, guard: dict) -> list:
    """Quiet output (the owner, 2026-10-06: "limit output speech (fluff, waffle, unnecessary outputs)"): at the end of
    a turn, a final answer over max_words of prose, when the prompt didn't ask for an explanation, report or plan,
    leaves a note that rides the next prompt (it never costs a turn). Code, tables and quoted output don't count."""
    if call.name == "UserPromptSubmit":
        note = call.state.get("answer_note")
        if not note:
            return []
        call.set("answer_note", None)
        return [("note", clean(note, 400))]
    if call.name != "Stop" or call.event.get("stop_hook_active"):
        return []
    prompt, answer = _final_answer(call.tail(STOP_TAIL_BYTES))
    words = prose_words(answer)
    if words > p["max_words"] and not ASKED_FOR_DETAIL.search(prompt or ""):
        call.set("answer_note", f"Quiet output: your last answer was {words} words of prose (budget {p['max_words']}). "
                                "Lead with the answer, then at most three short lines; cut recap and filler. Code, "
                                "tables and quoted output don't count.")
    return []


# session-note: Beginner Mode ------------------------------------------------------------------------------------------
def _session_note(call: Call, p: dict, how: str, guard: dict) -> list:
    """The pack's own note, added to Claude's context when a session starts, resumes, is cleared or has been
    compacted, so it lasts the whole session. Only from a verified Pro pack, or a pack whose note the user read and
    confirmed at `packs add` (R&D's review: this text reaches Claude every session, so its label must be true)."""
    if call.name != "SessionStart" or not guard.get("message") or not guard.get("_notes_ok"):
        return []
    return [("note", clean(guard["message"]))]


def _lessons(call: Call, p: dict, how: str, guard: dict) -> list:
    """The self-learning loop at a session's start (startup, resume, clear, compact): the guards that stopped sessions
    here at least `min_stops` times in `days` days, most first, each with its pack's next call, so a lesson outlives
    the context window it was learned in. A guard with no next call is left out: a lesson needs one."""
    if call.name != "SessionStart":
        return []
    counts: dict[str, int] = {}
    for e in read_events(p["days"], call.now):
        cls = e.get("class") or e.get("guard")
        if e.get("decision") == "blocked" and isinstance(cls, str):
            counts[cls] = counts.get(cls, 0) + 1  # by class: every variant of a slip adds to one lesson
    if not counts:
        return []
    guide = {}
    for pack in load_packs():
        for g in pack.get("guards") or []:
            if isinstance(g, dict) and isinstance(g.get("id"), str) and g.get("next"):
                guide.setdefault(slip_class(g), (clean(g.get("title") or g["id"], 80), clean(g["next"], MAX_NEXT),
                                                 clean(pack.get("slug"), 41)))
    lines = []
    for cls, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if n < p["min_stops"] or len(lines) >= p["limit"]:
            break
        if cls in guide:
            title, nxt, slug = guide[cls]
            lines.append(f"{title} ({clean(cls, 64)}, pack {slug}): stopped {n} times in {p['days']} days. Next time: {nxt}")
    return [("note", "Lessons from guard stops on this machine: " + " | ".join(lines))] if lines else []


def _backslash_collapse(call: Call, p: dict, how: str, guard: dict) -> list:
    """KB-0031: a Bash command with backslash runs the Bash tool would halve on the way to bash, where that changes
    what the command does (halved_backslashes). Only where the pack's `when` says it happens: Windows."""
    if call.name != "PreToolUse" or call.tool != "Bash" or not applies_here(guard):
        return []
    found = halved_backslashes(value(call.input, "command"))
    if not found:
        return []
    text = clean(guard.get("message") or "This command loses backslashes on the way to bash.") + f" Found {clean(found, 120)}."
    if guard.get("known_bug"):
        text += f" This is known bug {clean(guard['known_bug'], 12)}."
    return [("deny" if how == "block" else "note", text)]


# Each check: its code, the events it listens to, the decisions it supports (the first is its default) and its settings
# as (default, lowest, highest); a tuple default is a list of tool names. "examples": the function a pack's block and
# allow examples are checked with, for a check that reads one command.
BUILTINS = {
    "polling": {"run": _polling, "events": ("PreToolUse",), "decisions": ("block", "warn", "remind"),
                "params": {"max_sleep_s": (5, 1, 60), "max_loop_step_s": (10, 1, 120), "max_repeats": (3, 1, 20),
                           "window_s": (300, 60, 3600)}},
    "context-alarm": {"run": _context_alarm, "events": ("PreToolUse",), "decisions": ("warn", "remind"),
                      "params": {"alarm_pct": (90, 50, 95), "every_s": (30, 10, 600)}},
    "delegation": {"run": _delegation, "events": ("PreToolUse", "Stop", "UserPromptSubmit"),
                   "decisions": ("warn", "block", "remind"),
                   "params": {"write_budget": (12, 4, 50), "turn_calls": (10, 3, 100), "turn_writes": (3, 1, 50),
                              "writes_per_delegation": (8, 2, 50), "tools": (DELEGATION_TOOLS, None, None)}},
    "peer-message-length": {"run": _peer_message_length, "events": ("PreToolUse",),
                            "decisions": ("block", "warn", "remind"), "params": {"max_chars": (1500, 200, 20_000)}},
    "peer-request": {"run": _peer_request, "events": ("UserPromptSubmit",), "decisions": ("warn", "remind"),
                     "params": {}},
    "peer-handoff": {"run": _peer_handoff, "events": ("PreToolUse",), "decisions": ("block", "warn", "remind"),
                     "params": {}},
    "owed-reply": {"run": _owed_reply, "events": ("UserPromptSubmit", "PreToolUse", "Stop"),
                   "decisions": ("block", "remind"), "params": {}},
    "session-note": {"run": _session_note, "events": ("SessionStart",), "decisions": ("warn", "remind"),
                     "params": {}, "needs_message": True},
    "answer-length": {"run": _answer_length, "events": ("Stop", "UserPromptSubmit"), "decisions": ("warn", "remind"),
                      "params": {"max_words": (250, 50, 2000)}},
    "backslash-collapse": {"run": _backslash_collapse, "events": ("PreToolUse",), "decisions": ("block", "warn", "remind"),
                           "params": {}, "examples": halved_backslashes},
    "lessons": {"run": _lessons, "events": ("SessionStart",), "decisions": ("warn", "remind"),
                "params": {"days": (7, 1, 30), "min_stops": (2, 1, 20), "limit": (3, 1, 5)}},
}


def _valid_setting(spec: tuple, given) -> bool:
    default, low, high = spec
    if isinstance(default, tuple):
        return isinstance(given, list) and 0 < len(given) <= 20 and all(isinstance(v, str) and TOOL_END.match(v)
                                                                      for v in given)
    return isinstance(given, int) and not isinstance(given, bool) and low <= given <= high


def _settings(check: dict, given) -> dict:
    """The check's settings: the pack's where they're valid, the defaults otherwise."""
    given, out = (given if isinstance(given, dict) else {}), {}
    for key, spec in check["params"].items():
        if key in given and _valid_setting(spec, given[key]):
            out[key] = tuple(given[key]) if isinstance(spec[0], tuple) else given[key]
        else:
            out[key] = spec[0]
    return out


def _next_problems(guard: dict) -> list[str]:
    """A guard's `next`, the one call to make instead: pack text that reaches Claude, so plain and short."""
    nxt = guard.get("next")
    if nxt is None:
        return []
    if not isinstance(nxt, str) or not nxt.strip() or len(nxt) > MAX_NEXT or clean(nxt, MAX_NEXT) != nxt.strip():
        return [f"{guard.get('id')}: next must be plain text of {MAX_NEXT} characters at most"]
    return []


def _builtin_problems(guard: dict) -> list[str]:
    gid, check = guard.get("id"), BUILTINS.get(guard.get("check"))
    if check is None:
        return [f"{gid}: there's no built-in check called {clean(guard.get('check'), 40)!r}"]
    problems = []
    if guard.get("decision", check["decisions"][0]) not in check["decisions"]:
        problems.append(f"{gid}: decision must be one of {', '.join(check['decisions'])} for {guard['check']}")
    given = guard.get("settings", {})
    if not isinstance(given, dict) or not set(given) <= set(check["params"]):
        problems.append(f"{gid}: {guard['check']} takes only these settings: {', '.join(check['params']) or 'none'}")
    else:
        problems += [f"{gid}: setting {k} is out of range" for k, v in given.items()
                     if not _valid_setting(check["params"][k], v)]
    if check.get("examples"):  # a check that reads one command: its examples prove it, as a command guard's do
        ex = guard.get("examples") if isinstance(guard.get("examples"), dict) else {}
        block, allow = ex.get("block") or [], ex.get("allow") or []
        if not block or not allow or not all(isinstance(e, str) for e in block + allow):
            problems.append(f"{gid}: needs block AND allow examples (commands)")
        else:
            problems += [f"{gid}: doesn't block its example {e[:60]!r}" for e in block if not check["examples"](e)]
            problems += [f"{gid}: blocks its allow example {e[:60]!r}" for e in allow if check["examples"](e)]
    when = guard.get("when")
    if when is not None and (not isinstance(when, dict) or not set(when) <= WHEN_KEYS
                             or not set(when.get("os") or []) <= set(PLATFORMS)
                             or ("installed" in when and not PROGRAM.match(str(when["installed"])))):
        problems.append(f"{gid}: `when` may only hold os (windows, linux, macos) and installed (a program name)")
    if "known_bug" in guard and not KNOWN_BUG.match(str(guard["known_bug"])):
        problems.append(f"{gid}: known_bug must be a known-bug id like KB-0031")
    problems += _next_problems(guard)
    message = guard.get("message")
    if check.get("needs_message") and not message:
        problems.append(f"{gid}: needs a message")
    if message is not None and (not isinstance(message, str) or len(message) > MAX_MESSAGE
                                or clean(message) != message.strip()):
        problems.append(f"{gid}: the message must be plain text of {MAX_MESSAGE} characters at most")
    return problems


def run_builtins(call: Call, packs: list[dict], modes: dict) -> list[tuple[str, str, dict]]:
    """(kind, text, guard) for each finding of the installed built-in checks listening to this event: kind is deny or
    note before a tool call, note after a prompt or at a session's start, stop at a turn's end. Each check runs once,
    for the first pack that names it; one that fails is skipped (fail open), never the others."""
    found, done = [], set()
    for pack in packs:
        for guard in pack.get("guards") or []:
            check = BUILTINS.get(guard.get("check")) if isinstance(guard, dict) and guard.get("kind") == "builtin" else None
            if check is None or call.name not in check["events"] or guard["check"] in done:
                continue
            done.add(guard["check"])
            how = modes.get(guard.get("id"))
            if how != "off" and how not in check["decisions"]:
                how = guard.get("decision") if guard.get("decision") in check["decisions"] else check["decisions"][0]
            if how in ("off", "remind"):
                continue
            labelled = _found(guard, pack, how)
            try:
                for kind, text in check["run"](call, _settings(check, guard.get("settings")), how, labelled) or []:
                    found.append((kind, text, labelled))
            except Exception:  # noqa: BLE001 - one broken check never stops another, or the call
                continue
    return found


def _has_builtins(packs: list[dict]) -> bool:
    return any(isinstance(g, dict) and g.get("kind") == "builtin" for p in packs for g in p.get("guards") or [])


# --- validation ---------------------------------------------------------------------------------------------------------
def _example_call(guard: dict, ex):
    """An example is the field's text, or a tool_input dict with an optional "tool"."""
    if isinstance(ex, str):
        field = guard.get("field") or "command"
        return guard["tools"][0], {"command" if field == "command" else field: ex}
    ex = dict(ex)
    return ex.pop("tool", guard["tools"][0]), ex


def validate_pack(pack) -> list[str]:
    """Problems with a pack, empty when it's good. Run before any pack is saved. A pack is data only: no guard may name
    a script, and a built-in guard only names a check that ships here. Every command guard must block its "block"
    examples and let its "allow" examples through the whole pack."""
    if not isinstance(pack, dict) or pack.get("formatVersion") != 1:
        return ["not a formatVersion 1 pack"]
    problems = [f"missing {k}" for k in ("slug", "title", "version", "tier", "summary", "rules", "guards") if k not in pack]
    if problems:
        return problems
    if not SLUG.match(str(pack["slug"])):
        problems.append(f"bad slug {pack['slug']!r}")
    if pack["tier"] not in ("free", "pro"):
        problems.append("tier must be free or pro")
    if not isinstance(pack["rules"], list) or not isinstance(pack["guards"], list):
        return problems + ["rules and guards must be lists"]
    rule_ids = [r.get("id") for r in pack["rules"] if isinstance(r, dict)]
    guard_ids = [g.get("id") for g in pack["guards"] if isinstance(g, dict)]
    if len(rule_ids) != len(pack["rules"]) or len(guard_ids) != len(pack["guards"]):
        return problems + ["every rule and guard must be an object"]
    if len(set(rule_ids)) != len(rule_ids) or len(set(guard_ids)) != len(guard_ids):
        problems.append("duplicate ids")
    if not all(isinstance(i, str) and ID.match(i) for i in rule_ids + guard_ids):
        problems.append("ids may only hold letters, digits, dots, dashes and underscores (64 at most)")
    if len(json.dumps(pack)) > MAX_PACK_BYTES:
        problems.append(f"the pack is over {MAX_PACK_BYTES // 1000} KB")
    for r in pack["rules"]:
        if not all(r.get(k) for k in ("id", "title", "do", "why", "scope")):
            problems.append(f"rule {r.get('id')}: needs id, title, do, why and scope")
        if not set(r.get("enforced_by") or []) <= set(guard_ids):
            problems.append(f"rule {r.get('id')}: names a guard that isn't in the pack")
    for g in pack["guards"]:
        gid = g.get("id")
        if g.get("rule") not in rule_ids:
            problems.append(f"{gid}: unknown rule {g.get('rule')}")
        problems += _class_problems(g)
        if g.get("kind") == "builtin":
            problems += _builtin_problems(g)
            continue
        if g.get("kind") != "command":
            problems.append(f"{gid}: only command and built-in guards can be installed here (a pack can't run a script)")
            continue
        if not g.get("tools") or not all(t in TOOLS or MCP_TOOL.match(str(t)) for t in g["tools"]) \
                or not g.get("match_any") or not g.get("message"):
            problems.append(f"{gid}: needs tools, match_any and a message")
            continue
        if not isinstance(g["message"], str) or len(g["message"]) > MAX_MESSAGE or clean(g["message"]) != g["message"].strip():
            problems.append(f"{gid}: the message must be plain text of {MAX_MESSAGE} characters at most")
        problems += _next_problems(g)
        if g.get("decision", "block") not in DECISIONS:
            problems.append(f"{gid}: decision must be block, ask, warn or remind")
        if not isinstance(g.get("ignore_heredocs", False), bool) or not isinstance(g.get("ignore_quoted", False), bool):
            problems.append(f"{gid}: ignore_heredocs and ignore_quoted must be true or false")
        if "known_bug" in g and not KNOWN_BUG.match(str(g["known_bug"])):
            problems.append(f"{gid}: known_bug must be a known-bug id like KB-0031")
        when = g.get("when")
        if when is not None and (not isinstance(when, dict) or not set(when) <= WHEN_KEYS
                                 or not set(when.get("os") or []) <= set(PLATFORMS)
                                 or ("installed" in when and not PROGRAM.match(str(when["installed"])))):
            problems.append(f"{gid}: `when` may only hold os (windows, linux, macos) and installed (a program name)")
        try:
            for key in ("match_any", "match_all", "unless", "paths", "skip_paths"):
                for pattern in g.get(key) or []:
                    if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN:
                        raise re.error("pattern too long, or not text")
                    if runaway_shape(pattern):
                        raise re.error("it repeats a repeated group, which can take forever on long input")
                    re.compile(pattern)
        except re.error as exc:
            problems.append(f"{gid}: bad pattern ({exc})")
            continue
        ex = g.get("examples") or {}
        if not ex.get("block") or not ex.get("allow"):
            problems.append(f"{gid}: needs block AND allow examples")
            continue
        for e in ex["block"]:
            if not matches(g, *_example_call(g, e), here=False):
                problems.append(f"{gid}: doesn't block its example {str(e)[:60]!r}")
        for e in ex["allow"]:
            blocking, _ = evaluate(*_example_call(g, e), packs=[pack], modes={}, here=False)
            if blocking:
                problems.append(f"{blocking['id']}: blocks {gid}'s allow example {str(e)[:60]!r}")
    return problems


# --- the slip's class and form, and the filled-in next call (R&D's rules review v2, 2026-10-02) -------------------------
# A guard's class is the kind of slip (its pack field `class`, else its id): variants share it, so they share a count. The
# fingerprint is the call's form, never its text. The same code as the platform's (.claude/hooks/rule_events.py shape
# and fingerprint, guard-engine.py grep_call); test_backslash_collapse.py checks both give the same answers.
CLASS_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,60}$")
RETRY_WINDOW_S = 600
_TOKENS = re.compile(r"""\|\||&&|>>|<<-?|[|;&<>()]|"(?:\\.|[^"\\])*"?|'[^']*'?|[^\s|;&<>()]+""")
_STARTS = {"|", "||", "&&", ";", "&", "("}


def slip_class(guard: dict) -> str:
    return str(guard.get("class") or guard.get("id") or "")


def shape(command: str) -> str:
    """A command's form with its content left out: each program's name, its flags and the operators between them, with _
    for every other word (paths, patterns, text)."""
    out, start = [], True
    for tok in _TOKENS.findall(str(command or ""))[:300]:
        if tok in _STARTS or tok in (">", ">>", "<", ")") or tok.startswith("<<"):
            out.append("<<" if tok.startswith("<<") else tok)
            start = tok in _STARTS
            continue
        if start:
            name = tok.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
            out.append(re.sub(r"[^a-z0-9_.+-]", "", name)[:24] or "_")
            start = False
        elif re.fullmatch(r"--?[A-Za-z][\w-]{0,24}", tok):
            out.append(tok)
        elif out[-1:] != ["_"]:
            out.append("_")
    return " ".join(out)


def fingerprint(tool: str, cls: str, tool_input: dict | None = None) -> str:
    """The slip's form as a short hash, never its text: tool + class + the command's shape + feature bits (backslash,
    heredoc, pipe); a file's extension for an edit; the argument names for any other call."""
    inp = tool_input if isinstance(tool_input, dict) else {}
    command = str(inp.get("command") or "")
    if command:
        form = shape(command) + "|" + "".join(b for b, on in (("b", "\\" in command), ("h", "<<" in command),
                                                               ("p", "|" in command)) if on)
    elif inp.get("file_path") or inp.get("notebook_path"):
        form = Path(str(inp.get("file_path") or inp.get("notebook_path"))).suffix.lower() or "(none)"
    else:
        form = ",".join(sorted(map(str, inp))[:12])
    return hashlib.sha256(f"{tool}|{cls}|{form}".encode("utf-8")).hexdigest()[:12]


GREP_PROGRAMS = ("grep", "egrep", "fgrep", "rg")
_BRE = (("\\|", "|"), ("\\(", "("), ("\\)", ")"), ("\\{", "{"), ("\\}", "}"), ("\\+", "+"), ("\\?", "?"))
_LONG_FLAGS = {"--ignore-case": "i", "--line-number": "n", "--files-with-matches": "l", "--count": "c",
               "--fixed-strings": "F", "--extended-regexp": "E", "--invert-match": "v", "--word-regexp": "w"}


def grep_call(command: str) -> str:
    """The Grep tool call a blocked grep or rg command was after, filled in from its own words: pattern, path, glob,
    type, output mode and flags. "" when it can't say (an inverted match has no Grep form): the pack's next line stands."""
    import shlex
    try:
        lexer = shlex.shlex(str(command or "").replace("\n", " ; "), posix=True, punctuation_chars="|;&()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return ""
    start, args, prog = True, None, ""
    for tok in tokens:
        if tok and set(tok) <= set("|;&()"):
            if args is not None:
                break
            start = True
        elif args is not None:
            args.append(tok)
        elif start and tok == "rtk":
            continue
        elif start and tok.rsplit("/", 1)[-1] in GREP_PROGRAMS:
            prog, args = tok.rsplit("/", 1)[-1], []
        else:
            start = False
    if args is None:
        return ""
    flags, ctx, words, pattern, glob, kind, rest, j = set(), {}, [], None, "", "", False, 0
    while j < len(args):
        a, nxt = args[j], (args[j + 1] if j + 1 < len(args) else None)
        if rest or not a.startswith("-") or a == "-":
            words.append(a)
        elif a == "--":
            rest = True
        elif a in ("-e", "--regexp") and nxt is not None:
            pattern, j = nxt, j + 1
        elif a.startswith(("--include=", "--glob=")):
            glob = a.split("=", 1)[1]
        elif a in ("-g", "--glob", "--include") and nxt is not None:
            glob, j = nxt, j + 1
        elif a in ("-t", "--type") and nxt is not None:
            kind, j = nxt, j + 1
        elif a in ("-A", "-B", "-C") and nxt is not None and nxt.isdigit():
            ctx[a[1]], j = nxt, j + 1
        elif a.startswith("--"):
            flags.add(_LONG_FLAGS.get(a.split("=", 1)[0], ""))
        elif m := re.fullmatch(r"-([a-zA-Z]*)([ABC])(\d+)", a):
            flags.update(m.group(1))
            ctx[m.group(2)] = m.group(3)
        else:
            flags.update(a[1:])
        j += 1
    if pattern is None:
        if not words:
            return ""
        pattern, words = words[0], words[1:]
    if "v" in flags:
        return ""
    if prog == "fgrep" or "F" in flags:
        pattern = re.escape(pattern)
    elif prog == "grep" and not flags & {"E", "P"}:
        for bre, ere in _BRE:
            pattern = pattern.replace(bre, ere)
    if "w" in flags:
        pattern = f"\\b(?:{pattern})\\b"
    mode = "files_with_matches" if "l" in flags else "count" if "c" in flags else "content"
    parts = [f"pattern={json.dumps(pattern)}"] + [f"{k}={json.dumps(v)}" for k, v in
                                                  (("path", words[0] if words else ""), ("glob", glob), ("type", kind)) if v]
    parts.append(f'output_mode="{mode}"')
    parts += ["-i=true"] if "i" in flags else []
    parts += ["-n=true"] if "n" in flags and mode == "content" else []
    parts += [f"-{k}={v}" for k, v in sorted(ctx.items())] if mode == "content" else []
    return "Grep(" + ", ".join(parts) + ")"


SUGGESTERS = {"grep": grep_call}  # a guard's `suggest`: the next call, filled in from the blocked call itself


def next_call(guard: dict, tool_input: dict) -> str:
    fill = SUGGESTERS.get(str(guard.get("suggest") or ""))
    return fill(value(tool_input, "command")) if fill else ""


def _class_problems(g: dict) -> list[str]:
    gid, problems = g.get("id"), []
    if "class" in g and not CLASS_ID.match(str(g["class"])):
        problems.append(f"{gid}: class must be lowercase words joined by dots or dashes")
    if "suggest" in g and g["suggest"] not in SUGGESTERS:
        problems.append(f"{gid}: suggest must be one of {', '.join(sorted(SUGGESTERS))}")
    return problems


# --- the hook's answer --------------------------------------------------------------------------------------------------
def _label(guard: dict) -> str:
    """Always who said it: pack text is data from a file, so Claude is told where it came from (R&D's review)."""
    return f'Rule pack "{clean(guard.get("_pack"), 41)}" ({guard.get("_from", "unverified")}), guard {clean(guard.get("id"), 64)}'


def block_reason(guard: dict, stops: int = 0) -> str:
    """What Claude reads when a guard stops a call: who stopped it, the one next call to make (the pack's `next`),
    why, and from a session's second stop by the same guard, that every variant is stopped (the self-learning loop:
    a message that makes Claude invent the fix gets retried). A guard for a recorded bug names it."""
    message = clean(guard.get("message"))
    if guard.get("_next_call"):  # filled in from the blocked call itself, and the latch (R&D's rules review v2)
        lead = (f"Next call: {clean(guard['_next_call'], 300)}. Every variant of this is the same slip "
                f"({clean(slip_class(guard), 64)}) and is stopped for the rest of this session. ")
    else:
        nxt = clean(guard.get("next"), MAX_NEXT) if guard.get("next") and not message.startswith("Next:") else ""
        lead = f"Next: {nxt} " if nxt else ""
    reason = f"{_label(guard)}: {lead}{message}"
    if guard.get("known_bug") and KNOWN_BUG.match(str(guard["known_bug"])):
        reason += (f" This is known bug {guard['known_bug']}: bugs.find(\"{guard['known_bug']}\") has its cause and "
                   "workaround. Use that; don't retry the same way.")
    return reason + _repeat_line(stops)


def _repeat_line(stops: int) -> str:
    return (f" Stopped {stops} times this session: every variant of this is stopped the same way, so take the next "
            "call, not another variant.") if stops >= 2 else ""


# --- guard events: the self-learning loop's record (.claude/self-learning-loop.md) ---------------------------------------
# One line per stop, warning or question: when, which session, which guard, what was decided. Never the command itself.
# <data>/guard-events/<UTC day>.jsonl beside guard-state, kept EVENTS_KEEP_DAYS. Read back for the repeat line and for
# the `lessons` check at session start, so a lesson outlives the context window it was learned in.
EVENTS_KEEP_DAYS = 30
MAX_NEXT = 160


def _events_dir() -> Path | None:
    base = STATE_DIR.parent if STATE_DIR else (Path(PACKS_DIR).parent if PACKS_DIR else None)
    return base / "guard-events" if base else None


def log_event(session, guard_id, decision: str, now: float | None = None, cls: str | None = None,
              fp: str | None = None) -> None:
    """Never raises: a record that can't be written is just missing. A stop with the same session and fingerprint in
    the last RETRY_WINDOW_S is marked retry_of: the message didn't teach."""
    folder = _events_dir()
    if folder is None:
        return
    try:
        now = time.time() if now is None else now
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_strftime('%Y-%m-%d', _gmtime(now))}.jsonl"
        fresh = not path.exists()
        entry = {"t": round(now, 3), "session": str(session or "")[:80], "guard": str(guard_id)[:64],
                 "class": str(cls or guard_id)[:64], "decision": decision}
        if fp:
            entry["fp"] = str(fp)[:16]
            if decision == "blocked" and session and not fresh:
                before = [e for e in read_events(0, now) if e.get("session") == entry["session"]
                          and e.get("fp") == entry["fp"] and e.get("decision") == "blocked"
                          and 0 <= now - float(e.get("t") or 0) <= RETRY_WINDOW_S]
                if before:
                    entry["retry_of"] = before[-1].get("t")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        if fresh:  # a new day: drop the days past keeping
            cutoff = _strftime("%Y-%m-%d", _gmtime(now - EVENTS_KEEP_DAYS * 86400))
            for old in folder.glob("*.jsonl"):
                if old.stem < cutoff:
                    old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - the record is a nudge, never a reason to fail the call
        pass


def read_events(days: int, now: float | None = None):
    """The logged events of the last `days` days, oldest day first."""
    folder, now = _events_dir(), time.time() if now is None else now
    if folder is None:
        return
    for back in range(days, -1, -1):
        try:
            lines = (folder / f"{_strftime('%Y-%m-%d', _gmtime(now - back * 86400))}.jsonl").read_text(
                encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError, OverflowError):
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(e, dict):
                yield e


def session_stops(session, cls) -> int:
    """How many times this class of slip has stopped this session in the last day, any variant, the stop being handled
    included."""
    if not session:
        return 0
    return sum(1 for e in read_events(1) if e.get("session") == str(session)[:80]
               and (e.get("class") or e.get("guard")) == cls and e.get("decision") == "blocked")


def _note(guard: dict, text: str) -> str:
    return f"Heads-up from {_label(guard)}: {text}"


def ask_reason(guard: dict) -> str:
    """What the user reads in Claude Code's permission prompt. The engine frames it, so a pack's words can't pose as
    Claude Code's or HomeShed's own (R&D's review)."""
    return (f'Rule pack "{clean(guard.get("_pack"), 41)}" ({guard.get("_from", "unverified")}) wants you to confirm '
            f"this. Its note: {clean(guard.get('message'))}")


def _decide_tool(event: dict, packs: list[dict], modes: dict) -> str | None:
    """Before a tool call: block, ask the user, or let it run with notes for Claude."""
    tool, tool_input = str(event.get("tool_name") or ""), event.get("tool_input") or {}
    if (tool not in TOOLS and not MCP_TOOL.match(tool)) or not isinstance(tool_input, dict):
        return None
    stopping, noted = evaluate(tool, tool_input, packs, modes)
    call = Call(event) if _has_builtins(packs) else None
    found = run_builtins(call, packs, modes) if call else []
    notes = [_note(g, clean(g.get("message"))) for g in noted if g.get("_how") == "warn"]
    notes += [_note(g, text) for kind, text, g in found if kind == "note"]
    denies = [(f"{_label(g)}: {text}", g) for kind, text, g in found if kind == "deny"]
    session = event.get("session_id")
    slip = lambda g: {"cls": slip_class(g), "fp": fingerprint(tool, slip_class(g), tool_input)}
    for g in noted:
        if g.get("_how") == "warn":
            log_event(session, g.get("id"), "warned", **slip(g))
    answer = {"hookEventName": "PreToolUse"}
    if stopping and stopping.get("_how") != "ask":
        log_event(session, stopping.get("id"), "blocked", **slip(stopping))
        stopping["_next_call"] = next_call(stopping, tool_input)
        answer.update(permissionDecision="deny",
                      permissionDecisionReason=block_reason(stopping, session_stops(session, slip_class(stopping))))
    elif denies:
        text, g = denies[0]
        log_event(session, g.get("id"), "blocked", **slip(g))
        answer.update(permissionDecision="deny",
                      permissionDecisionReason=text + _repeat_line(session_stops(session, slip_class(g))))
    elif stopping:  # Claude Code shows this reason to the user in its permission prompt
        log_event(session, stopping.get("id"), "asked")
        answer.update(permissionDecision="ask", permissionDecisionReason=ask_reason(stopping))
    if call:
        call.finish(ran=answer.get("permissionDecision") != "deny")
    if notes:
        answer["additionalContext"] = "\n".join(notes)
    return json.dumps({"hookSpecificOutput": answer}) if len(answer) > 1 else None


def decide(event: dict) -> str | None:
    """The hook's answer for one event: a JSON line to print, or None to stay silent. An event with no name is a tool
    call (the first engine only ever had those)."""
    name = str(event.get("hook_event_name") or "PreToolUse")
    if name not in HOOK_EVENTS:
        return None
    if event.get("tool_name") == "Read" and ".env" not in str((event.get("tool_input") or {}).get("file_path") or "").lower():
        return None  # a read is guarded only when it names a .env file: every other one stays cheap (no packs loaded)
    packs, modes = load_packs(), guard_modes()
    if name == "PreToolUse":
        return _decide_tool(event, packs, modes)
    if not _has_builtins(packs):
        return None  # only the built-in checks listen to the other events
    call = Call(event)
    found = run_builtins(call, packs, modes)
    call.finish(ran=True)
    if name == "Stop":
        stops = [f"{_label(g)}: {text}" for kind, text, g in found if kind == "stop"]
        return json.dumps({"decision": "block", "reason": "\n".join(stops)}) if stops else None
    notes = [_note(g, text) for kind, text, g in found if kind == "note"]
    return json.dumps({"hookSpecificOutput": {"hookEventName": name, "additionalContext": "\n".join(notes)}}) \
        if notes else None


# --- the timed trial ----------------------------------------------------------------------------------------------------
TRIAL_RUN, TRIAL_REPEAT = 2_000, 20_000
# A guard is judged by how its time grows when the input doubles, not by wall time, which depends on the machine (the
# prepper's VM round, 2026-10-01: two curated guards took 0.8-1.4 s on a 2-core PC and failed a fixed 1 s budget).
# Linear grows ~2x, quadratic ~4x (harmless here: the hook reads at most MAX_TEXT, and its own 10 s limit is the
# backstop); a runaway grows far more, or never finishes and the caller's time limit refuses it.
TRIAL_GROWTH = 8.0
TRIAL_FLOOR_S = 0.2   # below this a ratio is just timer noise (0.05 let a busy PC refuse a good pack: the prepper,
                      # 2026-10-06, no-force-push refused 3 times while Docker and the model were busy)
TRIAL_MAX_S = 5.0     # one guard over the full trial input, on any machine


def _attacks(guard: dict, run: int = TRIAL_RUN, repeat: int = TRIAL_REPEAT) -> list[str]:
    """Hostile inputs for the timed trial (R&D's review): runs of one character ending in a mismatch, which make a
    runaway pattern (exponential, or worse than quadratic) take for ever even at a few thousand characters, and each
    example repeated to TRIAL_REPEAT. Sizes found 2026-10-01: an ordinary `git\\s+push\\b.*--force` is quadratic on
    100k of repeated "git push --force-with-lease" (seconds); that's harmless in real use, where the hook's own
    10-second limit is the backstop, so the trial looks for runaways, not for every quadratic pattern."""
    texts = [c * run + "!" for c in ("a", " ", "/", "-", "0", "x", "\\", "\t")] + ["x" * run + "\n"]
    ex = guard.get("examples") or {}
    for e in list(ex.get("block") or []) + list(ex.get("allow") or []):
        s = e if isinstance(e, str) else json.dumps(e, sort_keys=True)
        if s:
            texts.append((s * (repeat // len(s) + 1))[:repeat])
    return texts


def _timed(guard: dict, run: int, repeat: int) -> float:
    """The fastest of three passes of every pattern over the hostile inputs (the fastest is the least noisy)."""
    flags, texts, best = (re.I if guard.get("ignore_case") else 0), _attacks(guard, run, repeat), float("inf")
    patterns = [p for key in ("match_any", "match_all", "unless", "paths", "skip_paths") for p in guard.get(key) or []]
    for _ in range(3):
        start = time.perf_counter()
        for pattern in patterns:
            for text in texts:
                re.search(pattern, text, flags)
        best = min(best, time.perf_counter() - start)
    return best


def trial(pack: dict) -> list[str]:
    """The guards whose patterns run away on long input: their time grows more than TRIAL_GROWTH-fold when the input
    doubles, or passes TRIAL_MAX_S. rule_packs.py runs this in a child process with a time limit before a pack is saved,
    so a pattern that never finishes is refused there and never costs a tool call anything."""
    slow = []
    for g in pack.get("guards") or []:
        if not isinstance(g, dict) or g.get("kind") != "command":
            continue
        # A guard is refused only when two separate measurements both say so: one busy moment can't refuse a good pack.
        if all(_runs_away(g) for _ in range(2)):
            slow.append(str(g.get("id")))
    return slow


def _runs_away(g: dict) -> bool:
    half = _timed(g, TRIAL_RUN // 2, TRIAL_REPEAT // 2)
    full = _timed(g, TRIAL_RUN, TRIAL_REPEAT)
    return full > TRIAL_MAX_S or (full > TRIAL_FLOOR_S and full > TRIAL_GROWTH * max(half, TRIAL_FLOOR_S / TRIAL_GROWTH))


def _options(argv: list[str]) -> dict:
    """--packs DIR, --modes FILE, --version. Read by hand, not with argparse: argparse exits with code 2 on a bad
    argument, and exit code 2 from a hook BLOCKS (a tool call, a prompt, a turn's end), so one typo would stop work."""
    opts, i = {}, 0
    while i < len(argv):
        if argv[i] in ("--packs", "--modes") and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            opts.setdefault("other", []).append(argv[i])
            i += 1
    return opts


def main(argv: list[str] | None = None) -> int:
    """Fail open: whatever goes wrong, the work goes on and nothing is printed. Always exits 0."""
    global PACKS_DIR, MODES_FILE
    try:
        opts = _options(sys.argv[1:] if argv is None else argv)
        if "--version" in opts.get("other", []):
            print(ENGINE_VERSION)
            return 0
        if "--trial" in opts.get("other", []):  # rule_packs.py's timed check of a pack, never the hook
            print(json.dumps({"slow": trial(strict_loads(sys.stdin.read()))}))
            return 0
        if opts.get("packs"):
            PACKS_DIR = Path(opts["packs"])
        if opts.get("modes"):
            MODES_FILE = Path(opts["modes"])
        event = json.loads(sys.stdin.read() or "{}")
        answer = decide(event) if isinstance(event, dict) else None
        if answer:
            print(answer)
    except Exception:  # noqa: BLE001 - a guard problem must never stop work
        pass
    return 0


if __name__ == "__main__":
    # Exit code 2 from a hook blocks (the call, the prompt, the turn's end), so nothing may end this process with a
    # code: not an exception, not SystemExit, not Ctrl-C. Whatever happens, flush what was printed and leave with 0
    # (R&D's review).
    try:
        main()
    except BaseException:  # noqa: BLE001
        pass
    finally:
        try:
            sys.stdout.flush()
        except BaseException:  # noqa: BLE001
            pass
        os._exit(0)
