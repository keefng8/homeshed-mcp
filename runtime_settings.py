"""Tool server behaviour settings that change without a restart (GET/PUT /settings, owner token). Each has a
schema entry an admin client can draw its form from. Addresses and secrets stay in .env; these are behaviour
switches, plus the two addresses an end user's setup is most likely to change: empty means the .env value.

One small JSON file on the usage volume, re-read only when it changes. A missing or unreadable file
means every setting is at its default; a saved value that no longer fits the schema also falls back
to its default.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from paths import data_path

SETTINGS_FILE = Path(os.environ.get("TOOL_SETTINGS_FILE") or data_path("usage/settings.json"))
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SPACED_RE = re.compile(r"^[A-Za-z0-9_-](?:[A-Za-z0-9 _-]{0,62}[A-Za-z0-9_-])?$")  # a name with single inner spaces
URL_RE = re.compile(r"^https?://[^\s/$.?#][^\s]{0,199}$")

SCHEMA = {
    "local_ai_allow_cloud": {
        "type": "bool", "default": True, "group": "AI models",
        "label": "Let the local AI use cloud models when needed",
        "help": "When your own model is busy or can't answer, the local AI may use NVIDIA's cloud models. "
                "Switch off to keep every prompt on your own machines.",
    },
    "ntfy_default_topic": {
        "type": "str", "default": os.environ.get("NTFY_DEFAULT_TOPIC", "").strip(), "pattern": TOPIC_RE.pattern,
        "group": "Notifications",
        "label": "Alert channel for phone notifications",
        "help": "The ntfy channel alerts go to when a tool doesn't name one. Your phone must follow this channel.",
    },
    "local_ai_base_url": {
        "type": "url", "default": "", "group": "AI models",
        "label": "Your own model server",
        "help": "The address of the AI model running on your own machine (llama.cpp, Ollama or LM Studio), ending "
                "in /v1. Leave empty to use the tool server's settings file, or if you have no local model.",
    },
    "ntfy_base_url": {
        "type": "url", "default": "", "group": "Notifications",
        "label": "Phone alerts server",
        "help": "The address of your ntfy server (for example https://ntfy.sh or your own). Leave empty to use the "
                "tool server's settings file. The access token lives under Credentials.",
    },
    "stray_command_stop": {
        "type": "bool", "default": True, "group": "Notifications",
        "label": "Stop runaway one-off commands",
        "help": "A command run inside the tool server by hand or by a session (not the server itself) that uses a "
                "whole CPU core for 10 minutes is stopped, and you get a phone alert saying what it was.",
    },
    "bugs_search_public": {
        "type": "bool", "default": False, "group": "Known bugs",
        "label": "Look for similar public reports when nothing known matches",
        "help": "Searches GitHub issues for the same problem and shows links. Only a cleaned version of the error is "
                "sent: no file paths, addresses, numbers or secrets. Off until you switch it on.",
    },
    "bug_capture": {
        "type": "bool", "default": True, "group": "Known bugs",
        "label": "Suggest bugs from failed tool calls",
        "help": "When the same error comes back in two different sessions, it shows under Suggested bugs for you to "
                "keep or dismiss, so nobody has to remember to report it. Your own blocks, refusals and wrongly used "
                "tools are counted but never suggested. Everything stays on this machine.",
    },
    "library_url": {
        "type": "url", "default": "", "group": "Known bugs",
        "label": "HomeShed Library address (to share known bugs)",
        "help": "If you also run the HomeShed Library, its address with the port, for example http://your-library-server:8770. "
                "Both then hold the same known bugs. Also add the library's key under Credentials as LIBRARY_BUGS_KEY. "
                "Leave empty if you don't use the library: nothing is sent.",
    },
    "claude_compact_window": {
        "type": "select", "default": "default", "group": "Claude Code",
        "options": ["default", "100000", "150000", "200000", "300000", "400000", "500000", "700000", "1000000"],
        "option_labels": {"default": "Claude Code's default", "100000": "100k tokens", "150000": "150k tokens",
                          "200000": "200k tokens", "300000": "300k tokens", "400000": "400k tokens",
                          "500000": "500k tokens", "700000": "700k tokens", "1000000": "1M tokens"},
        "label": "When Claude Code compacts a chat",
        "help": "How full a chat gets before Claude Code summarises it to make room. Smaller is cheaper but forgets "
                "sooner. A size above your model's window (200k on most models) just means its default. Written to "
                "Claude Code's own settings (the old file is kept as settings.json.homeshed-bak). Claude Code reads it "
                "when a chat starts: restart open chats (exit, then claude --continue).",
    },
}
# Settings that also change something outside this file, applied before the save: a ValueError saves nothing.
def _apply_compact_window(value: str) -> None:
    import rule_packs  # here, not at the top: rule_packs is heavy and most saves never need it
    try:
        res = rule_packs.compact_window(None if value == "default" else int(value))
    except rule_packs.PackError as e:
        raise ValueError(str(e)) from None
    if res["reason"] == "unreadable":
        raise ValueError(f"Claude Code's settings ({res['path']}) couldn't be read, so nothing was changed.")
    if res["reason"] == "changed meanwhile":
        raise ValueError("Claude Code's settings changed while saving, so nothing was changed. Save again.")


APPLY = {"claude_compact_window": _apply_compact_window}
# Which .env name each address setting stands in for (address() below).
ADDRESS_OVERRIDES = {"LOCAL_AI_BASE_URL": "local_ai_base_url", "NTFY_BASE_URL": "ntfy_base_url"}

try:  # The owner's private add-on settings (private_settings.py). The public copy leaves it out.
    import private_settings
    SCHEMA.update(private_settings.SCHEMA)
    ADDRESS_OVERRIDES.update(private_settings.ADDRESS_OVERRIDES)
except ImportError:
    pass
try:  # the sharing switch lives with the share tools: a build without them shows no switch
    from tools.share import SETTINGS as _SHARE_SETTINGS
    SCHEMA.update(_SHARE_SETTINGS)
except ImportError:
    pass

try:  # the image provider switches (paid providers start off) live with the image tools
    from tools.image.providers import SETTINGS as _IMAGE_SETTINGS
    SCHEMA.update(_IMAGE_SETTINGS)
except ImportError:
    pass

try:  # Google text to speech's paid allowance (none: free characters only) lives with the tts tools
    from tools.tts import SETTINGS as _TTS_SETTINGS
    SCHEMA.update(_TTS_SETTINGS)
except ImportError:
    pass

try:  # the language-model relay switches (llm.chat; every paid provider starts off) live with the llm tools
    from tools.llm.providers import SETTINGS as _LLM_SETTINGS
    SCHEMA.update(_LLM_SETTINGS)
except ImportError:
    pass

try:  # the shop proxy switches (etsy.*, printify.*; both start off) live with the shop tools
    from tools.commerce import SETTINGS as _COMMERCE_SETTINGS
    SCHEMA.update(_COMMERCE_SETTINGS)
except ImportError:
    pass

try:  # the Proxmox alarms' switch and lines live with the monitor
    from proxmox_monitor import SETTINGS as _PROXMOX_SETTINGS
    SCHEMA.update(_PROXMOX_SETTINGS)
except ImportError:
    pass

try:  # the agent-messages switch (on) lives with the agents tools
    from tools.agents import SETTINGS as _AGENTS_SETTINGS
    SCHEMA.update(_AGENTS_SETTINGS)
except ImportError:
    pass

try:  # the to-do list's app route (off) lives with the to-do tools
    from tools.todo import SETTINGS as _TODO_SETTINGS
    SCHEMA.update(_TODO_SETTINGS)
except ImportError:
    pass

try:  # the schedules' time zone lives with the schedule tools
    from tools.schedule import SETTINGS as _SCHEDULE_SETTINGS
    SCHEMA.update(_SCHEDULE_SETTINGS)
except ImportError:
    pass

try:  # the owner's outgoing mail server lives with the email tools
    from tools.email import SETTINGS as _EMAIL_SETTINGS
    SCHEMA.update(_EMAIL_SETTINGS)
except ImportError:
    pass

_lock = threading.Lock()
_cache: tuple[object, dict] = (None, {})


# A server name (email settings, 2026-10-24) and an email address: plain checks, no lookups.
HOST_RE = re.compile(r"(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+")
EMAIL_RE = re.compile(r"[^@\s<>,;\"]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,}")


def validate(key: str, value):
    """The value if it fits the schema; ValueError with a message fit for the Settings page if not."""
    if key not in SCHEMA:
        raise ValueError(f"Unknown setting: {key}")
    spec = SCHEMA[key]
    if spec["type"] == "bool" and not isinstance(value, bool):
        raise ValueError(f"{spec['label']}: must be on or off.")
    if spec["type"] == "str" and not (isinstance(value, str) and TOPIC_RE.fullmatch(value.strip())):
        raise ValueError(f"{spec['label']}: 1 to 64 letters, numbers, dashes or underscores.")
    if spec["type"] == "url" and not (isinstance(value, str) and (not value.strip() or URL_RE.fullmatch(value.strip()))):
        raise ValueError(f"{spec['label']}: an address starting with http:// or https://, or empty.")
    if spec["type"] == "host" and not (isinstance(value, str) and (not value.strip() or HOST_RE.fullmatch(value.strip()))):
        raise ValueError(f"{spec['label']}: a server name like smtp.example.com, or empty.")
    if spec["type"] == "email" and not (isinstance(value, str) and (not value.strip() or EMAIL_RE.fullmatch(value.strip()))):
        raise ValueError(f"{spec['label']}: an email address like you@example.com, or empty.")
    if spec["type"] == "select" and value not in spec["options"]:
        raise ValueError(f"{spec['label']}: choose one of the listed options.")
    if spec["type"] == "names":  # a list of names, e.g. apps: "a,b", or empty for none (2026-10-09)
        parts = [" ".join(p.split()) for p in value.split(",") if p.strip()] if isinstance(value, str) else None
        name_re = SPACED_RE if spec.get("spaces") else TOPIC_RE  # agents' names have spaces: "Planner bot"
        if parts is None or len(parts) > 20 or not all(name_re.fullmatch(p) for p in parts):
            raise ValueError(f"{spec['label']}: names separated by commas (letters, numbers, "
                             f"{'spaces, ' if spec.get('spaces') else ''}dashes or underscores), or empty for none.")
        return ",".join(parts)
    return value.strip() if isinstance(value, str) else value


def _raw() -> dict:
    global _cache
    try:
        st = SETTINGS_FILE.stat()
        sig = (str(SETTINGS_FILE), st.st_mtime_ns, st.st_size)
    except OSError:
        _cache = (None, {})
        return {}
    if sig != _cache[0]:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        _cache = (sig, data)
    return dict(_cache[1])


def get(key: str):
    raw = _raw()
    try:
        return validate(key, raw[key]) if key in raw else SCHEMA[key]["default"]
    except ValueError:
        return SCHEMA[key]["default"]


def values() -> dict:
    with _lock:
        return {key: get(key) for key in SCHEMA}


def update(changes: dict) -> dict:
    """Checks every change first (nothing is saved if one is bad), keeps other keys, writes atomically."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Send the changes as a JSON object.")
    checked = {key: validate(key, value) for key, value in changes.items()}
    for key, value in checked.items():
        if key in APPLY:
            APPLY[key](value)
    with _lock:
        raw = _raw()
        raw.update(checked)
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=1), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
        _forget()  # a same-size change inside one clock tick keeps (mtime, size) unchanged: re-read next time
    return values()


def _forget() -> None:
    global _cache
    _cache = (None, {})


def address(env_name: str) -> str:
    """An address a tool uses: the Settings page's value when one is set, else the .env value."""
    key = ADDRESS_OVERRIDES.get(env_name)
    return (get(key) if key else "") or os.environ.get(env_name, "")
