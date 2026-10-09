"""Tests for dev.lint. Mocked subprocess -- the command is caller-supplied and its toolchain
isn't guaranteed present."""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "dev" and c.name == "lint"]
    assert len(matches) == 1


def test_invokes_full_command(tmp_path):
    from tools.dev.lint import lint

    fake = MagicMock(returncode=0, stdout="All checks passed!", stderr="")
    with patch("tools.dev._run.subprocess.run", return_value=fake) as mock_run:
        result = lint(["ruff", "check", "."], cwd=str(tmp_path))
    assert result["exit_code"] == 0
    args, _ = mock_run.call_args
    assert args[0] == ["ruff", "check", "."]


def test_empty_command_raises(tmp_path):
    from tools.dev.lint import lint
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="non-empty"):
        lint([], cwd=str(tmp_path))


def test_cwd_not_a_directory():
    from tools.dev.lint import lint
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="not a directory"):
        lint(["ruff", "check"], cwd="/definitely/does/not/exist")


def test_binary_not_found(tmp_path):
    from tools.dev.lint import lint
    from tools.dev._run import DevError

    with patch("tools.dev._run.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DevError, match="not installed"):
            lint(["ruff", "check"], cwd=str(tmp_path))
