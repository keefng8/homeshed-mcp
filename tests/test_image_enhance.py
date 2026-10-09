"""image.generate's prompt rewrite (the owner, 2026-10-07, #65): a time limit, FLUX's length, spelling and intent."""
import time

import pytest

from tools.image import generate as g


def _fake_ask(text=None, delay=0.0, error=None):
    def ask(prompt, **kw):
        ask.prompts.append(prompt)
        if delay:
            time.sleep(delay)
        if error:
            raise g.LocalAIError(error)
        return {"text": text, "backend": "test-model"}
    ask.prompts = []
    return ask


def test_a_slow_rewrite_gives_up_and_keeps_the_original(monkeypatch):
    monkeypatch.setattr(g, "ENHANCE_BUDGET_S", 0.2)
    monkeypatch.setattr(g, "ask", _fake_ask("a lovely long rewrite " * 3, delay=1.0))
    started = time.time()
    assert g._enhance("a red shed", "flux.1-schnell") == (None, None, "the rewrite took over 0.2 s; used the original prompt")
    assert time.time() - started < 0.9


def test_flux_gets_a_shorter_rewrite_and_an_over_long_one_is_refused(monkeypatch):
    fake = _fake_ask("x" * (g.FLUX_MAX_CHARS + 1))
    monkeypatch.setattr(g, "ask", fake)
    assert g._enhance("a red shed", "flux.1-schnell")[2] == "rewrite was empty or too long; used the original prompt"
    assert "40-90 words" in fake.prompts[0] and "Fix spelling mistakes" in fake.prompts[0]
    fake2 = _fake_ask("x" * (g.FLUX_MAX_CHARS + 1))
    monkeypatch.setattr(g, "ask", fake2)
    assert g._enhance("a red shed", "qwen-image-2.1")[0] is not None  # other models allow up to 2,000
    assert "60-120 words" in fake2.prompts[0]


def test_a_good_rewrite_and_an_error(monkeypatch):
    monkeypatch.setattr(g, "ask", _fake_ask("A weathered red garden shed at golden hour, soft light, moss on the roof."))
    assert g._enhance("a red shd", "flux.1-schnell")[:2] == (
        "A weathered red garden shed at golden hour, soft light, moss on the roof.", "test-model")
    monkeypatch.setattr(g, "ask", _fake_ask(error="no backend could answer"))
    assert g._enhance("a red shed", "flux.1-schnell") == (None, None, "no backend could answer; used the original prompt")


def test_a_long_flux_prompt_gets_a_warning(monkeypatch):
    monkeypatch.setattr(g, "choose", lambda p, m, r: (g.prov.LOCAL, "flux.1-schnell", False))
    monkeypatch.setattr(g, "base_url", lambda: "http://img.test")

    class R:
        status_code = 200

        def json(self):
            return {"job_id": "abc", "status": "queued"}
    monkeypatch.setattr(g.httpx, "post", lambda *a, **k: R())
    out = g.generate("a shed " * 200, enhance_prompt=False, model="flux.1-schnell")
    assert "256 tokens" in out["prompt_warning"]
    out = g.generate("a shed", enhance_prompt=False, model="flux.1-schnell")
    assert "prompt_warning" not in out
