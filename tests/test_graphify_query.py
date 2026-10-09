"""Tests for graphify.query. subprocess.run is mocked throughout — no real graphify binary or
graph.json needed to run this suite.
"""
from unittest.mock import MagicMock, patch

import pytest


def test_discovered_by_registry():
    from registry import discover

    matches = [c for c in discover() if c.category == "graphify" and c.name == "query"]
    assert len(matches) == 1


def test_success(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    fake_result = MagicMock(returncode=0, stdout="NODE foo [src=bar.py]\n", stderr="")
    with patch("tools.graphify.query.subprocess.run", return_value=fake_result) as mock_run:
        result = module.query(str(tmp_path), "how does foo work")

    assert result == {
        "result": "NODE foo [src=bar.py]",
        "graph_path": str(graph_dir / "graph.json"),
    }
    args = mock_run.call_args.args[0]
    assert args[:2] == ["graphify", "query"]
    assert "how does foo work" in args
    assert "--budget" in args and "2000" in args
    assert "--dfs" not in args


def test_dfs_flag_passed(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    fake_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("tools.graphify.query.subprocess.run", return_value=fake_result) as mock_run:
        module.query(str(tmp_path), "question", dfs=True)

    assert "--dfs" in mock_run.call_args.args[0]


def test_missing_graph_rejected(tmp_path):
    from tools.graphify import query as module

    with pytest.raises(module.GraphifyError, match="no graph found"):
        module.query(str(tmp_path), "question")


def test_empty_question_rejected(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    with pytest.raises(module.GraphifyError, match="non-empty"):
        module.query(str(tmp_path), "")


def test_question_too_long_rejected(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    with pytest.raises(module.GraphifyError, match="too long"):
        module.query(str(tmp_path), "x" * (module.MAX_QUESTION_CHARS + 1))


def test_invalid_budget_rejected(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    with pytest.raises(module.GraphifyError, match="budget"):
        module.query(str(tmp_path), "question", budget=50)
    with pytest.raises(module.GraphifyError, match="budget"):
        module.query(str(tmp_path), "question", budget=50_000)


def test_binary_not_found(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    with patch("tools.graphify.query.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(module.GraphifyError, match="not installed"):
            module.query(str(tmp_path), "question")


def test_cli_failure_propagates_stderr(tmp_path):
    from tools.graphify import query as module

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    fake_result = MagicMock(returncode=1, stdout="", stderr="error: something broke")
    with patch("tools.graphify.query.subprocess.run", return_value=fake_result):
        with pytest.raises(module.GraphifyError, match="something broke"):
            module.query(str(tmp_path), "question")


def test_timeout(tmp_path):
    from tools.graphify import query as module
    import subprocess

    graph_dir = tmp_path / "graphify-out"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text("{}")

    with patch(
        "tools.graphify.query.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="graphify", timeout=30),
    ):
        with pytest.raises(module.GraphifyError, match="timed out"):
            module.query(str(tmp_path), "question")
