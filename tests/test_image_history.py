"""image.history (the owner, to-do #48, 2026-10-06): which prompt made which image, local and paid together. Case
list drafted by local_ai.ask (qwen3-coder-30b). The local image service and the paid folder are faked."""
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from tools.image import history as h
from tools.image import providers as prov


@pytest.fixture
def paid(tmp_path, monkeypatch):
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path)
    return tmp_path


def local(monkeypatch, files=None, fail=False):
    monkeypatch.setattr(h, "base_url", lambda: "http://gpu.test")

    def get(url, params=None, timeout=0):
        if fail:
            raise httpx.ConnectError("down")
        assert url == "http://gpu.test/image/files" and params == {"limit": params["limit"]}
        return SimpleNamespace(status_code=200, json=lambda: {"files": files or []})
    monkeypatch.setattr(h.httpx, "get", get)


def sidecar(folder, job_id, at, **job):
    p = folder / f"{job_id}.json"
    p.write_text(json.dumps({"job_id": job_id, "status": "done", "saved_as": f"img-{job_id}.png", "finished_at": at,
                             "prompt": f"paid {job_id}", "provider": "xai", "model": "grok-2-image",
                             "license": "commercial", "image_b64": "never-returned", **job}), encoding="utf-8")
    os.utime(p, (at, at))


def test_local_and_paid_together_newest_first_with_their_prompts(monkeypatch, paid):
    local(monkeypatch, [{"name": "20261006-200000-7.png", "mtime": 300.0, "prompt": "a red fox, detailed",
                         "original_prompt": "fox", "seed": 7, "bytes": 1}])
    sidecar(paid, "aaaaaaaaaaaa", 400.0)
    sidecar(paid, "bbbbbbbbbbbb", 100.0)
    out = h.history()
    assert [r["name"] for r in out["images"]] == ["img-aaaaaaaaaaaa.png", "20261006-200000-7.png", "img-bbbbbbbbbbbb.png"]
    loc = out["images"][1]
    assert loc == {"name": "20261006-200000-7.png", "source": "local", "at": 300.0, "prompt": "a red fox, detailed",
                   "original_prompt": "fox", "seed": 7}
    assert out["images"][0]["source"] == "paid" and out["images"][0]["provider"] == "xai" and "note" not in out
    assert "never-returned" not in json.dumps(out)  # no image data, ever


def test_the_local_service_down_lists_paid_images_with_a_note(monkeypatch, paid):
    local(monkeypatch, fail=True)
    sidecar(paid, "aaaaaaaaaaaa", 400.0)
    out = h.history()
    assert out["count"] == 1 and "didn't answer" in out["note"]


def test_no_paid_folder_and_skipped_records(monkeypatch, tmp_path):
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path / "missing")
    local(monkeypatch)
    assert h.history() == {"images": [], "count": 0}
    monkeypatch.setattr(prov, "output_dir", lambda: tmp_path)
    sidecar(tmp_path, "cccccccccccc", 50.0, status="failed")
    sidecar(tmp_path, "dddddddddddd", 60.0, status="running", saved_as=None)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    assert h.history()["count"] == 0


def test_the_limit(monkeypatch, paid):
    local(monkeypatch, [{"name": f"20261006-2000{i:02d}-1.png", "mtime": float(i)} for i in range(30)])
    assert h.history(limit=5)["count"] == 5
    for bad in (0, 101, True, "5"):
        with pytest.raises(h.ImageError):
            h.history(limit=bad)
