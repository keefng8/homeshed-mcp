"""Capability manifest loader — answers "what capabilities exist?" from capabilities/**/*.json.

Deliberately separate from registry.py: this never imports tools/, so listing capabilities
never requires (or risks) importing execution code. See ARCHITECTURE.md's registry/execution/
transport split (nextsteps.md Phase 4).
"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_FIELDS = ("id", "name", "version", "description", "category", "risk", "transport")

CAPABILITIES_DIR = Path(__file__).parent / "capabilities"


_manifest_cache: tuple[tuple, list[dict]] = ((), [])


def load_manifests() -> list[dict]:
    """Cached copy of _load_manifests(): re-parsed only when a manifest file is added, removed or
    changed (one stat per file). The dashboard polls /capabilities every few seconds."""
    global _manifest_cache
    paths = sorted(CAPABILITIES_DIR.glob("**/*.json"))
    sig = tuple((str(p), p.stat().st_mtime_ns) for p in paths)
    if sig != _manifest_cache[0]:
        _manifest_cache = (sig, _load_manifests())
    return [dict(m) for m in _manifest_cache[1]]


def _load_manifests() -> list[dict]:
    """Load and validate every capabilities/**/*.json sidecar. Raises on a malformed one —
    a broken manifest should fail loudly at startup, not silently vanish from discovery."""
    manifests = []
    for path in sorted(CAPABILITIES_DIR.glob("**/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [f for f in REQUIRED_FIELDS if f not in data]
        if missing:
            raise ValueError(f"{path}: missing required field(s) {missing}")
        # Scope = "<risk>:<category>" (e.g. read:memory, write:docker), after OmniRoute's tool scopes.
        # Metadata only: a future public API gateway maps customer plans onto scopes; MCP itself
        # stays free of users and keys.
        data.setdefault("scope", f"{data.get('risk', 'read')}:{data['category']}")
        manifests.append(data)
    return manifests
