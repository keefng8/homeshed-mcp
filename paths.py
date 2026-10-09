"""Where this server keeps its state (release v1, 2026-09-29). Every state file's default lives under DATA_DIR: /data in
the Docker image (unchanged for existing installs), and a per-user folder when `homeshed-mcp` runs on someone's own
machine (cli.py sets DATA_DIR before anything imports this). Each file's own variable (USAGE_FILE, VAULT_FILE, ...)
still overrides its default. test_paths.py fails if a module hard-codes /data again."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or "/data")


def data_path(relative: str) -> str:
    """DATA_DIR/relative, as a string (the modules wrap it in Path themselves)."""
    return str(DATA_DIR / relative)
