"""The helper service's token header, for every call HomeShed's tools make to it (GPU/image/knowledge service)."""
from __future__ import annotations


def helper_headers() -> dict:
    """{"X-GPU-Token": token} when GPU_SERVICE_TOKEN is set, else {}. Never log or return the token elsewhere."""
    import vault
    token = vault.secret("GPU_SERVICE_TOKEN", "")
    return {"X-GPU-Token": token} if token else {}
