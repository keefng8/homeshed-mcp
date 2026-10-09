"""Test isolation: no test may write the real state files. On Windows the container paths /data/usage/...
land in D:\\data\\usage\\..., and tests left usage.json there (observation #0016, 2026-09-28). Set before any
module under test is imported, so their path constants point at a throwaway folder."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_STATE = tempfile.mkdtemp(prefix="mcp-server-test-state-")
for var, name in (("USAGE_FILE", "usage.json"), ("CLIENTS_FILE", "clients.json"),
                  ("CLIENTS_AUDIT_FILE", "client_audit.jsonl"), ("DISABLED_TOOLS_FILE", "disabled_tools.json"),
                  ("TOOL_SETTINGS_FILE", "settings.json"), ("YOUTUBE_POSTS_FILE", "youtube_posts.json"),
                  ("VAULT_FILE", "vault.json"), ("VAULT_AUDIT_FILE", "vault_audit.jsonl"),
                  ("PROJECTS_FILE", "projects.json"),
                  ("PROXMOX_PENDING_FILE", "proxmox_pending.json"), ("OWNER_INBOX_FILE", "owner_inbox.json"),
                  ("THREADS_POSTS_FILE", "threads_posts.json"), ("SIGNALS_DB", "signals.db"),
                  ("IMAGE_OUTPUT_DIR", "images"),
                  ("IMAGE_USAGE_FILE", "image_daily.json"), ("LLM_USAGE_FILE", "llm_daily.json"),
                  ("LLM_USAGE_LOG", "llm_usage.jsonl"), ("TOOL_GROUPS_FILE", "tool_groups.json"),
                  ("PRINTIFY_PENDING_FILE", "printify_pending.json"), ("PRINTIFY_PUBLISHED_FILE", "printify_published.json"),
                  ("PRINTIFY_DECISIONS_FILE", "printify_decisions.json"), ("PRINTIFY_EDITS_FILE", "printify_edits.json"),
                  ("ETSY_ATTRIBUTES_FILE", "etsy_attributes.json"), ("ETSY_DEACTIVATED_FILE", "etsy_deactivated.json"),
                  ("ETSY_CONNECTION_FILE", "etsy_connection.json")):
    os.environ[var] = os.path.join(_STATE, name)


@pytest.fixture(autouse=True)
def _no_private_decision_hook(monkeypatch):
    """The owner's private decision hook (private_decision_hooks.py) passes approvals on to a real app: never from a
    test. A test that wants one sets its own module in sys.modules."""
    import sys
    monkeypatch.setitem(sys.modules, "private_decision_hooks", None)


@pytest.fixture
def outside_any_repo(tmp_path):
    """A folder with no git repository above it: pytest's tmp_path where that works (Linux CI), else D:\\ on a PC whose
    user folder is itself a git repo, so the temp tree walks up to one. A fixed "D:/" alone raised FileNotFoundError on
    Linux (the prepper's CI run, 2026-10-01). Skips when neither works."""
    for candidate in (tmp_path, Path("D:/")):
        if candidate.is_dir() and subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=candidate,
                                                 capture_output=True).returncode != 0:
            return candidate
    pytest.skip("no folder outside a git repository on this machine")
