"""Tests for dev.python. Runs a real python subprocess -- python itself is this container's own
runtime, always present, not worth mocking away."""
from unittest.mock import patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "dev" and c.name == "python"]
    assert len(matches) == 1


def test_real_script_runs(tmp_path):
    from tools.dev.python import python

    script = tmp_path / "hello.py"
    script.write_text("print('hi')")
    result = python(["hello.py"], cwd=str(tmp_path))
    assert result["exit_code"] == 0
    assert "hi" in result["stdout"]


def test_nonzero_exit_captured(tmp_path):
    from tools.dev.python import python

    script = tmp_path / "fail.py"
    script.write_text("import sys; sys.exit(3)")
    result = python(["fail.py"], cwd=str(tmp_path))
    assert result["exit_code"] == 3


def test_stderr_captured(tmp_path):
    from tools.dev.python import python

    script = tmp_path / "err.py"
    script.write_text("import sys; print('oops', file=sys.stderr)")
    result = python(["err.py"], cwd=str(tmp_path))
    assert "oops" in result["stderr"]


def test_empty_args_raises(tmp_path):
    from tools.dev.python import python
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="non-empty"):
        python([], cwd=str(tmp_path))


def test_cwd_not_a_directory():
    from tools.dev.python import python
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="not a directory"):
        python(["-c", "pass"], cwd="/definitely/does/not/exist")


def test_timeout_out_of_range_raises(tmp_path):
    from tools.dev.python import python
    from tools.dev._run import DevError

    with pytest.raises(DevError, match="timeout must be"):
        python(["-c", "pass"], cwd=str(tmp_path), timeout=0)
    with pytest.raises(DevError, match="timeout must be"):
        python(["-c", "pass"], cwd=str(tmp_path), timeout=1000)


def test_real_timeout(tmp_path):
    from tools.dev.python import python
    from tools.dev._run import DevError

    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(30)")
    with pytest.raises(DevError, match="timed out"):
        python(["slow.py"], cwd=str(tmp_path), timeout=1)


def test_binary_not_found(tmp_path):
    from tools.dev.python import python
    from tools.dev._run import DevError

    with patch("tools.dev._run.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(DevError, match="not installed"):
            python(["-c", "pass"], cwd=str(tmp_path))
