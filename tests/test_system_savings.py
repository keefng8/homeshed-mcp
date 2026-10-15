"""system.savings: today's and the last week's tokens saved, with money; missing sources are null, never zeros."""
import httpx
import pytest

import usage
from tools.system import savings as sv

DAILY = {"available": True, "daily": [
    {"date": f"2026-10-{d:02d}", "commands": 10, "saved_tokens": 1000 * d, "savings_pct": 30.04} for d in range(5, 15)]}


@pytest.fixture
def gpu(monkeypatch):
    monkeypatch.setenv("GPU_SERVICE_URL", "http://helper:8082/")
    monkeypatch.setattr(usage, "summary", lambda: {"estimated_tokens_saved": 7900})
    seen = []

    def get(url, **kw):
        seen.append(url)
        return httpx.Response(200, json=DAILY, request=httpx.Request("GET", url))
    monkeypatch.setattr(sv.httpx, "get", get)
    return seen


def test_today_week_and_money(gpu):
    out = sv.savings()
    assert gpu == ["http://helper:8082/stats/rtk/daily"]
    assert out["today"]["date"] == "2026-10-14" and out["today"]["saved_tokens"] == 14000
    assert out["today"]["savings_pct"] == 30.0
    assert [d["date"] for d in out["days"]] == [f"2026-10-{d:02d}" for d in range(8, 15)]   # the last 7, oldest first
    week = sum(1000 * d for d in range(8, 15))
    assert out["week"]["saved_tokens"] == week
    assert out["week"]["money"] == {"usd": round(week * 4 / 1e6, 2), "gbp": round(week * 4 / 1e6 * 0.75, 2),
                                    "at": "Claude Opus 5.5", "approximate": True}
    assert out["tool_server_total_tokens"] == 7900 and out["source"] == "rtk"


def test_another_model_prices_differently(gpu):
    assert sv.savings("haiku-5-5")["week"]["money"]["at"] == "Claude Haiku 5.5"
    with pytest.raises(sv.SavingsError):
        sv.savings("gpt")


@pytest.mark.parametrize("setup", ["unset", "down", "unavailable"])
def test_no_source_is_null_not_zero(monkeypatch, setup):
    monkeypatch.setattr(usage, "summary", lambda: {})
    if setup == "unset":
        monkeypatch.delenv("GPU_SERVICE_URL", raising=False)
        monkeypatch.delenv("LOCAL_AI_DECIDE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("GPU_SERVICE_URL", "http://helper:8082")

        def get(url, **kw):
            if setup == "down":
                raise httpx.ConnectError("no route")
            return httpx.Response(200, json={"available": False}, request=httpx.Request("GET", url))
        monkeypatch.setattr(sv.httpx, "get", get)
    out = sv.savings()
    assert out["today"] is None and out["days"] == [] and out["source"] is None
    assert out["tool_server_total_tokens"] == 0
