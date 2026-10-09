"""The version HomeShed reports in serverInfo and `doctor`. Running from the source folder (the Docker image, a
checkout) there's no installed package, so both said "dev" (found verifying the 2026-09-30 deploy). The pyproject.toml
next to this file comes first; a wheel install has none here, so it reads the installed package's metadata."""
from __future__ import annotations

import tomllib
from pathlib import Path

NAME = "homeshed-mcp"
PYPROJECT = Path(__file__).with_name("pyproject.toml")


def version() -> str:
    try:
        with open(PYPROJECT, "rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    except (OSError, KeyError, TypeError, ValueError):
        pass
    try:
        from importlib.metadata import version as installed

        return installed(NAME)
    except Exception:
        return "dev"
