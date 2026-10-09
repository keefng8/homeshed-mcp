"""Approximate Claude-token savings accounting, fed by registry.logged() on every real MCP call.

Records SIZES ONLY (json length of arguments and result), never content, into cumulative
per-capability counters persisted at USAGE_FILE (the `usage` volume), exposed by GET /usage.

Saved tokens are an ESTIMATE, labelled as such everywhere they're shown. chars / 4 ≈ tokens
(the usual rule of thumb for English and code). The rule per capability is what Claude
plausibly didn't have to spend. It's deliberately conservative:
- Generation offloaded (local_ai.ask, reasoning.delegate/pipeline, local_ai.decide): the
  output the local model wrote. The prompt doesn't count, because Claude already paid to
  write it.
- Reading offloaded (local_ai.summarize_file): whole file minus the summary returned. The
  file never entered Claude's context. This is the biggest real saving.
- Retrieval (knowledge.search): the full source files the hits came from, minus the chunks
  returned, instead of Claude opening those files.
- image.generate: counted as images, not tokens.
Everything else counts calls/sizes but saves nothing.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from paths import data_path

USAGE_FILE = Path(os.environ.get("USAGE_FILE") or data_path("usage/usage.json"))
CHARS_PER_TOKEN = 4
_lock = threading.Lock()

# Memory calls counted as reads or writes, per app and category (memory_stats()).
MEMORY_READS = {"memory.recall": "read", "memory.recall_facts": "read", "memory.recall_relevant": "read"}
MEMORY_WRITES = {"memory.capture": "write", "memory.remember_fact": "write"}
MAX_MEMORY_CATEGORIES = 200
GENERATION = {"local_ai.ask", "reasoning.delegate", "reasoning.pipeline", "local_ai.decide", "local_ai.assess"}
RULES = {
    "generation": "output the local model wrote (Claude didn't generate it)",
    "reading": "file size minus summary returned (file never entered Claude's context)",
    "retrieval": "full source files of the hits minus the chunks returned",
}


def _size(value) -> int:
    try:
        return len(json.dumps(value, default=str))
    except Exception:
        return 0


def saved_chars(capability_id: str, result) -> tuple[int, str | None]:
    """(estimated chars Claude didn't spend, rule name) for one successful call."""
    out = _size(result)
    if capability_id in GENERATION:
        text = result.get("text") or result.get("answer") or "" if isinstance(result, dict) else ""
        return (len(text) if text else out), "generation"
    if capability_id == "local_ai.summarize_file" and isinstance(result, dict):
        return max(0, int(result.get("file_chars", 0)) - out), "reading"
    if capability_id == "knowledge.search" and isinstance(result, dict):
        files = {r.get("path"): int(r.get("file_chars", 0)) for r in result.get("results", [])}
        return max(0, sum(files.values()) - out), "retrieval"
    return 0, None


def _load() -> dict:
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"since": time.time(), "capabilities": {}}


def record(capability_id: str, args: dict, result, ok: bool, client: str | None = None) -> None:
    """Never raises: accounting must not break a real capability call."""
    try:
        saved, rule = saved_chars(capability_id, result) if ok else (0, None)
        with _lock:
            data = _load()
            c = data["capabilities"].setdefault(capability_id, {
                "calls": 0, "errors": 0, "in_chars": 0, "out_chars": 0, "saved_chars": 0, "rule": rule})
            c["calls"] += 1
            c["errors"] += 0 if ok else 1
            c["in_chars"] += _size(args)
            c["out_chars"] += _size(result) if ok else 0
            c["saved_chars"] += saved
            c["rule"] = c.get("rule") or rule
            c["last"] = time.time()
            if capability_id == "image.generate" and ok:
                c["images"] = c.get("images", 0) + 1
            kind = MEMORY_READS.get(capability_id) or MEMORY_WRITES.get(capability_id)
            if kind and ok:  # memory reads and writes per app and category (the owner, 2026-10-06: totals on both dashes)
                who = data.setdefault("memory", {}).setdefault(client or "owner", {"reads": 0, "writes": 0, "categories": {}})
                rw = "reads" if capability_id in MEMORY_READS else "writes"
                who[rw] += 1
                cats = who["categories"]
                cat = str((args or {}).get("category") or "general")[:40]
                if cat in cats or len(cats) < MAX_MEMORY_CATEGORIES:
                    cats.setdefault(cat, {"reads": 0, "writes": 0})[rw] += 1
                who["last"] = time.time()
            if client:  # per-client counters: the building block for per-customer metering later
                pc = data.setdefault("clients", {}).setdefault(client, {"calls": 0, "errors": 0, "refused": 0, "tools": {}})
                pc["calls"] += 1
                pc["errors"] += 0 if ok else 1
                pc["tools"][capability_id] = pc["tools"].get(capability_id, 0) + 1
                pc["last"] = time.time()
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = USAGE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(USAGE_FILE)
    except Exception:
        pass


def record_refusal(client: str | None) -> None:
    """Count a refused call against a client (not a tool error). Never raises."""
    if not client:
        return
    try:
        with _lock:
            data = _load()
            pc = data.setdefault("clients", {}).setdefault(client, {"calls": 0, "errors": 0, "refused": 0, "tools": {}})
            pc["refused"] = pc.get("refused", 0) + 1
            USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = USAGE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(USAGE_FILE)
    except Exception:
        pass


def memory_stats(client: str | None) -> dict:
    """Memory reads and writes since counting began. An app sees only its own, by category; the owner sees every app
    with its share of the total."""
    data = _load()
    per = data.get("memory") or {}
    if client:
        mine = per.get(client) or {"reads": 0, "writes": 0, "categories": {}}
        return {"app": client, "reads": mine["reads"], "writes": mine["writes"], "categories": mine["categories"],
                "since": data.get("since")}
    total = sum(v["reads"] + v["writes"] for v in per.values()) or 0
    return {"reads": sum(v["reads"] for v in per.values()), "writes": sum(v["writes"] for v in per.values()),
            "apps": {name: {"reads": v["reads"], "writes": v["writes"],
                            "share": round((v["reads"] + v["writes"]) / total, 3) if total else 0.0}
                     for name, v in sorted(per.items())},
            "since": data.get("since")}


def summary() -> dict:
    data = _load()
    caps = data["capabilities"]
    saved = sum(c["saved_chars"] for c in caps.values())
    by_rule: dict[str, int] = {}
    for c in caps.values():
        if c.get("rule"):
            by_rule[c["rule"]] = by_rule.get(c["rule"], 0) + c["saved_chars"] // CHARS_PER_TOKEN
    return {
        "since": data.get("since"),
        "estimated_tokens_saved": saved // CHARS_PER_TOKEN,
        "by_rule": by_rule,
        "rules": RULES,
        "images_generated": sum(c.get("images", 0) for c in caps.values()),
        "calls": sum(c["calls"] for c in caps.values()),
        "chars_per_token": CHARS_PER_TOKEN,
        "capabilities": {k: {**v, "saved_tokens": v["saved_chars"] // CHARS_PER_TOKEN} for k, v in caps.items()},
        "clients": data.get("clients", {}),
    }
