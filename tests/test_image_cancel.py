"""image.cancel: an app stops only its own image jobs; waiting or running local ones are cancelled; finished ones
say so; a paid provider's running job can't be stopped. The image service is faked."""
import httpx
import pytest

import clients
import tools.image.cancel as ic
from tools.image.generate import ImageError

JOB = "abcdef012345"


class Fake:
    def __init__(self, job=None):
        self.job, self.posts = job, []

    def get(self, url, timeout=None):
        return httpx.Response(200 if self.job else 404, json=self.job or {"detail": "unknown job id"},
                              request=httpx.Request("GET", url))

    def post(self, url, timeout=None):
        self.posts.append(url)
        return httpx.Response(200, json={**self.job, "status": "cancelled"}, request=httpx.Request("POST", url))


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(ic, "base_url", lambda: "http://gpu:8082")
    monkeypatch.setattr(ic.prov, "remote_status", lambda job_id: None)

    def use(job=None, client=None):
        fake = Fake(job)
        monkeypatch.setattr(ic.httpx, "get", fake.get)
        monkeypatch.setattr(ic.httpx, "post", fake.post)
        clients.current_client.set(client)
        return fake
    yield use
    clients.current_client.set(None)


def test_an_app_cancels_its_own_running_job(svc):
    fake = svc({"job_id": JOB, "status": "running", "requested_by": "shopbot"}, client="shopbot")
    out = ic.cancel(JOB)
    assert out == {"ok": True, "job_id": JOB, "status": "cancelled", "was": "running"}
    assert fake.posts == [f"http://gpu:8082/image/jobs/{JOB}/cancel"]


def test_an_app_cannot_cancel_someone_elses_job_but_the_owner_can(svc):
    svc({"job_id": JOB, "status": "queued", "requested_by": "owner"}, client="shopbot")
    with pytest.raises(ImageError, match="only its own"):
        ic.cancel(JOB)
    fake = svc({"job_id": JOB, "status": "queued", "requested_by": "shopbot"}, client=None)
    assert ic.cancel(JOB)["status"] == "cancelled" and fake.posts


def test_finished_missing_and_paid_jobs(svc, monkeypatch):
    fake = svc({"job_id": JOB, "status": "done", "requested_by": "shopbot"}, client="shopbot")
    assert ic.cancel(JOB) == {"ok": True, "job_id": JOB, "status": "already done", "was": "done"} and not fake.posts
    svc(None, client="shopbot")
    assert ic.cancel(JOB) == {"ok": False, "job_id": JOB, "status": "not found"}
    monkeypatch.setattr(ic.prov, "remote_status", lambda job_id: {"status": "running"})
    assert ic.cancel(JOB)["status"] == "running"
    with pytest.raises(ImageError, match="12-character"):
        ic.cancel("nope")
