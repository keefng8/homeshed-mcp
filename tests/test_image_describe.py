"""image.describe: visible details through the llm relay (fake API, dummy key), banned words filtered out, a bad
answer retried once, refusals before any request. Reuses image.ip_check's test fixture."""
from __future__ import annotations

import pytest

from tests.test_image_ip_check import fake, png  # noqa: F401 - the fixture and the helper
from tools.image import describe as idesc


def test_details_and_confidence(fake):
    fake.answers = [{"details": ["long gold ears", "coral shapes behind the head", "white muzzle"], "confidence": "high"}]
    out = idesc.describe(image_b64=png(), count=2)
    assert out["ok"] is True and out["details"] == ["long gold ears", "coral shapes behind the head"]
    assert out["confidence"] == "high" and out["cost_usd"] > 0
    body = fake.calls[0]
    assert "never" in body["system"][0]["text"].lower() and "Give 2 details" in body["messages"][0]["content"][0]["text"]
    assert body["output_config"]["format"]["schema"] == idesc.SCHEMA


def test_breed_and_text_words_are_dropped_and_a_useless_answer_is_retried(fake):
    fake.answers = [{"details": ["a Labrador breed", "the word LOVE"], "confidence": "high"},
                    {"details": ["black ears", "round orange sun"], "confidence": "medium"}]
    out = idesc.describe(image_b64=png())
    assert out["details"] == ["black ears", "round orange sun"] and len(fake.calls) == 2


@pytest.mark.parametrize("kw, says", [({"count": 0}, "count"), ({"count": 7}, "count"), ({}, "exactly one"),
                                      ({"image_name": "a", "image_b64": "b"}, "exactly one")])
def test_refusals_cost_nothing(fake, kw, says):
    if "image_b64" not in kw and "image_name" not in kw and kw.get("count") is not None:
        kw["image_b64"] = png()
    out = idesc.describe(**kw)
    assert out["ok"] is False and says in out["reason"] and fake.calls == []
