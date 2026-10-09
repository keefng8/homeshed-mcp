"""local_ai.ask. See ../../capabilities/local_ai/ask.md.

model="auto" (default) is a registry-driven DELEGATOR: adding a model needs minimal changes, and every
model actually gets used.
- Models come from local_ai_backends.json (one entry per model, no code change to add one).
- A "discover" entry asks an OpenAI-compatible gateway which models it serves (cached 5 min),
  so a model added to the ai-gateway (LiteLLM) is picked up automatically.
- Lower tier is tried first (local, free and private, before cloud). WITHIN a tier the starting
  model rotates round-robin, so load spreads across every model instead of one doing all the
  work.
- A local model whose llama.cpp /metrics says busy is skipped. A model that fails is benched for
  BENCH_S seconds (circuit breaker), then tried again.
- allow_cloud=False keeps the prompt local. An explicit model name (or alias) means exactly that
  model, with no fallback.
- Every call leaves one line in an in-memory decision log (last DECISIONS_MAX): which model
  answered, which were skipped and why, and timing; never the prompt or the answer. The dashboard's
  "Why this model?" view reads it through mcp-server's /local-ai/decisions (2026-09-28, idea taken
  from OmniRoute's route-explainability panel).
"""
from __future__ import annotations

import collections
import fnmatch
import itertools
import json
import os
import re
import threading
import time
from pathlib import Path

import runtime_settings
import vault

import httpx

import runtime_settings
from registry import tool

MAX_PROMPT_CHARS = 32_000
MIN_TIMEOUT_S = 30
# NVIDIA's shared endpoint is sometimes slow (63s seen live 2026-09-25 vs 2.6s benchmarked), so
# cloud calls get the benchmark's own 90s floor.
CLOUD_MIN_TIMEOUT_S = 90
TOKENS_PER_SECOND_FLOOR = 15
BUSY_CHECK_TIMEOUT_S = 1.5
DISCOVERY_TTL_S = 300
BENCH_S = 60
DECISIONS_MAX = 50
REGISTRY_FILE = Path(os.environ.get("LOCAL_AI_BACKENDS_FILE", Path(__file__).resolve().parents[2] / "local_ai_backends.json"))
_BUSY_METRIC = re.compile(r"^llamacpp:requests_processing\s+([0-9.]+)", re.M)
_DEFERRED_METRIC = re.compile(r"^llamacpp:requests_deferred\s+([0-9.]+)", re.M)
SLOTS_TTL_S = 600
_slots: dict[str, tuple[float, int]] = {}  # llama.cpp root -> (checked at, slots)

_lock = threading.Lock()
_discovery_cache: dict[str, tuple[float, list[str]]] = {}
_benched_until: dict[str, float] = {}
_rotation: dict[int, itertools.count] = {}
_decisions: collections.deque = collections.deque(maxlen=DECISIONS_MAX)


class LocalAIError(RuntimeError):
    """Any local-AI backend failure. Message is always safe to return to the caller: never
    includes a base URL, key, or raw exception internals that could leak topology."""


class _BackendFailed(Exception):
    """Internal: this backend failed; the delegator may try the next one."""


def _load_registry() -> list[dict]:
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["backends"]
    except (OSError, ValueError, KeyError) as exc:
        raise LocalAIError(f"model registry unreadable ({type(exc).__name__})") from None


def _discover(entry: dict) -> list[str]:
    """Model ids the gateway serves, filtered by include globs. Cached; never raises."""
    base = runtime_settings.address(entry.get("url_env", "")) or entry.get("url")
    key = vault.secret(entry["key_env"]) if entry.get("key_env") else None
    if not base or (entry.get("key_env") and not key):
        return []
    now = time.time()
    cached = _discovery_cache.get(entry["name"])
    if cached and now - cached[0] < DISCOVERY_TTL_S:
        return cached[1]
    try:
        resp = httpx.get(f"{base.rstrip('/')}/models", timeout=5,
                         headers={"Authorization": f"Bearer {key}"} if key else None)
        ids = [m["id"] for m in resp.json().get("data", [])] if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        ids = None
    if ids is None:  # discovery failed: keep the last good list (or nothing)
        return cached[1] if cached else []
    include = entry.get("include") or ["*"]
    ids = sorted(i for i in ids if any(fnmatch.fnmatch(i, pat) for pat in include))
    _discovery_cache[entry["name"]] = (now, ids)
    return ids


def backends() -> list[dict]:
    """Resolved backends: {name, base_url, model, key, cloud, tier, configured, aliases}.
    Discovered gateway models appear by model id. Also used by the /local-ai/backends route."""
    out = []
    for entry in _load_registry():
        cloud, tier = bool(entry.get("cloud")), int(entry.get("tier", 99))
        base = runtime_settings.address(entry.get("url_env", "")) or entry.get("url")  # Settings page first
        key = vault.secret(entry["key_env"]) if entry.get("key_env") else None  # Credentials first, then .env
        if entry.get("discover"):
            aliases = entry.get("aliases") or {}
            by_model = {v: k for k, v in aliases.items()}
            for model_id in _discover(entry):
                out.append({"name": model_id, "base_url": base, "model": model_id, "key": key,
                            "cloud": cloud, "tier": tier, "configured": bool(base and key),
                            "aliases": [by_model[model_id]] if model_id in by_model else []})
            continue
        model = os.environ.get(entry.get("model_env", "")) or entry.get("model")
        configured = bool(base and model and (key or not entry.get("key_env")))
        out.append({"name": entry["name"], "base_url": base, "model": model, "key": key,
                    "cloud": cloud, "tier": tier, "configured": configured,
                    "aliases": list(entry.get("aliases") or [])})
    return out


def _find(name: str, pool: list[dict]) -> dict | None:
    return next((b for b in pool if name == b["name"] or name in b["aliases"]), None)


def _total_slots(root: str) -> int:
    """How many requests llama.cpp serves at once (/props total_slots), cached; 1 when unknown."""
    now = time.monotonic()
    cached = _slots.get(root)
    if cached and now - cached[0] < SLOTS_TTL_S:
        return cached[1]
    try:
        n = int(httpx.get(f"{root}/props", timeout=BUSY_CHECK_TIMEOUT_S).json().get("total_slots") or 1)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        n = 1
    _slots[root] = (now, max(1, n))
    return _slots[root][1]


def _is_busy(base_url: str) -> bool:
    """True only when llama.cpp positively reports it is full: every slot is working, or requests are
    already waiting. One request in flight isn't busy on a server with more slots (otherwise one
    session's request would send every other session's work to the cloud). Unreadable metrics count as 'not busy': the chat call itself is the real test."""
    root = base_url.rstrip("/").removesuffix("/v1")
    try:
        resp = httpx.get(f"{root}/metrics", timeout=BUSY_CHECK_TIMEOUT_S)
        if resp.status_code != 200:
            return False
        deferred = _DEFERRED_METRIC.search(resp.text)
        if deferred and float(deferred.group(1)) > 0:
            return True
        processing = _BUSY_METRIC.search(resp.text)
        return bool(processing and float(processing.group(1)) >= _total_slots(root))
    except (httpx.HTTPError, ValueError):
        return False


def _ordered(pool: list[dict]) -> list[dict]:
    """Tier ascending; within a tier, rotate the start so every backend gets work."""
    result = []
    for tier in sorted({b["tier"] for b in pool}):
        group = sorted((b for b in pool if b["tier"] == tier), key=lambda b: b["name"])
        with _lock:
            start = next(_rotation.setdefault(tier, itertools.count())) % len(group)
        result.extend(group[start:] + group[:start])
    return result


PROPS_TTL_S = 60.0
_props_cache: dict[str, tuple[float, str]] = {}


def _served_model(base_url: str) -> str:
    """The model file a local llama-server has loaded (its /props), cached a minute; "" when it can't say. The name we
    ask for and the server's alias can't be trusted: on 2026-09-30 a restart loaded Qwen3-8B under the 30B's alias,
    and every answer for part of the morning claimed the 30B (R&D's scorecard)."""
    root = re.sub(r"/v1/?$", "", base_url.rstrip("/"))
    hit = _props_cache.get(root)
    if hit and time.time() - hit[0] < PROPS_TTL_S:
        return hit[1]
    served = ""
    try:
        r = httpx.get(f"{root}/props", timeout=2)
        path = r.json().get("model_path") if r.status_code == 200 else ""
        served = os.path.basename(str(path or "").replace("\\", "/"))
    except Exception:  # noqa: BLE001 - not a llama-server, or unreachable: no warning, never a failed answer
        served = ""
    _props_cache[root] = (time.time(), served)
    return served


def _same_model(asked: str, served: str) -> bool:
    """"qwen3-coder-30b" matches "Qwen3-Coder-30B-A3B-Instruct-UD-Q3_K_XL.gguf"; not "Qwen3-8B-Q6_K.gguf"."""
    a, s = (re.sub(r"[^a-z0-9]", "", x.lower()) for x in (asked or "", served or ""))
    return not a or not s or a in s or s in a


def _call(b: dict, prompt: str, max_tokens: int, temperature: float, enable_thinking: bool) -> dict:
    # enable_thinking goes to EVERY backend: Nemotron 3 reasons too, and without
    # enable_thinking=false it spent a whole 20-token budget deliberating (verified 2026-09-25).
    body = {"model": b["model"], "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": temperature,
            "chat_template_kwargs": {"enable_thinking": enable_thinking}}
    headers = {"Authorization": f"Bearer {b['key']}"} if b["key"] else None
    try:
        response = httpx.post(f"{b['base_url'].rstrip('/')}/chat/completions", json=body, headers=headers,
                              timeout=max(CLOUD_MIN_TIMEOUT_S if b["cloud"] else MIN_TIMEOUT_S,
                                          max_tokens / TOKENS_PER_SECOND_FLOOR))
    except httpx.TimeoutException:
        raise _BackendFailed("timed out") from None
    except httpx.RequestError:
        raise _BackendFailed("unavailable") from None
    if response.status_code != 200:
        raise _BackendFailed(f"returned HTTP {response.status_code}")
    try:
        choice = response.json()["choices"][0]
        text, finish_reason = choice["message"]["content"], choice.get("finish_reason", "unknown")
    except (ValueError, KeyError, IndexError, TypeError):
        raise _BackendFailed("returned an unexpected response shape") from None
    if text is None:
        raise _BackendFailed("returned no text")
    out = {"text": text, "model": b["model"], "backend": b["name"], "finish_reason": finish_reason}
    if not b.get("cloud"):
        served = _served_model(b["base_url"])
        if served and not _same_model(b["model"], served):
            out.update(served_model=served, warning=f"asked for {b['model']}, but the local server has {served} loaded")
    return out


def _record(mode: str, order: list[dict], chosen: dict | None, skipped: list[dict], started: float,
            allow_cloud: bool, error: str | None = None) -> None:
    entry = {
        "t": time.time(), "mode": mode, "ok": chosen is not None,
        "chosen": chosen["name"] if chosen else None,
        "tier": chosen["tier"] if chosen else None,
        "cloud": chosen["cloud"] if chosen else None,
        "order": [b["name"] for b in order], "skipped": [dict(s) for s in skipped],
        "allow_cloud": allow_cloud, "ms": round((time.time() - started) * 1000),
    }
    if error:
        entry["error"] = error
    with _lock:
        _decisions.appendleft(entry)


def decisions(limit: int = 20) -> list[dict]:
    """Newest-first copy of the decision log (names, reasons, timings; no prompts or answers)."""
    with _lock:
        return [dict(d) for d in itertools.islice(_decisions, max(0, limit))]


def bench_remaining(name: str) -> int:
    """Seconds until a benched backend is tried again (0 = not benched)."""
    return max(0, round(_benched_until.get(name, 0) - time.time()))


def reset_bench(name: str) -> bool:
    """Lift a backend's bench now (e.g. after fixing it). True if it was benched."""
    with _lock:
        return _benched_until.pop(name, None) is not None


@tool(name="ask", category="local_ai", doc="local_ai/ask.md")
def ask(
    prompt: str,
    model: str = "auto",
    max_tokens: int = 1024,
    temperature: float = 0.2,
    enable_thinking: bool = False,
    allow_cloud: bool = True,
) -> dict:
    """Ask a model a question. Cheap, delegated grunt work: reach for this before Claude on
    analysis, summarization, classification, or preliminary investigation. Not for tasks needing
    strong reasoning, architecture decisions, or high confidence (escalate those to Claude).
    To summarise a FILE, use local_ai.summarize_file instead: it never puts the text in your
    context.

    Args:
        prompt: the question/instruction. Single-turn, not a chat history.
        model: "auto" (default): the delegator. Models come from local_ai_backends.json plus
            whatever NVIDIA models the ai-gateway serves. Local tier first, then cloud; within a
            tier the start rotates so every model gets work. Busy or failing models are skipped.
            Or name one model/alias ("qwen-coder", "qwen3-coder", "nemotron-super", or a full
            gateway model id) to use exactly that one, with no fallback.
        max_tokens: cap on generated tokens (1-4096).
        temperature: 0.0-2.0 sampling temperature.
        enable_thinking: hidden reasoning pass (Qwen3 and Nemotron have one). Off by default: it
            can consume the whole budget and return empty or deliberation text.
        allow_cloud: False keeps the prompt on local machines. Use it for anything sensitive.

    Returns:
        {"text", "model", "backend", "finish_reason", "skipped": [{"backend", "reason"}]}.

    Raises:
        LocalAIError: bad arguments, an unknown/unconfigured/failed explicit model, allow_cloud=
            False with a cloud model, or every auto backend failed (reasons listed).
    """
    if not prompt or not prompt.strip():
        raise LocalAIError("prompt must be non-empty")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise LocalAIError(f"prompt too long ({len(prompt)} chars, max {MAX_PROMPT_CHARS})")
    if not 0.0 <= temperature <= 2.0:
        raise LocalAIError("temperature must be between 0.0 and 2.0")
    if not 1 <= max_tokens <= 4096:
        raise LocalAIError("max_tokens must be between 1 and 4096")

    if not runtime_settings.get("local_ai_allow_cloud"):
        allow_cloud = False  # switched off on the Settings page: every prompt stays on local machines
    pool = backends()
    started = time.time()
    if model != "auto":
        b = _find(model, pool)
        if b is None:
            raise LocalAIError(f"unknown model {model!r}: choose 'auto' or one of {sorted(x['name'] for x in pool)}")
        if b["cloud"] and not allow_cloud:
            raise LocalAIError(f"model {model!r} is a cloud backend but allow_cloud is False")
        if not b["configured"]:
            raise LocalAIError(f"local_ai.ask model={model!r} is not configured")
        try:
            result = {**_call(b, prompt, max_tokens, temperature, enable_thinking), "skipped": []}
        except _BackendFailed as exc:
            _record("explicit", [b], None, [{"backend": b["name"], "reason": str(exc)}], started, allow_cloud, str(exc))
            raise LocalAIError(f"local AI backend {model!r} {exc}") from None
        _record("explicit", [b], b, [], started, allow_cloud)
        return result

    skipped: list[dict] = []
    now = time.time()
    order = _ordered(pool)
    for b in order:
        if b["cloud"] and not allow_cloud:
            skipped.append({"backend": b["name"], "reason": "cloud not allowed"})
        elif not b["configured"]:
            skipped.append({"backend": b["name"], "reason": "not configured"})
        elif _benched_until.get(b["name"], 0) > now:
            skipped.append({"backend": b["name"], "reason": "benched after a recent failure"})
        elif not b["cloud"] and _is_busy(b["base_url"]):
            skipped.append({"backend": b["name"], "reason": "busy"})
        else:
            try:
                result = _call(b, prompt, max_tokens, temperature, enable_thinking)
                _benched_until.pop(b["name"], None)
                _record("auto", order, b, skipped, started, allow_cloud)
                return {**result, "skipped": skipped}
            except _BackendFailed as exc:
                _benched_until[b["name"]] = time.time() + BENCH_S
                skipped.append({"backend": b["name"], "reason": str(exc)})
    reasons = "; ".join(f"{s['backend']}: {s['reason']}" for s in skipped) or "no models registered"
    _record("auto", order, None, skipped, started, allow_cloud, "no backend could answer")
    raise LocalAIError(f"no backend could answer ({reasons})")
