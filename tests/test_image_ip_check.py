"""image.ip_check: Claude Haiku through the llm relay (fake API, dummy key), the verdict worst-first per purpose, the
print crop, and fail closed (ok false) on everything that stops the check. Files under tmp_path."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from PIL import Image

import runtime_settings as rs
import vault
from tools.image import ip_check as ic
from tools.llm import providers as prov

KEY = "sk-ant-" + "Zp4" * 12  # secret-scan: allow (a dummy)


def flags(**found):
    d = {f: {"note": found.get(f, ""), "found": f in found} for f in ic.FLAGS}
    d["confidence"] = 0.9
    return d


def png(size=(400, 300)):
    out = io.BytesIO()
    Image.new("RGB", size, (90, 120, 200)).save(out, "PNG")
    return base64.b64encode(out.getvalue()).decode()


class Fake:
    def __init__(self):
        self.calls: list[dict] = []
        self.answers = [flags()]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(json.loads(req.content))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        text = a if isinstance(a, str) else json.dumps(a)
        return httpx.Response(200, json={"type": "message", "model": ic.MODEL, "stop_reason": "end_turn",
                                         "content": [{"type": "thinking", "thinking": "..."},
                                                     {"type": "text", "text": text}],
                                         "usage": {"input_tokens": 1800, "output_tokens": 150}})


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.delenv("VAULT_KEY", raising=False)
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / "no.key")
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.json")
    monkeypatch.setattr(vault, "AUDIT_FILE", tmp_path / "audit.jsonl")
    monkeypatch.setattr(vault, "_cache", (None, None))
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    for env, f in (("LLM_USAGE_FILE", "d.json"), ("LLM_USAGE_LOG", "u.jsonl"), ("LLM_MONTHLY_FILE", "m.json")):
        monkeypatch.setenv(env, str(tmp_path / f))
    monkeypatch.setenv("CLAUDE_WORKSPACE_API", KEY)
    f = Fake()
    monkeypatch.setattr(prov, "_TRANSPORT", httpx.MockTransport(f))
    rs.update({"llm_provider_anthropic_enabled": True, "llm_daily_cap_anthropic": "100",
               "llm_monthly_usd_anthropic": "100"})
    return f


def test_clear_image_with_the_schema_and_cached_rules(fake):
    out = ic.ip_check(image_b64=png())
    assert out["ok"] is True and out["verdict"] == "clear" and out["flags"] == {} and out["confidence"] == 0.9
    body = fake.calls[0]
    assert body["model"] == "claude-haiku-5-5" and body["output_config"]["format"]["schema"] == ic.SCHEMA
    assert body["system"][0]["text"] == ic.RULES and body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert [p["type"] for p in body["messages"][0]["content"]] == ["text", "image"]
    assert "temperature" not in body


@pytest.mark.parametrize("context,found,verdict", [
    ("hero", {"child": "small figure, right background", "brand": "logo"}, "child"),
    ("hero", {"print_covered": "strings over the print"}, "covered"),
    ("hero", {"person": "the model"}, "clear"),        # a hero has a model in it by design
    ("upload", {"person": "a real face"}, "person"),
    ("upload", {"child": "a child"}, "clear"),         # upload/design: a child is a person case, not its own flag
    ("design", {"character": "a cartoon mouse", "unsafe": "weapon"}, "unsafe"),
    # copyright (2026-10-23): someone else's photo or artwork
    ("upload", {"watermark": "stock agency mark", "person": "a face"}, "watermark"),
    ("upload", {"artwork": "a photo of a poster", "brand": "logo"}, "artwork"),
    ("upload", {"celebrity": "a famous singer", "watermark": "x", "person": "x"}, "celebrity"),
    ("upload", {"unsafe": "partial nudity", "celebrity": "x"}, "unsafe"),
    ("design", {"artwork": "it is a design"}, "clear"),   # every design is artwork: it doesn't count there
    ("hero", {"watermark": "the shop's tiled mark"}, "clear"),
    ("hero", {"unsafe": "nudity", "child": "a child"}, "unsafe"),
])
def test_the_worst_flag_for_the_context_wins(fake, context, found, verdict):
    fake.answers = [flags(**found)]
    out = ic.ip_check(image_b64=png(), purpose=context)
    assert out["verdict"] == verdict
    assert set(out["flags"]) == {k for k in found if k in ic.ORDER[context]}


@pytest.mark.parametrize("purpose,garment,found,verdict,named", [
    ("hero", "hoodie", {"garment": "shows a t-shirt", "brand": "logo"}, "garment", True),
    ("hero", "Hoodie", {"garment": "shows a t-shirt", "print_covered": "a cord"}, "covered", True),
    ("hero", "", {"garment": "x"}, "clear", False),           # no garment named: the flag doesn't count
    ("hero", "jacket", {"garment": "x"}, "clear", False),     # an unknown value is ignored
    ("upload", "tee", {"garment": "x"}, "clear", False),      # hero only
])
def test_garment_flags_another_garment_type_on_a_hero(fake, purpose, garment, found, verdict, named):
    fake.answers = [flags(**found)]
    out = ic.ip_check(image_b64=png(), purpose=purpose, garment=garment)
    assert out["ok"] is True and out["verdict"] == verdict
    assert ("garment" in out["flags"]) is (named and "garment" in found)
    assert ("The listing's garment is" in fake.calls[0]["messages"][0]["content"][0]["text"]) is named


def test_print_box_sends_a_close_crop_too(fake):
    ic.ip_check(image_b64=png((1000, 1000)), purpose="hero", print_box=[300, 300, 700, 700])
    content = fake.calls[0]["messages"][0]["content"]
    assert [p["type"] for p in content] == ["text", "image", "image"] and "close crop" in content[0]["text"]
    crop = Image.open(io.BytesIO(base64.b64decode(content[2]["source"]["data"])))
    assert crop.size == (520, 520)  # the box plus 15% each side


def test_an_unreadable_answer_is_tried_once_more_then_fails_closed(fake):
    fake.answers = ["not json", flags(child="blurred child")]
    out = ic.ip_check(image_b64=png(), purpose="hero")
    assert out["verdict"] == "child" and len(fake.calls) == 2
    fake.calls.clear()
    fake.answers = ["{broken"]
    out = ic.ip_check(image_b64=png(), purpose="hero")
    assert out == {"ok": False, "verdict": None, "reason": out["reason"]} and "couldn't be read" in out["reason"]
    assert len(fake.calls) == 2


@pytest.mark.parametrize("kw,why", [
    (dict(), "exactly one"), (dict(image_b64="!!"), "base64"), (dict(image_b64=base64.b64encode(b"x").decode()),
                                                                 "isn't an image"),
    (dict(image_b64=png(), purpose="poster"), "purpose"),
    (dict(image_b64=png(), print_box=[0, 0, 5, 5]), "hero"),
    (dict(image_b64=png(), purpose="hero", print_box=[0, 0, 900, 900]), "inside"),
])
def test_bad_input_fails_closed_without_a_request(fake, kw, why):
    out = ic.ip_check(**kw)
    assert out["ok"] is False and why in out["reason"] and fake.calls == []


def test_switched_off_or_out_of_credit_fails_closed(fake):
    prov._add_month_cost("anthropic", 100.0)
    out = ic.ip_check(image_b64=png())
    assert out["ok"] is False and "spend limit" in out["reason"] and fake.calls == []


def test_discovered_with_its_manifest():
    import registry
    from manifest import load_manifests
    assert "ip_check" in {c.name for c in registry.discover() if c.category == "image"}
    assert {m["id"]: m["risk"] for m in load_manifests()}["image.ip_check"] == "write"
