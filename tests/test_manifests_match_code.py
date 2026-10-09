"""Every tool's manifest matches its code (the 2026-09-30 reflection #5, the prepper's G2: capability docs described
parameters and defaults the code no longer had). A tool and its manifest come and go together; the manifest's inputs
are the function's parameters (keyword-only ones too; *args/**kwargs aside), required exactly when the function has no
default, with the same defaults; and the doc the tool names exists. Edge cases listed by local_ai.ask."""
import inspect
from pathlib import Path

import pytest

import manifest
import registry

CAPS = {f"{c.category}.{c.name}": c for c in registry.discover()}
MANIFESTS = {m["id"]: m for m in manifest.load_manifests()}
DOCS = Path(manifest.__file__).resolve().parent / "capabilities"
BOTH = sorted(set(CAPS) & set(MANIFESTS))


def params(func) -> dict:
    return {n: p for n, p in inspect.signature(func).parameters.items()
            if n not in ("ctx", "context") and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)}


def test_every_tool_folder_is_a_package():
    """pkgutil skips a folder without __init__.py, silently: social.youtube_status and social.youtube_publish were never
    registered (found by the test below on 2026-09-30; live since 2026-09-29 without them)."""
    tools = Path(registry.__file__).resolve().parent / "tools"
    missing = [d.name for d in tools.iterdir() if d.is_dir() and d.name != "__pycache__" and any(d.glob("*.py"))
               and not (d / "__init__.py").exists()]
    assert missing == []


def test_every_tool_has_a_manifest_and_every_manifest_a_tool():
    assert sorted(set(CAPS) - set(MANIFESTS)) == [], "tools without a manifest"
    assert sorted(set(MANIFESTS) - set(CAPS)) == [], "manifests without a tool"


@pytest.mark.parametrize("tool_id", BOTH)
def test_the_inputs_are_the_functions_parameters(tool_id):
    have, inputs = params(CAPS[tool_id].func), MANIFESTS[tool_id].get("inputs") or {}
    assert sorted(inputs) == sorted(have), f"{tool_id}: manifest inputs vs function parameters"
    for name, p in have.items():
        spec = inputs[name]
        required = p.default is inspect.Parameter.empty
        assert bool(spec.get("required")) is required, f"{tool_id}.{name}: required"
        if not required and "default" in spec:
            assert spec["default"] == p.default, f"{tool_id}.{name}: default {spec['default']!r} vs {p.default!r}"


@pytest.mark.parametrize("tool_id", BOTH)
def test_the_doc_exists(tool_id):
    assert (DOCS / CAPS[tool_id].doc).is_file(), f"{tool_id}: capabilities/{CAPS[tool_id].doc}"
