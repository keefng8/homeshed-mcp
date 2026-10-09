"""system.health. See ../../capabilities/system/health.md."""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

import docker
import httpx

from registry import tool


def _check_http(url: str, timeout: float = 3.0) -> dict:
    start = time.monotonic()
    try:
        resp = httpx.get(url, timeout=timeout)
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        if 200 <= resp.status_code < 300:
            return {"ok": True, "latency_ms": latency_ms, "error": None}
        return {"ok": False, "latency_ms": latency_ms, "error": f"http {resp.status_code}"}
    except httpx.RequestError as e:
        return {"ok": False, "latency_ms": None, "error": str(e)}


DOCKER_SOCKETS = ("/var/run/docker.sock", r"\\.\pipe\docker_engine")


def _docker_configured() -> bool:
    """DOCKER_HOST is set, or Docker's default socket (Linux, macOS) or named pipe (Windows) exists."""
    return bool(os.environ.get("DOCKER_HOST")) or any(os.path.exists(p) for p in DOCKER_SOCKETS)


def _check_docker() -> dict:
    start = time.monotonic()
    try:
        client = docker.from_env()
        try:
            client.ping()
            return {"ok": True, "latency_ms": round((time.monotonic() - start) * 1000, 1), "error": None}
        finally:
            client.close()
    except Exception as e:
        return {"ok": False, "latency_ms": None, "error": str(e)}



def _local_model() -> dict | None:
    """The first configured local (not cloud) backend in tier order, or None."""
    try:
        from tools.local_ai.ask import backends
        pool = sorted((b for b in backends() if not b["cloud"] and b["configured"] and b["base_url"]), key=lambda b: b["tier"])
    except Exception:  # noqa: BLE001 - an unreadable registry is reported as no local model, never a crash
        return None
    return pool[0] if pool else None

@tool(name="health", category="system", doc="system/health.md")
def health() -> dict:
    """Check whether this server's configured dependencies are reachable right now: memory-core, the
    first local model, the app store backend (MAVIS_STRAPI_BASE_URL) and the Docker daemon. Deterministic,
    no LLM call involved (checks each server's own health endpoint, never runs inference or a real query).
    Useful for a session to verify its environment before starting work, not just for a dashboard.

    Returns:
        {"ok": bool, "checks": {"memory_core": {...}, "local_ai_coder": {...},
         "mavis_strapi": {...}, "docker_daemon": {...}}} — each check is {ok, latency_ms, error}.
        A backend that isn't configured isn't checked, so it never makes `ok` false (Docker counts as configured
        when DOCKER_HOST is set or its default socket or named pipe exists). `ok` at the top level is true only if
        every check that ran passed. Never raises.
    """
    checks = {}

    # A backend that isn't configured isn't checked (like the local model below): a fresh install has none of
    # them, and its health must not stay red for services it never asked for.
    memory_url = os.environ.get("MEMORY_CORE_BASE_URL")
    if memory_url:
        checks["memory_core"] = _check_http(f"{memory_url.rstrip('/')}/health")

    # The local model the delegator really uses (local_ai_backends.json), not a fixed variable. No local model
    # configured (a machine without a GPU) means no check, not a failure.
    local = _local_model()
    if local:
        parsed = urlparse(local["base_url"])
        checks["local_ai_coder"] = {**_check_http(f"{parsed.scheme}://{parsed.netloc}/health"), "model": local["name"]}

    strapi_url = os.environ.get("MAVIS_STRAPI_BASE_URL")
    if strapi_url:
        checks["mavis_strapi"] = _check_http(f"{strapi_url.rstrip('/')}/_health")

    if _docker_configured():
        checks["docker_daemon"] = _check_docker()

    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
