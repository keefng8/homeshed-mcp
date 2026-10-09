"""slim: shrink an over-limit instruction file (a CLAUDE.md or rules.md) without losing anything (2026-09-29).

Moving text by hand costs Claude as many tokens as the text it moves. Here the local model reads each section (free), this script moves
the text itself, and Claude reads only a short plan.

    python slim.py plan   FILE   split FILE at its ## headings, label each section, and write .slim/<name>.plan.md
                                 next to it. Nothing else changes.
    python slim.py apply  FILE   do what the plan says (labels edited in the plan win). All or nothing: it refuses if
                                 FILE changed since the plan, and checks every line ends up somewhere.
    python slim.py finish FILE   when FILE is under its limit and every pointer resolves, delete the .slim files.

Labels: keep (stays); move (its own .md under .claude/docs/, and a one-line pointer stays); memory (a memory file plus
a MEMORY.md index line, in Claude Code's memory folder for that project); drop (deleted: finished, or already written
elsewhere; a copy stays in .slim/ until finish, and git has it too).

Why the local model and not Shedkeeper: Shedkeeper picks among options with a confidence, but sorted rules into packs right only
16 times in 37 (2026-09-28). Why ## sections: a ### stays with its parent, so a move never strands a subsection under
the wrong heading. A section whose long paragraphs are already in another .md of the project is labelled drop, with
that file named (R&D's dedupe idea, capability #5). Stdlib only: it runs anywhere Python does.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

LLM_URL = os.environ.get("SLIM_LLM_URL") or "http://127.0.0.1:8080/v1/chat/completions"
LLM_TIMEOUT_S = int(os.environ.get("SLIM_LLM_TIMEOUT_S", "90"))
LABELS = ("keep", "move", "memory", "drop")
WORK = ".slim"
TINY = 200           # a section this small isn't worth a pointer: it stays
MIN_SAVING = 250     # a move must save at least this much after its pointer (a review found seven
                     # "move" labels were one-line pointers already, where moving saved almost nothing)
EXCERPT = 3000       # what the local model reads of a long section (its start and end)
DUP_SHARE = 0.8      # this share of a section's long paragraphs already in one other file makes it a duplicate
DUP_MIN_PARA = 120   # shorter paragraphs don't count toward duplication
MAX_SCAN = 400       # .md files read when looking for duplicates
SKIP_DIRS = {".git", "node_modules", "dist", "build", WORK, ".venv", "venv", "__pycache__", "graphify-out"}
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
POINTER = re.compile(r"^Details: `([^`]+)`[ \t]*$", re.M)
ROW = re.compile(r"^\|\s*(S\d{2,})\s*\|\s*([A-Za-z]+)\s*\|")
# Keyword guesses for when the local model can't be reached (lists drafted by the local model, 2026-09-29).
GUESS = (
    ("drop", re.compile(r"\b(done|completed|superseded|obsolete|archived|removed|finished|closed|historical|retired)\b", re.I)),
    ("keep", re.compile(r"\b(rules?|must|never|always|safety|prohibited|restricted|mandatory)\b", re.I)),
    ("memory", re.compile(r"\b(status|decisions?|history|context|state|facts|notes|log|changelog|progress)\b", re.I)),
    ("move", re.compile(r"\b(how to|guide|setup|tutorial|reference|explanation|process|architecture|structure|"
                        r"install|deploy|commands?|examples?)\b", re.I)),
)
# A title that says it's a rule keeps its section in place without asking. An early run on a real CLAUDE.md had the local model send security rules to memory and working conventions out to a
# doc: rules must stay where every session reads them. The owner can still change it in the plan.
KEEP_TITLE = re.compile(r"\b(rules?|must|never|always|safety|security|conventions?|important|read this first|"
                        r"do not|don'?t)\b", re.I)
PROMPT = (
    "You are sorting one section of an AI coding assistant's instruction file ({name}). The assistant reads the whole "
    "file at the start of every session, so every word in it costs something each time. Pick ONE label:\n"
    "keep - a rule or pointer every session must follow or know, stated briefly\n"
    "move - how-to steps, reference, background, examples or long detail needed only sometimes\n"
    "memory - facts about the project: its state, decisions made, history, who/what/where\n"
    "drop - finished work, completed checklists, old plans, or text that says it is done, superseded or historical\n"
    "Reply with only the label, then \" | \", then what the section is, in under 12 words.\n\n"
    "Section title: {title}\nSection text:\n{text}")


class SlimError(RuntimeError):
    """A refusal, with a message for the person running it."""


# --- small helpers ---------------------------------------------------------------------------------------------------
def chars(text: str) -> int:
    """Size as the size guard counts it: a line ending is one character."""
    return len(text.replace("\r\n", "\n"))


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:  # keeps \r\n as it is
        return f.read()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50].strip("-") or "section"


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")[:90]


def limit_for(path: Path) -> int:
    """The global rules' limits: the global CLAUDE.md 4,000, any other CLAUDE.md 10,000, a rules.md 20,000."""
    if path.name == "CLAUDE.md":
        try:
            if path.resolve() == (Path.home() / ".claude" / "CLAUDE.md").resolve():
                return 4000
        except OSError:
            pass
        return 10000
    return 20000 if path.name == "rules.md" else 10000


def memory_dir_for(folder: Path) -> Path:
    """Claude Code's memory folder for a project folder (its name is the path with every other character a hyphen)."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(folder)) / "memory"


# --- sections ------------------------------------------------------------------------------------------------------------
def split(text: str) -> list[dict]:
    """The file's sections: what comes before the first cut heading ("S00"), then one per heading at the cut level (the
    shallowest level below # in use; # headings cut too). Headings inside code fences don't count. Joining the
    sections' text gives the file back exactly."""
    lines = text.splitlines(keepends=True)
    heads, fence = [], None
    for i, line in enumerate(lines):
        m = FENCE.match(line)
        if m:
            mark = m.group(1)[0]
            fence = mark if fence is None else (None if fence == mark else fence)
            continue
        if fence is None:
            h = HEADING.match(line.rstrip("\r\n"))
            if h:
                heads.append((i, len(h.group(1)), h.group(2).strip()))
    deeper = sorted({level for _, level, _ in heads if level >= 2})
    cut = deeper[0] if deeper else 1
    starts = [h for h in heads if h[1] <= cut]
    out = []
    first = starts[0][0] if starts else len(lines)
    if first > 0:
        out.append({"id": "S00", "title": "(top of the file)", "level": 0, "text": "".join(lines[:first])})
    for k, (i, level, title) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        out.append({"id": f"S{k + 1:02d}", "title": title, "level": level, "text": "".join(lines[i:end])})
    return out


def _paras(text: str) -> list[str]:
    """Long paragraphs, whitespace-normalised, headings left out (a heading glued to its first paragraph would stop an
    exact copy elsewhere from matching)."""
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if not HEADING.match(line.strip())]
    paras = (" ".join(p.split()) for p in re.split(r"\n[ \t]*\n", "\n".join(lines)))
    return [p for p in paras if len(p) >= DUP_MIN_PARA]


def duplicates(sections: list[dict], file: Path) -> dict[str, str]:
    """{section id: the other file} for sections whose long paragraphs are (by size, DUP_SHARE of them) already in one
    other .md under the file's folder."""
    wanted = {s["id"]: _paras(s["text"]) for s in sections if s["id"] != "S00"}
    wanted = {k: v for k, v in wanted.items() if v}
    found: dict[str, str] = {}
    if not wanted:
        return found
    scanned = 0
    for folder, dirs, names in os.walk(file.parent):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = Path(folder) / name
            if not name.lower().endswith(".md") or path.resolve() == file.resolve():
                continue
            scanned += 1
            if scanned > MAX_SCAN:
                return found
            try:
                if path.stat().st_size > 1_000_000:
                    continue
                theirs = set(_paras(_read(path)))
            except (OSError, UnicodeDecodeError):
                continue
            for sid, paras in wanted.items():
                if sid in found:
                    continue
                total = sum(len(p) for p in paras)
                if sum(len(p) for p in paras if p in theirs) >= DUP_SHARE * total:
                    found[sid] = os.path.relpath(path, file.parent).replace("\\", "/")
    return found


# --- labels ----------------------------------------------------------------------------------------------------------
def _excerpt(text: str) -> str:
    return text if len(text) <= EXCERPT else text[:EXCERPT * 2 // 3] + "\n[...]\n" + text[-EXCERPT // 3:]


def ask_local(prompt: str) -> str:
    """One question to the local model (an OpenAI-compatible chat endpoint: llama.cpp, Ollama, LM Studio)."""
    body = json.dumps({"model": os.environ.get("SLIM_LLM_MODEL", "local"), "temperature": 0, "max_tokens": 60,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(LLM_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]


def parse_answer(reply: str) -> tuple[str, str] | None:
    """("move", "how to deploy the site") from a reply like "Move | how to deploy the site"; None if no label."""
    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    m = re.search(r"\b(keep|move|memory|drop)\b", text, re.I)
    if not m:
        return None
    what = text.split("|", 1)[1] if "|" in text else ""
    return m.group(1).lower(), " ".join(what.split())[:90]


def guess(title: str, text: str) -> tuple[str, str]:
    for label, pattern in GUESS:
        if pattern.search(title):
            return label, "from the title's words"
    if "```" in text or chars(text) > 1500:
        return "move", "long, or has code"
    return "keep", "no clear signal"


def label(sections: list[dict], ask=ask_local, name: str = "CLAUDE.md", dups: dict | None = None) -> None:
    """Give each section a label, what it is, and how it was decided ("model", "guess", "size", "duplicate")."""
    for s in sections:
        size = chars(s["text"])
        if (dups or {}).get(s["id"]):
            s.update(label="drop", what=f"already in {dups[s['id']]}", how="duplicate")
            continue
        if s["id"] == "S00" or size < TINY:
            s.update(label="keep", what="the file's opening" if s["id"] == "S00" else "short", how="size")
            continue
        if KEEP_TITLE.search(s["title"]):
            s.update(label="keep", what="a rule, by its title", how="title")
            continue
        got = None
        if ask is not None:
            try:
                got = parse_answer(ask(PROMPT.format(name=name, title=s["title"], text=_excerpt(s["text"]))))
            except Exception:
                ask = None  # down or too slow: the rest are keyword guesses, marked as such
        lab, what = got or guess(s["title"], s["text"])
        if lab == "move" and size - _pointer_size(s) < MIN_SAVING:
            lab, what = "keep", "already short: a pointer would save little"
        s.update(label=lab, what=what, how="model" if got else "guess")


# --- plan --------------------------------------------------------------------------------------------------------------
def _pointer_size(s: dict) -> int:
    return len(s["title"]) + s["level"] + 45  # "## Title\nDetails: `.claude/docs/<slug>.md`\n\n"


def estimate(rows: list[dict], labels: dict[str, str]) -> int:
    after = 0
    for r in rows:
        lab = labels[r["id"]]
        after += r["size"] if lab == "keep" else _pointer_size(r) if lab == "move" else 0
    return after


def render_plan(file: Path, state: dict) -> str:
    rows, lim = state["sections"], state["limit"]
    labels = {r["id"]: r["label"] for r in rows}
    now, after = sum(r["size"] for r in rows), estimate(rows, labels)
    verdict = "under the limit" if after <= lim else "still over: change more labels to move, memory or drop"
    script = Path(__file__).resolve().as_posix()
    out = [f"# Slim plan: {file}", "",
           f"{now:,} characters now, limit {lim:,}. As labelled: about {after:,} ({verdict}).", "",
           "Change any label in the table (keep, move, memory, drop), then run:",
           f'    python "{script}" apply "{file}"', "",
           "- keep: stays as it is.",
           f"- move: goes to its own file in {state['docs']}, and a one-line pointer stays here.",
           f"- memory: becomes a memory file, with an index line, in {state['memory']}.",
           "- drop: deleted. Check it's really finished, or already written elsewhere; a copy stays in .slim until "
           "finish.",
           "Rows marked (guess) were labelled from keywords because the local model didn't answer.", "",
           "| id | label | size | section | what it is |", "|---|---|---|---|---|"]
    for r in rows:
        mark = " (guess)" if r["how"] == "guess" else ""
        out.append(f"| {r['id']} | {r['label']} | {r['size']:,} | {_cell(r['title'])} | {_cell(r['what'])}{mark} |")
    if after > lim:  # the levers left are the big sections that stay: only their owner can split them
        big = sorted((r for r in rows if labels[r["id"]] == "keep"), key=lambda r: -r["size"])[:2]
        out += ["", "Still over. The biggest sections that stay: " + "; ".join(
            f"{r['id']} {r['title']} ({r['size']:,})" for r in big) + ". Split one by hand (its history and state can "
            "go to memory, its rules stay), then run plan again."]
    return "\n".join(out) + "\n"


def _paths(file: Path) -> tuple[Path, Path, Path, Path]:
    work = file.parent / WORK
    return work, work / f"{file.stem}.plan.md", work / f"{file.stem}.state.json", work / f"{file.stem}.backup.md"


def plan(file: Path, ask=ask_local, docs: str | None = None, memory: str | None = None,
         limit: int | None = None) -> Path:
    file = Path(file).resolve()
    raw = _read(file)
    sections = split(raw)
    label(sections, ask=ask, name=file.name, dups=duplicates(sections, file))
    work, plan_path, state_path, _ = _paths(file)
    state = {"file": str(file), "sha": _sha(raw), "limit": limit or limit_for(file),
             "docs": str(Path(docs) if docs else file.parent / ".claude" / "docs"),
             "memory": str(Path(memory) if memory else memory_dir_for(file.parent)),
             "made": datetime.date.today().isoformat(),
             "sections": [{"id": s["id"], "title": s["title"], "level": s["level"], "size": chars(s["text"]),
                           "label": s["label"], "what": s["what"], "how": s["how"]} for s in sections]}
    _write(work / ".gitignore", "*\n")  # the working files never get committed by accident
    _write(state_path, json.dumps(state, indent=1))
    _write(plan_path, render_plan(file, state))
    return plan_path


def read_labels(plan_path: Path) -> dict[str, str]:
    out = {}
    for line in _read(plan_path).splitlines():
        m = ROW.match(line)
        if m:
            lab = m.group(2).lower()
            if lab not in LABELS:
                raise SlimError(f"{m.group(1)}: {m.group(2)!r} isn't a label (keep, move, memory, drop)")
            out[m.group(1)] = lab
    return out


# --- apply -------------------------------------------------------------------------------------------------------------
def _load_state(file: Path) -> dict:
    _, _, state_path, _ = _paths(file)
    try:
        return json.loads(_read(state_path))
    except (OSError, ValueError):
        raise SlimError(f"no plan for {file.name}: run plan first") from None


def _free(folder: Path, name: str, taken: set) -> Path:
    """folder/name, or name-2, name-3...: never an existing file, never one already planned in this run."""
    stem, suffix = os.path.splitext(name)
    candidate, n = folder / name, 2
    while candidate.exists() or str(candidate).lower() in taken:
        candidate, n = folder / f"{stem}-{n}{suffix}", n + 1
    taken.add(str(candidate).lower())
    return candidate


def _ending(text: str, nl: str) -> str:
    return text if text.endswith(("\n", "\r")) else text + nl


def _index_line(title: str, file_name: str, what: str) -> str:
    head = f"- [{title[:60]}]({file_name}) — "
    return head + what[:max(0, 199 - len(head))]


def apply(file: Path, today: str | None = None) -> dict:
    file = Path(file).resolve()
    state = _load_state(file)
    work, plan_path, state_path, backup = _paths(file)
    raw = _read(file)
    if _sha(raw) != state["sha"]:
        raise SlimError(f"{file.name} changed since the plan was made: run plan again")
    sections = split(raw)
    labels = read_labels(plan_path)
    missing = [s["id"] for s in sections if s["id"] not in labels]
    if missing:
        raise SlimError(f"the plan has no label for {', '.join(missing)}")
    nl = "\r\n" if "\r\n" in raw else "\n"
    docs, mem = Path(state["docs"]), Path(state["memory"])
    today = today or datetime.date.today().isoformat()
    whats = {r["id"]: r.get("what") or "" for r in state["sections"]}
    kept, new_files, index_lines, taken = [], {}, [], set()
    for s in sections:
        lab = labels[s["id"]]
        if lab == "keep":
            kept.append(s["text"])
        elif lab == "move":
            target = _free(docs, _slug(s["title"]) + ".md", taken)
            new_files[target] = f"<!-- Moved from {file.name} on {today} by slim; {file.name} points here. -->{nl}{nl}" \
                                + _ending(s["text"], nl)
            rel = os.path.relpath(target, file.parent).replace("\\", "/")
            head = f"{'#' * s['level']} {s['title']}{nl}" if s["level"] else ""
            kept.append(f"{head}Details: `{rel}`{nl}{nl}")
        elif lab == "memory":
            target = _free(mem, "project_" + _slug(s["title"]).replace("-", "_") + ".md", taken)
            what = whats.get(s["id"]) or s["title"]
            new_files[target] = (f"---{nl}name: {target.stem}{nl}description: {json.dumps(what)}{nl}metadata:{nl}"
                                 f"  type: project{nl}---{nl}{nl}" + _ending(s["text"], nl)
                                 + f"{nl}(Moved from {file} on {today} by slim.){nl}")
            index_lines.append(_index_line(s["title"], target.name, what))
    new_text = "".join(kept)

    # Nothing may be lost: every line of the file is kept, moved, or in a dropped section (the backup keeps those).
    pool = new_text + "".join(new_files.values()) + "".join(s["text"] for s in sections if labels[s["id"]] == "drop")
    lost = [line for line in raw.splitlines() if line.strip() and line not in pool]
    if lost:
        raise SlimError(f"{len(lost)} line(s) would be lost (first: {lost[0][:80]!r}); nothing changed")

    index = mem / "MEMORY.md"
    index_before = _read(index) if index.exists() else None
    created: list[Path] = []
    try:
        _write(backup, raw)
        for path, content in new_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "x", encoding="utf-8", newline="") as f:  # "x": never overwrite anything
                f.write(content)
            created.append(path)
        if index_lines:
            old = index_before or ""
            sep = nl if old and not old.endswith(("\n", "\r")) else ""
            _write(index, old + sep + nl.join(index_lines) + nl)
        _write(file, new_text)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        if index_lines:
            if index_before is None:
                index.unlink(missing_ok=True)
            else:
                _write(index, index_before)
        _write(file, raw)
        raise
    result = {"before": chars(raw), "after": chars(new_text), "limit": state["limit"], "files": [str(p) for p in created],
              "index": str(index) if index_lines else None,
              "dropped": [s["id"] for s in sections if labels[s["id"]] == "drop"]}
    state["applied"] = {"at": today, "labels": labels, **result}
    _write(state_path, json.dumps(state, indent=1))
    return result


# --- finish ------------------------------------------------------------------------------------------------------------
def finish(file: Path, anyway: bool = False) -> dict:
    file = Path(file).resolve()
    state = _load_state(file)
    applied = state.get("applied")
    if not applied:
        raise SlimError("nothing applied yet: run apply first (or delete the .slim folder to drop the plan)")
    text = _read(file)
    size, lim = chars(text), state["limit"]
    problems = []
    if size > lim:
        problems.append(f"{file.name} is {size:,} of {lim:,} characters: still over (run plan again for another round)")
    broken = [p for p in POINTER.findall(text) if not (file.parent / p).exists()]
    if broken:
        problems.append("pointers to missing files: " + ", ".join(broken))
    gone = [p for p in applied.get("files") or [] if not Path(p).exists()]
    if gone:
        problems.append("moved files missing: " + ", ".join(gone))
    if problems and not anyway:
        raise SlimError("; ".join(problems) + ". Nothing deleted (--anyway deletes the working files regardless)")
    work, plan_path, state_path, backup = _paths(file)
    for path in (plan_path, state_path, backup):
        path.unlink(missing_ok=True)
    if work.exists() and not [p for p in work.iterdir() if p.name != ".gitignore"]:
        (work / ".gitignore").unlink(missing_ok=True)
        work.rmdir()
    return {"size": size, "limit": lim, "problems": problems}


# --- command line ------------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="slim", description="Shrink an over-limit CLAUDE.md or rules.md without losing "
                                                         "anything: plan, check the plan, apply, finish.")
    sub = p.add_subparsers(dest="cmd", required=True)
    pp = sub.add_parser("plan", help="label each section and write a plan to review")
    pp.add_argument("file")
    pp.add_argument("--limit", type=int, help="characters (default: 10,000 for a CLAUDE.md, 20,000 for rules.md)")
    pp.add_argument("--docs", help="folder for moved sections (default: .claude/docs next to the file)")
    pp.add_argument("--memory", help="memory folder (default: Claude Code's memory folder for the file's folder)")
    pp.add_argument("--no-model", action="store_true", help="keyword guesses only, no local model")
    sub.add_parser("apply", help="do what the plan says").add_argument("file")
    pf = sub.add_parser("finish", help="delete the working files once the file is under its limit")
    pf.add_argument("file")
    pf.add_argument("--anyway", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.cmd == "plan":
            path = plan(Path(a.file), ask=None if a.no_model else ask_local, docs=a.docs, memory=a.memory,
                        limit=a.limit)
            state = _load_state(Path(a.file).resolve())
            counts = {lab: sum(r["label"] == lab for r in state["sections"]) for lab in LABELS}
            after = estimate(state["sections"], {r["id"]: r["label"] for r in state["sections"]})
            print(f"Plan: {path}")
            print(f"{len(state['sections'])} sections: " + ", ".join(f"{n} {lab}" for lab, n in counts.items())
                  + f". About {after:,} of {state['limit']:,} characters after. Read the plan, change any label, "
                    "then run apply.")
        elif a.cmd == "apply":
            out = apply(Path(a.file))
            print(f"Applied: {out['before']:,} -> {out['after']:,} characters (limit {out['limit']:,}); "
                  f"{len(out['files'])} file(s) written, {len(out['dropped'])} section(s) dropped. Check them, then "
                  "run finish.")
        else:
            out = finish(Path(a.file), a.anyway)
            print(f"Done: {out['size']:,} of {out['limit']:,} characters; working files deleted."
                  + (" Problems: " + "; ".join(out["problems"]) if out["problems"] else ""))
    except SlimError as exc:
        print(f"slim: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
