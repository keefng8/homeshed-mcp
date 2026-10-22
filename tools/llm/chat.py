"""llm.chat: one chat turn with a paid language model, relayed so the caller never holds the key.
See ../../capabilities/llm/relay.md."""
from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict  # pydantic needs this one on Python < 3.12 (the server wouldn't start on 3.11)

import clients
from registry import tool
from tools.llm import providers as prov

ROLES = ("user", "assistant", "system")
MAX_MESSAGES = 40
MAX_INPUT_CHARS = 48_000  # every message plus system: about 12k tokens, a bounded input cost
MAX_SYSTEM_CHARS = 8_000
MAX_TOKENS = 2048
MAX_TEXT_CHARS = 4_200  # the whole result stays under 5,000 characters


class Message(TypedDict):
    role: Literal["user", "assistant", "system"]
    content: str


def _check(messages, system: str, max_tokens, temperature) -> tuple[list[dict], str, int, float | None]:
    if not isinstance(messages, list) or not messages:
        raise prov.LLMError("messages must be a non-empty list of {role, content}")
    if len(messages) > MAX_MESSAGES:
        raise prov.LLMError(f"at most {MAX_MESSAGES} messages; summarise older turns")
    clean: list[dict] = []
    for i, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("role") not in ROLES or not isinstance(m.get("content"), str):
            raise prov.LLMError(f"messages[{i}] must be {{role: user|assistant|system, content: text}}")
        if not m["content"].strip():
            raise prov.LLMError(f"messages[{i}] has empty content")
        clean.append({"role": m["role"], "content": m["content"]})
    system = system if isinstance(system, str) else ""
    if len(system) > MAX_SYSTEM_CHARS:
        raise prov.LLMError(f"system is over {MAX_SYSTEM_CHARS:,} characters")
    total = len(system) + sum(len(m["content"]) for m in clean)
    if total > MAX_INPUT_CHARS:
        raise prov.LLMError(f"the input is {total:,} characters; the limit is {MAX_INPUT_CHARS:,}")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_TOKENS:
        raise prov.LLMError(f"max_tokens must be a whole number from 1 to {MAX_TOKENS}")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
            raise prov.LLMError("temperature must be between 0 and 2")
        temperature = float(temperature)
    return clean, system.strip(), max_tokens, temperature


MAX_IMAGES = 4  # images the model looks at with the last user message (2026-10-11: the shop's seat bots review tries)
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # each; four stay a reasonable request (xAI takes up to 20 MiB per image)


def _with_images(msgs: list[dict], images) -> list[dict]:
    """The messages with the named images attached to the last user message, as the Responses API takes them."""
    if images is None or images == []:
        return msgs
    if not isinstance(images, list) or not all(isinstance(r, str) and r.strip() for r in images):
        raise prov.LLMError("images must be a list of image names (as image.history or image.status give them)")
    if len(images) > MAX_IMAGES:
        raise prov.LLMError(f"at most {MAX_IMAGES} images")
    last = max((i for i, m in enumerate(msgs) if m["role"] == "user"), default=None)
    if last is None:
        raise prov.LLMError("images go with a user message; add one")
    import base64

    from tools.commerce._http import CommerceError
    from tools.image.providers import sniff_mime
    from tools.image.refs import image_bytes
    parts: list[dict] = [{"type": "input_text", "text": msgs[last]["content"]}]
    for ref in images:
        try:
            data = image_bytes(ref.strip(), max_bytes=MAX_IMAGE_BYTES)
        except CommerceError as exc:
            raise prov.LLMError(str(exc)) from None
        mime = sniff_mime(data)
        if mime is None:
            raise prov.LLMError(f"{ref} isn't a PNG, JPEG or WebP")
        parts.append({"type": "input_image", "detail": "high",
                      "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"})
    return [*msgs[:last], {"role": "user", "content": parts}, *msgs[last + 1:]]


@tool(name="chat", category="llm", doc="llm/relay.md")
def chat(messages: list[Message], system: str = "", model: str = "", max_tokens: int = 1024,
         temperature: float | None = None, provider: str = "xai", images: list[str] | None = None) -> dict:
    """One chat turn with a PAID language model (xAI Grok first), relayed through HomeShed: the key stays in the
    owner's vault and never reaches the caller. Off until the owner switches the provider on and gives it a daily cap
    (Control Panel > Settings > AI and tools > Language models); refused with the reason when off, keyless or capped.
    Nothing is stored at the provider (store=false); HomeShed logs who, model, tokens and cost, never the text.

    Args:
        messages: the conversation, oldest first: [{"role": "user"|"assistant"|"system", "content": "..."}], 1-40
            messages, together with system at most 48,000 characters.
        system: optional instructions (system prompt), at most 8,000 characters.
        model: one of the provider's models in llm.providers.list; empty means its default (xai: grok-4.3).
        max_tokens: most answer tokens, 1-2048 (default 1024). Reasoning tokens are billed on top.
        temperature: 0-2, optional (the model's default when left out).
        provider: "xai" or "anthropic" (Claude; default model claude-haiku-5-5, temperature ignored: its models
            don't take it). Either stops at the owner's monthly spend limit when one is set. openai planned.
        images: optional, up to 4 image names for the model to look at with the last user message: a generated or
            paid image's name, "folder/name.png" from the image store, or a Printify mockup address. Image tokens are
            billed as input.

    Returns:
        {"text", "provider", "model", "truncated", "finish", "usage": {"input_tokens", "output_tokens",
         "reasoning_tokens", "cached_tokens", "cost_usd", "cost_source": "xai"|"estimate"}, "used_today", "daily_cap"}
        (with a monthly spend limit set, also "credit_left_usd": the limit minus this month's spend so far).
        truncated is true when the model stopped at max_tokens or the text was cut to fit 4,200 characters.
    """
    provider = str(provider or "xai").strip().lower()
    if provider not in prov.PROVIDERS:
        prov.unusable_reason(provider)  # raises: unknown provider, with the list
    p = prov.PROVIDERS[provider]
    model = str(model or "").strip() or p["default_model"]
    if model not in p["models"]:
        raise prov.LLMError(f"{provider} has no model {model!r} here; models: {', '.join(p['models'])}")
    msgs, system, max_tokens, temperature = _check(messages, system, max_tokens, temperature)
    msgs = _with_images(msgs, images)  # loaded before reserve(): a bad name costs nothing
    client = clients.current_client.get() or "owner"
    prov.reserve(provider, client)  # switch, key, caps; counts the request
    try:
        out = prov.call(provider, model, msgs, system, max_tokens, temperature)
    except prov.LLMError as exc:
        prov.record(provider, client, model, None, ok=False, error=str(exc))
        raise
    prov.record(provider, client, model, out["usage"], ok=True)
    text = out["text"]
    cut = len(text) > MAX_TEXT_CHARS
    if cut:
        text = text[: MAX_TEXT_CHARS - 1] + "…"
    used = prov.today(provider)
    return {"text": text, "provider": provider, "model": out["model"], "truncated": bool(cut or out["incomplete"]),
            "finish": out["finish"], "usage": out["usage"], "used_today": used["requests"],
            "daily_cap": prov.daily_cap(provider),
            **({"credit_left_usd": round(max(0.0, cap - prov.month_cost(provider)), 4)}
               if (cap := prov.monthly_cap(provider)) else {})}
