"""image.queue and image.prompts: thin proxies to the image service, faked here with httpx.MockTransport-style stubs."""
import httpx
import pytest

from tools.image import prompts as prompts_mod
from tools.image import queue as queue_mod
from tools.image.generate import ImageError


class R:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def base(monkeypatch):
    monkeypatch.setenv("IMAGE_BASE_URL", "http://img.test")


def test_queue_busy_running_and_waiting_in_order(monkeypatch):
    jobs = {"jobs": [{"job_id": "c", "status": "queued", "queue_position": 2}, {"job_id": "a", "status": "running"},
                     {"job_id": "b", "status": "queued", "queue_position": 1}, {"job_id": "z", "status": "done"}]}
    monkeypatch.setattr(queue_mod.httpx, "get", lambda url, **k: R(200, jobs if url.endswith("/jobs") else {"busy": True, "max_queued": 20}))
    q = queue_mod.queue()
    assert q["busy"] and q["running"]["job_id"] == "a" and [j["job_id"] for j in q["waiting"]] == ["b", "c"]
    assert q["waiting_count"] == 2 and q["room"] == 18


def test_queue_unreachable(monkeypatch):
    def down(url, **k):
        raise httpx.ConnectError("no")
    monkeypatch.setattr(queue_mod.httpx, "get", down)
    with pytest.raises(ImageError, match="unreachable"):
        queue_mod.queue()


def test_prompts_bad_action_and_missing_id_make_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(prompts_mod.httpx, "request", lambda *a, **k: calls.append(a))
    with pytest.raises(ImageError, match="action must be"):
        prompts_mod.prompts(action="wipe")
    for action in ("edit", "remove"):
        with pytest.raises(ImageError, match="needs prompt_id"):
            prompts_mod.prompts(action=action)
    assert calls == []


def test_prompts_routes_and_bodies(monkeypatch):
    seen = []

    def fake(method, url, json=None, timeout=None):
        seen.append((method, url.split("/image", 1)[1], json))
        return R(200, {"ok": True})
    monkeypatch.setattr(prompts_mod.httpx, "request", fake)
    prompts_mod.prompts()
    prompts_mod.prompts(action="add", prompt="a shed", folder=" Sheds ")
    prompts_mod.prompts(action="edit", prompt_id="0a0a0a0a", prompt="x")
    prompts_mod.prompts(action="remove", prompt_id="0a0a0a0a")
    assert [s[:2] for s in seen] == [("GET", "/prompts"), ("POST", "/prompts"), ("PUT", "/prompts/0a0a0a0a"),
                                     ("DELETE", "/prompts/0a0a0a0a")]
    assert seen[1][2]["folder"] == "Sheds" and seen[0][2] is None and seen[3][2] is None


def test_prompts_backend_refusal_passes_its_reason(monkeypatch):
    monkeypatch.setattr(prompts_mod.httpx, "request", lambda *a, **k: R(400, {"detail": "unknown size"}))
    with pytest.raises(ImageError, match="unknown size"):
        prompts_mod.prompts(action="add", prompt="x", size="9x9")


def test_generate_folder_is_local_only(monkeypatch):
    from tools.image import generate as gen
    monkeypatch.setattr(gen, "choose", lambda p, m, r: ("openai", "gpt-image-1", False))
    monkeypatch.setattr(gen.prov, "model_info", lambda n, m: {"license": {"commercial_use": True}})
    with pytest.raises(ImageError, match="only available on the local provider"):
        gen.generate("a shed", enhance_prompt=False, provider="openai", folder="Sheds")
    with pytest.raises(ImageError, match="only available on the local provider"):
        gen.generate("a shed", enhance_prompt=False, provider="openai", negative_prompt="blurry")
