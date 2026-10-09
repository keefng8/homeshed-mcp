"""Tests for dev.build. Mocked subprocess -- the command is caller-supplied and its toolchain
isn't guaranteed present."""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "dev" and c.name == "build"]
    assert len(matches) == 1


def test_invokes_full_command(tmp_path):
    from tools.dev.build import build

    fake = MagicMock(returncode=0, stdout="built", stderr="")
    with patch("tools.dev._run.subprocess.run", return_value=fake) as mock_run:
        result = build(["npm", "run", "build"], cwd=str(tmp_path))
    assert result["exit_code"] == 0
    args, kwargs = mock_run.call_args
    assert args[0] == ["npm", "run", "build"]
    assert kwargs["timeout"] == 300


def test_empty_command_raises(tmp_path):
    from tools.dev.build import build
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="non-empty"):
        build([], cwd=str(tmp_path))


def test_timeout_out_of_range_raises(tmp_path):
    from tools.dev.build import build
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="timeout must be"):
        build(["make"], cwd=str(tmp_path), timeout=1000)


def test_binary_not_found(tmp_path):
    from tools.dev.build import build
    from tools.dev._run import DevError

    with patch("tools.dev._run.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DevError, match="not installed"):
            build(["make"], cwd=str(tmp_path))
