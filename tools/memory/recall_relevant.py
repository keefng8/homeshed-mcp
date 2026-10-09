"""memory.recall_relevant. See ../../capabilities/memory/recall_relevant.md.

Adapts cognitive-workspace's relevance-check approach (reference-repos/cognitive-workspace,
_check_working_memory: keyword overlap, no embeddings needed) on top of memory.recall's existing
flat, chronological history -- not a port of its stateful hierarchical-buffer architecture. See
CLAUDE.md's cognitive-workspace entry for why the full buffer system stays deferred (a real,
unresolved "where does cross-call state live" question) while this piece doesn't have that
problem: it's stateless per call, reading memory.recall's already-persisted history fresh each
time rather than maintaining any new mutable state of its own.
"""
from __future__ import annotations

import math
import re
from collections import Counter

import projects
from registry import tool
from tools.memory.capture import MemoryError, facts_session_id
from tools.memory.recall import check_max_chars, recall, shorten

# Fact categories to search besides the session itself. Found 2026-09-29: recall_relevant only ranked one session's
# captured messages, and nothing is captured under a Claude session's id (facts go in with remember_fact), so every
# call from two sessions all night considered zero messages. These are the long-standing categories;
# remember_fact records any new one in the FACT_INDEX session, which is read too.
# Every category in use. platform, external-audits, tasks and scripts were missing,
# so recall_relevant never searched them (found 2026-09-29 by the Research and Development session: a new platform
# fact never scored, while the old fact it replaced still ranked second).
KNOWN_FACT_CATEGORIES = ("general", "platform", "mcp-server", "memory-stack", "local-ai", "known-gaps", "conventions",
                         "external-audits", "bugs-fixed", "tasks", "scripts")
SUPERSEDES_RE = re.compile(r"SUPERSEDES\s+(msg-[0-9a-f]{6,})", re.I)
FACT_INDEX = "fact-index"

_WORD_RE = re.compile(r"[a-z0-9]+")


# Words that say nothing about the topic, and a light plural fold ("lives" finds "live"). Before this, "where does
# the starter kit live" ranked facts by "where", "does" and "the" as much as by "starter" and "kit" (2026-09-29).
_STOP = frozenset(("a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do", "does", "for", "from",
                   "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "my", "no", "not", "of",
                   "on", "or", "our", "should", "so", "that", "the", "their", "them", "then", "there", "they", "this",
                   "to", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will", "with", "would",
                   "you", "your"))


def _fold(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _words(text: str) -> set[str]:
    return set(_terms(text))


def _terms(text: str) -> list[str]:
    return [_fold(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOP]


# Ranking (R&D's R2/R10, from the hindsight audit): Okapi BM25 over the candidate facts themselves, so a rare word that
# matches counts for more than a common one and a long fact doesn't win by length; then newest +10% to oldest -10%.
# Plain word overlap tied 3 of the 5 regression cases (researchandimprovements/evals/recall_eval.py).
BM25_K1, BM25_B, RECENCY_SPAN = 1.2, 0.75, 0.10


def bm25(query: str, docs: list[str]) -> list[float]:
    q = set(_terms(query))
    toks = [_terms(d) for d in docs]
    n = len(toks) or 1
    avgdl = (sum(map(len, toks)) / n) or 1.0
    df = Counter(t for ts in toks for t in set(ts) & q)
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in q}
    scores = []
    for ts in toks:
        tf, dl = Counter(t for t in ts if t in q), len(ts)
        scores.append(sum(idf[t] * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
                          for t, f in tf.items()))
    return scores


# Meaning (R10's next step): the GPU service scores each candidate against the query with bge-small, and the two
# rankings are fused (reciprocal rank fusion, k=60), so a fact worded differently from the question can still rank.
# Any failure means word ranking alone, and the meaning arm rests for SEMANTIC_REST_S so every recall doesn't wait on
# a PC that's off.
SEMANTIC_TIMEOUT_S, SEMANTIC_REST_S, RRF_K = 4.0, 60.0, 60
_semantic_rest_until = 0.0


def _semantic(query: str, docs: list[str]) -> list[float] | None:
    global _semantic_rest_until
    import os
    import time

    import httpx

    import vault
    url = os.environ.get("LOCAL_AI_DECIDE_BASE_URL", "").rstrip("/")  # the GPU service
    token = vault.secret("GPU_SERVICE_TOKEN")
    if not (url and token and docs) or time.time() < _semantic_rest_until:
        return None
    try:
        r = httpx.post(f"{url}/knowledge/similarity", json={"query": query, "texts": docs},
                       headers={"X-GPU-Token": token}, timeout=SEMANTIC_TIMEOUT_S)
        scores = r.json().get("scores") if r.status_code == 200 else None
        if isinstance(scores, list) and len(scores) == len(docs):
            return [float(s) for s in scores]
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    _semantic_rest_until = time.time() + SEMANTIC_REST_S
    return None


def _fuse(lexical: list[float], semantic: list[float]) -> list[float]:
    """Reciprocal rank fusion: a place near the top of either ranking counts, whatever each score's scale. A message
    that shares no word only gets its meaning half."""
    def places(scores):
        out = [0] * len(scores)
        for place, i in enumerate(sorted(range(len(scores)), key=lambda i: -scores[i]), 1):
            out[i] = place
        return out
    lp, sp = places(lexical), places(semantic)
    return [(1 / (RRF_K + lp[i]) if lexical[i] > 0 else 0.0) + 1 / (RRF_K + sp[i]) for i in range(len(lexical))]


def _recency(messages: list[dict]) -> list[float]:
    """Each message's recency factor, from its place among the candidates by timestamp: newest 1.1, oldest 0.9."""
    order = sorted(range(len(messages)), key=lambda i: str(messages[i].get("timestamp") or ""))
    span = max(1, len(messages) - 1)
    factor = [1.0] * len(messages)
    for place, i in enumerate(order):
        factor[i] = 1 + RECENCY_SPAN * (2 * place / span - 1)
    return factor


@tool(name="recall_relevant", category="memory", doc="memory/recall_relevant.md")
def recall_relevant(session_id: str, query: str, limit: int = 50, max_results: int = 5, max_chars: int = 300,
                    scope: str = "project", project_dir: str = "") -> dict:
    """Recall messages from a session ranked by relevance to a query, not just chronological
    order -- memory.recall on its own returns exactly what was written, in the order it was
    written; this reranks that same data by how much it actually overlaps with what you're
    asking about right now.

    Args:
        session_id: the session_id used when capturing (same as memory.recall).
        query: what you're trying to find -- scored by word overlap against each message's
            content, not a formal search syntax.
        limit: how many messages to fetch from memory.recall before ranking, 1-100.
        max_results: how many of the ranked results to actually return, 1-50.
        max_chars: cut each returned message to this many characters (0 = full text). Defaults
            to 300 (0 until 2026-09-29). Ranking always uses the full text.
        scope: "project" (default) searches this project's own memory; "global" searches the shared
            platform memory, whichever client asks.
        project_dir: the caller's working folder; picks that project's own memory (projects.py).

    Returns:
        {"messages": [{"id", "role", "content", "timestamp", "relevance_score"}, ...],
         "total_considered": int}
        `messages` is sorted by `relevance_score` descending and truncated to `max_results`.
        `relevance_score` is an Okapi BM25 score over the candidates (a rare shared word counts
        for more than a common one; long messages don't win by length), times a recency factor
        from 0.9 (oldest) to 1.1 (newest). Word matching, not semantic search: a `relevance_score`
        of 0 means nothing matched, not a ranking position.

    Raises:
        ValueError if `query` is empty/whitespace, or `max_results` is out of range (1-50).
        MemoryError: same failure modes as memory.recall (not configured, backend unreachable,
        query rejected).
    """
    with projects.working_in(project_dir):
        return _ranked(session_id, query, limit, max_results, max_chars, scope)


def _ranked(session_id: str, query: str, limit: int, max_results: int, max_chars: int, scope: str) -> dict:
    if not query or not query.strip():
        raise ValueError("query must be non-empty")
    if not 1 <= max_results <= 50:
        raise ValueError("max_results must be between 1 and 50")

    check_max_chars(max_chars)
    result = recall(session_id, limit=limit, scope=scope)
    messages = [{**m, "source": "session"} for m in result["messages"]]
    messages += _facts(min(limit, 50), scope)
    # A fact that says "SUPERSEDES msg-<id>" hides the one it replaces (ids may be given in short form).
    gone = {g.lower() for m in messages for g in SUPERSEDES_RE.findall(str(m.get("content") or ""))}
    messages = [m for m in messages if not any(str(m.get("id") or "").lower().startswith(g) for g in gone)]

    contents = [str(m.get("content") or "") for m in messages]
    lexical = [s * r for s, r in zip(bm25(query, contents), _recency(messages))]
    meaning = _semantic(query, contents)
    order = _fuse(lexical, meaning) if meaning else lexical
    scored = [{**m, "relevance_score": round(s, 3) if s else 0, **({"semantic": round(x, 3)} if meaning else {}),
               "_order": o} for m, s, x, o in zip(messages, lexical, meaning or lexical, order)]
    # Best first; among exact ties, the order memory.recall gave (a stable sort).
    scored.sort(key=lambda m: m["_order"], reverse=True)
    for m in scored:
        del m["_order"]

    return {"messages": shorten(scored[:max_results], max_chars), "total_considered": len(messages)}


def fact_categories(scope: str = "project") -> list[str]:
    """The long-standing categories plus every one remember_fact has indexed since."""
    found = list(KNOWN_FACT_CATEGORIES)
    try:
        for m in recall(facts_session_id(FACT_INDEX), limit=100, scope=scope)["messages"]:  # 200 got an HTTP 400, silently
            name = str(m.get("content") or "").strip()
            if name and name not in found:
                found.append(name)
    except (MemoryError, ValueError):
        pass
    return found


def _facts(limit: int, scope: str) -> list[dict]:
    """Saved facts from every category, each tagged with where it came from. A category that can't be read is
    skipped: one bad category never costs the whole recall."""
    out = []
    for category in fact_categories(scope):
        try:
            got = recall(facts_session_id(category), limit=limit, scope=scope)["messages"]
        except (MemoryError, ValueError):
            continue
        out += [{**m, "source": f"fact:{category}"} for m in got]
    return out
