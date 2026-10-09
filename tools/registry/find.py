"""registry.find — the Phase 8 fast capability router. See ../../capabilities/registry/find.md.

Three tiers, cheapest first, per nextsteps.md's explicit design (no vector DB, no embeddings):
exact ID -> alias/keyword substring -> local_ai.ask as a last-resort natural-language picker.
"""
from __future__ import annotations

from manifest import load_manifests
from registry import tool
from tools.local_ai.ask import LocalAIError, ask


def _summarize(manifest: dict) -> dict:
    return {"id": manifest["id"], "name": manifest["name"], "description": manifest["description"]}


@tool(name="find", category="registry", doc="registry/find.md")
def find(query: str, top_n: int = 3) -> dict:
    """Find which capability best matches a natural-language request. Never executes anything —
    use this to decide what to call, then call that capability separately.

    Args:
        query: what you're trying to do, e.g. "check what's running" or a capability ID directly.
        top_n: max candidates to return from the alias/keyword tier.

    Returns:
        {"tier": "exact_id"|"alias_keyword"|"local_ai_fallback"|"no_match", "matches": [...]}
        Each match is {id, name, description}. tier tells you how confident the match is —
        exact_id and alias_keyword are deterministic; local_ai_fallback is a local model's guess
        and should be treated with more skepticism.
    """
    if not query or not query.strip():
        raise ValueError("query must be non-empty")

    manifests = load_manifests()
    q = query.strip().lower()

    for m in manifests:
        if m["id"].lower() == q:
            return {"tier": "exact_id", "matches": [_summarize(m)]}

    scored = []
    for m in manifests:
        aliases = [a.lower() for a in m.get("aliases", [])]
        haystacks = aliases + [m["id"].lower(), m["name"].lower()]
        if any(q in h or h in q for h in haystacks):
            scored.append(m)
    if scored:
        return {"tier": "alias_keyword", "matches": [_summarize(m) for m in scored[:top_n]]}

    candidate_list = "\n".join(f"- {m['id']}: {m['description']}" for m in manifests)
    prompt = (
        f'A user asked: "{query}"\n\n'
        f"Which ONE of these capabilities (if any) is most relevant? "
        f"Reply with ONLY the capability id, or the word none.\n\n{candidate_list}"
    )
    try:
        result = ask(prompt, max_tokens=1024, temperature=0.0)
    except LocalAIError:
        return {"tier": "no_match", "matches": []}

    picked = result["text"].strip().lower()
    match = next((m for m in manifests if m["id"].lower() in picked), None)
    if match:
        return {"tier": "local_ai_fallback", "matches": [_summarize(match)]}
    return {"tier": "no_match", "matches": []}
