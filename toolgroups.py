"""Optional tool groups: which service-backed tools the AI sees at all (2026-10-06, for v0.1.0: every AI session paid
for all ~100 tool descriptions, including tools that can't work on that machine, in tokens and in wrong-tool picks).

`setup` asks which groups this install uses, pre-ticking the ones it can detect, and saves the answer in the data
folder (usage/tool_groups.json, {"hidden": [group, ...]}). The server doesn't register a hidden group's tools, so they don't
appear in the tool list. Everything not in GROUPS is the core and always loads. With no saved answer nothing is
hidden, so an install that never ran the new setup keeps every tool. A change applies when the server next starts.
Safety is unchanged: tools that write or cost money still start switched off (toolswitch.py).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from paths import data_path



def groups_file() -> Path:
    """The saved answer: TOOL_GROUPS_FILE, else DATA_DIR/usage/tool_groups.json (read on every call, so tests can point
    it at a temp folder)."""
    return Path(os.environ.get("TOOL_GROUPS_FILE") or data_path("usage/tool_groups.json"))


# name: (what it is, the tool categories in it, settings any one of which means it's set up here).
GROUPS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "local-ai": ("A local AI model (Ollama, LM Studio, llama.cpp): drafts, summaries, quick decisions",
                 ("local_ai",), ("LOCAL_AI_BASE_URL", "LOCAL_AI_GATEWAY_URL", "LOCAL_AI_DECIDE_BASE_URL")),
    "docker": ("Docker: list, inspect and (when switched on) start or stop containers", ("docker",), ("DOCKER_HOST",)),
    "notify": ("Phone notifications through ntfy", ("notify",), ("NTFY_BASE_URL",)),
    "images": ("Image generation (your own local image service, or a paid provider on your key)", ("image",),
               ("IMAGE_BASE_URL", "IMAGE_DEFAULT_PROVIDER")),
    "paid-ai": ("A paid language model relayed for your apps, on your own key (xAI first)", ("llm",),
                ("XAI_API_KEY", "X_AI")),
    "proxmox": ("Proxmox VE: nodes, VMs, storage; changes wait for your approval", ("proxmox",),
                ("PROXMOX_API_TOKEN", "PROXMOX_TOKEN_ID", "PROXMOX_HOST")),
    "shops": ("Online shops: Etsy and Printify (changes wait for your approval)", ("etsy", "printify", "commerce"),
              ("ETSY_KEYSTRING", "PRINTIFY_API_TOKEN")),
    "threads": ("Post to your Threads account (off until you switch posting on)", ("threads",),
                ("THREADS_ACCESS_TOKEN", "THREADS_APP_ID")),
    "uptime": ("Uptime Kuma monitors", ("uptime",), ("KUMA_DB_PATH",)),
    "graphify": ("Code maps from graphify", ("graphify",), ()),
    "dev": ("Code-running tools (build, lint, test): off by default, for disposable environments only", ("dev",), ()),
}
PUBLIC = frozenset(GROUPS)
# An install's own extra groups (tools only it has), and extra settings that mean a public group is set up there:
# private_settings.TOOL_GROUPS_EXTRA, in the same shape. The public copy has no private_settings.
try:
    from private_settings import TOOL_GROUPS_EXTRA
except ImportError:
    TOOL_GROUPS_EXTRA = {}
for _name, (_what, _cats, _settings) in TOOL_GROUPS_EXTRA.items():
    if _name in GROUPS:
        _base = GROUPS[_name]
        GROUPS[_name] = (_base[0], _base[1] + tuple(_cats), _base[2] + tuple(_settings))
    else:
        GROUPS[_name] = (_what, tuple(_cats), tuple(_settings))
CATEGORY_GROUP = {cat: name for name, (_, cats, _) in GROUPS.items() for cat in cats}


def hidden() -> set[str]:
    """The saved hidden groups (unknown names ignored). No file, or one that can't be read: nothing hidden."""
    try:
        data = json.loads(groups_file().read_text(encoding="utf-8"))
        return {g for g in data.get("hidden", []) if g in GROUPS}
    except (OSError, ValueError, AttributeError, TypeError):
        return set()


def is_hidden(category: str) -> bool:
    return CATEGORY_GROUP.get(category) in hidden()


def save(hidden_groups) -> Path:
    """Write the answer (atomic). Returns the file."""
    keep = sorted(g for g in set(hidden_groups) if g in GROUPS)
    path = groups_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"hidden": keep}, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return path


def detected(is_set, docker_ok: bool = False, model_found: bool = False, graphify_ok: bool = False,
             dev_on: bool = False) -> set[str]:
    """The groups this machine looks set up for: one of a group's settings is set (is_set(name) checks the environment
    and the vault's fallbacks), or the probe the caller ran found it (Docker socket, a local model, graphify, ENABLE_TOOLS
    naming dev)."""
    found = {name for name, (_, _, settings) in GROUPS.items() if any(is_set(s) for s in settings)}
    found |= {name for name, ok in (("docker", docker_ok), ("local-ai", model_found), ("graphify", graphify_ok),
                                    ("dev", dev_on)) if ok}
    return found
