"""image.generate (job start) and image.status client tests. Boundary cases (13-char ids)
from a local_ai.ask draft list; security/stripping cases added by hand."""
from __future__ import annotations

import httpx
import pytest

import tools.image.generate as ig
import tools.image.status as ist

URL = "http://10.9.9.9:8082"
JOB = {"job_id": "abcdef012345", "status": "running", "progress": "loading model", "seed": 1,
       "license": "L", "timeout_s": 900}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return dict(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("x", request=None, response=None)


HELD = {**JOB, "status": "queued", "progress": "waiting: not enough GPU memory: 1.3GB free, ~2GB needed"}


@pytest.fixture
def held(monkeypatch):
    """The local service holds the job for GPU memory; xAI is usable unless a test says otherwise."""
    calls, state = [], {"cancel": 200}
    monkeypatch.setenv("IMAGE_BASE_URL", URL)

    def fake(url, **kwargs):
        calls.append(url)
        return _Resp(state["cancel"], {"status": "cancelled"}) if url.endswith("/cancel") else _Resp(body=HELD)
    monkeypatch.setattr(ig.httpx, "post", fake)
    monkeypatch.setattr(ig.prov, "unusable_reason", lambda p, m: state.get("why") if p == "xai" else None)
    monkeypatch.setattr(ig.prov, "start_remote", lambda p, m, *a: calls.append((p, m)) or {"job_id": "x1", "provider": p,
                                                                                            "model": m, "paid": True})
    return calls, state


def test_paid_fallback_moves_a_gpu_held_job_to_xai(held):
    calls, _ = held
    out = ig.generate("a fox", enhance_prompt=False, paid_fallback=True)
    assert out["provider"] == "xai" and out["model"] == "grok-imagine-image" and "GPU memory" in out["fell_back"]
    assert calls == [f"{URL}/image/generate", f"{URL}/image/jobs/{JOB['job_id']}/cancel", ("xai", "grok-imagine-image")]


@pytest.mark.parametrize("case", ["off", "no_flag", "behind_another", "cancel_fails", "folder"])
def test_no_fallback_leaves_the_local_job_waiting(held, monkeypatch, case):
    calls, state = held
    kw = {"paid_fallback": case != "no_flag"}
    if case == "off":
        state["why"] = "xai is over its daily cap"
    if case == "cancel_fails":
        state["cancel"] = 409
    if case == "behind_another":
        HELD_BEHIND = {**HELD, "progress": "waiting for the image before it"}
        monkeypatch.setattr(ig.httpx, "post", lambda url, **k: calls.append(url) or _Resp(body=HELD_BEHIND))
    if case == "folder":
        kw["folder"] = "shirts"
    out = ig.generate("a fox", enhance_prompt=False, **kw)
    assert out["provider"] == "local-sdcpp" and "fell_back" not in out
    assert not any(isinstance(c, tuple) for c in calls)  # nothing paid started


def test_paid_fallback_failure_after_cancel_names_both(held, monkeypatch):
    def boom(*a):
        raise ig.prov.ProviderError("xAI returned 500")
    monkeypatch.setattr(ig.prov, "start_remote", boom)
    with pytest.raises(ig.ImageError, match="GPU memory.*paid fallback failed: xAI returned 500"):
        ig.generate("a fox", enhance_prompt=False, paid_fallback=True)


@pytest.fixture
def post_calls(monkeypatch):
    recorded = []
    monkeypatch.setenv("IMAGE_BASE_URL", URL)

    def fake(url, **kwargs):
        recorded.append((url, kwargs))
        return _Resp(body=JOB)

    monkeypatch.setattr(ig.httpx, "post", fake)
    return recorded


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    # Prompt enhancement calls the local_ai.ask delegator; never hit a real model in tests.
    monkeypatch.setattr(ig, "ask", lambda *a, **k: {"text": "A richly detailed red fox in a snowy birch forest at golden hour.", "backend": "qwen-coder"})


def test_generate_starts_job_and_returns_fast(post_calls):
    out = ig.generate("a red fox", enhance_prompt=False)
    assert out == {**JOB, "original_prompt": "a red fox", "enhanced_by": None, "enhance_error": None,
                   "auto_selected": False, "provider": "local-sdcpp", "model": "qwen-image-2.1", "paid": False,
                   "cost_estimate_usd": 0.0, "commercial_use": False,
                   "license_info": {"name": "Qwen Research License", "commercial_use": False,
                                    "terms_url": "https://huggingface.co/Qwen"}}
    url, kw = post_calls[0]
    assert url == f"{URL}/image/generate" and kw["timeout"] == 30  # never waits for the image
    assert kw["json"] == {"prompt": "a red fox", "model": "qwen-image-2.1", "width": 768,
                          "height": 768, "steps": None, "seed": -1, "upscale": False,
                          "original_prompt": None}  # set only when the prompt was enhanced (the image's record keeps it)


def test_negative_prompt_replaces_or_drops_the_built_in_list(post_calls):
    ig.generate("a striped sunset", enhance_prompt=False, negative_prompt="  blurry, low quality ")
    ig.generate("a striped sunset", enhance_prompt=False, default_negative=False)  # stripes wanted: no list at all
    ig.generate("a striped sunset", enhance_prompt=False, default_negative=False, negative_prompt="blurry")
    assert [kw["json"].get("negative_prompt") for _, kw in post_calls] == ["blurry, low quality", "", "blurry"]
    with pytest.raises(ig.ImageError, match="1000"):
        ig.generate("a striped sunset", enhance_prompt=False, negative_prompt="x" * 1001)
    assert len(post_calls) == 3  # refused before calling the service


def test_enhance_prompt_rewrites_before_generating(post_calls):
    out = ig.generate("a red fox", upscale=True)
    sent = post_calls[0][1]["json"]
    assert sent["prompt"].startswith("A richly detailed red fox") and sent["upscale"] is True
    assert out["original_prompt"] == "a red fox" and out["enhanced_by"] == "qwen-coder"


def test_enhance_failure_falls_back_to_original(post_calls, monkeypatch):
    def fail(*a, **k):
        raise ig.LocalAIError("no backend could answer")

    monkeypatch.setattr(ig, "ask", fail)
    out = ig.generate("a red fox")
    assert post_calls[0][1]["json"]["prompt"] == "a red fox"
    assert "used the original prompt" in out["enhance_error"]


def test_enhance_rejects_useless_rewrite(post_calls, monkeypatch):
    monkeypatch.setattr(ig, "ask", lambda *a, **k: {"text": "  ", "backend": "qwen-coder"})
    out = ig.generate("a red fox")
    assert post_calls[0][1]["json"]["prompt"] == "a red fox" and out["enhance_error"]


@pytest.mark.parametrize("prompt", ["", "   "])
def test_generate_empty_prompt_never_calls_backend(post_calls, prompt):
    with pytest.raises(ig.ImageError):
        ig.generate(prompt)
    assert post_calls == []


def test_generate_busy_409_surfaces_detail_not_url(monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", URL)
    monkeypatch.setattr(ig.httpx, "post", lambda url, **kw: _Resp(409, {"detail": "an image is already generating"}))
    with pytest.raises(ig.ImageError) as exc:
        ig.generate("x")
    assert "already generating" in str(exc.value) and URL not in str(exc.value)


def test_generate_unconfigured(monkeypatch):
    monkeypatch.delenv("IMAGE_BASE_URL", raising=False)
    monkeypatch.delenv("LOCAL_AI_DECIDE_BASE_URL", raising=False)
    with pytest.raises(ig.ImageError, match="not configured"):
        ig.generate("x")


def test_generate_connect_error_hides_url(monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", URL)

    def boom(url, **kw):
        raise httpx.ConnectError(f"cannot reach {url}")

    monkeypatch.setattr(ig.httpx, "post", boom)
    with pytest.raises(ig.ImageError) as exc:
        ig.generate("x")
    assert URL not in str(exc.value)


@pytest.fixture
def get_calls(monkeypatch):
    recorded = []
    monkeypatch.setenv("IMAGE_BASE_URL", URL)

    def fake(url, **kwargs):
        recorded.append((url, kwargs))
        return _Resp(body={**JOB, "status": "done", "image_b64": "AAAA", "image_mime": "image/jpeg"})

    monkeypatch.setattr(ist.httpx, "get", fake)
    return recorded


def test_status_strips_image_by_default(get_calls):
    out = ist.status("abcdef012345")
    assert out["status"] == "done" and "image_b64" not in out
    assert get_calls[0] == (f"{URL}/image/jobs/abcdef012345",
                            {"params": {"include_image": False}, "timeout": 30})


def test_status_include_image(get_calls):
    assert ist.status("abcdef012345", include_image=True)["image_b64"] == "AAAA"


@pytest.mark.parametrize("bad", ["", "abcdef01234", "abcdef0123456", "ABCDEF012345",
                                 "../../etc/pas", "abcdef01234g"])
def test_status_rejects_malformed_ids_without_http(get_calls, bad):
    with pytest.raises(ist.ImageError, match="12-character"):
        ist.status(bad)
    assert get_calls == []


def test_status_unknown_job(monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", URL)
    monkeypatch.setattr(ist.httpx, "get", lambda url, **kw: _Resp(404, {"detail": "unknown"}))
    with pytest.raises(ist.ImageError, match="unknown job"):
        ist.status("abcdef012345")
