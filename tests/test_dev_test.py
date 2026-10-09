"""Tests for dev.test. Mocked subprocess -- the command is caller-supplied and its toolchain
isn't guaranteed present."""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "dev" and c.name == "test"]
    assert len(matches) == 1


def test_invokes_full_command(tmp_path):
    from tools.dev.test import test as dev_test

    fake = MagicMock(returncode=0, stdout="5 passed", stderr="")
    with patch("tools.dev._run.subprocess.run", return_value=fake) as mock_run:
        result = dev_test(["pytest", "tests/"], cwd=str(tmp_path))
    assert result["exit_code"] == 0
    assert "5 passed" in result["stdout"]
    args, _ = mock_run.call_args
    assert args[0] == ["pytest", "tests/"]


def test_empty_command_raises(tmp_path):
    from tools.dev.test import test as dev_test
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="non-empty"):
        dev_test([], cwd=str(tmp_path))


def test_cwd_not_a_directory():
    from tools.dev.test import test as dev_test
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="not a directory"):
        dev_test(["pytest"], cwd="/definitely/does/not/exist")


def test_binary_not_found(tmp_path):
    from tools.dev.test import test as dev_test
    from tools.dev._run import DevError

    with patch("tools.dev._run.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DevError, match="not installed"):
            dev_test(["pytest"], cwd=str(tmp_path))
