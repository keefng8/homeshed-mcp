"""Tests for dev.node. Mocked subprocess -- node is not guaranteed present in every environment
this runs in (confirmed genuinely absent in the production container, see capabilities/dev/node.md)."""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "dev" and c.name == "node"]
    assert len(matches) == 1


def test_invokes_node_with_args(tmp_path):
    from tools.dev.node import node

    fake = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("tools.dev._run.subprocess.run", return_value=fake) as mock_run:
        result = node(["script.js"], cwd=str(tmp_path))
    assert result == {"exit_code": 0, "stdout": "ok", "stderr": ""}
    args, kwargs = mock_run.call_args
    assert args[0] == ["node", "script.js"]
    assert kwargs["cwd"] == tmp_path


def test_empty_args_raises(tmp_path):
    from tools.dev.node import node
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="non-empty"):
        node([], cwd=str(tmp_path))


def test_cwd_not_a_directory():
    from tools.dev.node import node
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="not a directory"):
        node(["x.js"], cwd="/definitely/does/not/exist")


def test_node_not_installed(tmp_path):
    from tools.dev.node import node
    from tools.dev._run import DevError

    with patch("tools.dev._run.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DevError, match="not installed"):
            node(["x.js"], cwd=str(tmp_path))


def test_timeout(tmp_path):
    from tools.dev.node import node
    from tools.dev._run import DevError
    import subprocess

    with patch("tools.dev._run.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 5)):
        with pytest.raises(DevError, match="timed out"):
            node(["x.js"], cwd=str(tmp_path), timeout=5)
