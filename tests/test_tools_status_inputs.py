"""/tools/status carries each tool's inputs, so the Control Panel's Run page can build a form for any tool (to-do #50)."""
import pytest


def test_tools_status_lists_inputs():
    from starlette.testclient import TestClient
    import server
    c = TestClient(server.mcp.streamable_http_app(),
                   headers={"Authorization": f"Bearer {server.AUTH_TOKEN}"} if getattr(server, "AUTH_TOKEN", None) else {})
    r = c.get("/tools/status")
    if r.status_code in (401, 403):
        pytest.skip("admin auth differs in this test setup")
    rows = {t["id"]: t for t in r.json()["tools"]}
    # a public tool with a boolean default (signal.* is private and left out of the public copy)
    assert rows["etsy.listing.get"]["inputs"]["full"] == {"type": "boolean", "required": False, "default": False}
    assert rows["image.carousel"]["inputs"]["image_names"] == {"type": "array", "required": True}
    assert all(isinstance(t["inputs"], dict) for t in rows.values())
