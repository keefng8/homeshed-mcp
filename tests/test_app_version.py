"""The version HomeShed reports: pyproject.toml when running from source (the Docker image said "dev"), else the
installed package, else "dev". Test list drafted by the local model."""
from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import app_version
import cli
import server

HERE = Path(__file__).resolve().parents[1]
_REAL = importlib.metadata.version


def _installed(monkeypatch, value: str | None):
    """This package's metadata answers `value` (None: not installed); every other package keeps its real answer, since
    imports look up their own versions too (httpx2 does)."""
    def version(name):
        if name != app_version.NAME:
            return _REAL(name)
        if value is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return value
    monkeypatch.setattr(importlib.metadata, "version", version)


def test_pyproject_next_to_the_code_wins(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "homeshed-mcp"\nversion = "9.8.7"\n')
    monkeypatch.setattr(app_version, "PYPROJECT", tmp_path / "pyproject.toml")
    _installed(monkeypatch, "0.0.1")
    assert app_version.version() == "9.8.7"


def test_an_installed_package_reports_its_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(app_version, "PYPROJECT", tmp_path / "missing.toml")
    _installed(monkeypatch, "1.2.3")
    assert app_version.version() == "1.2.3"


def test_a_pyproject_without_a_version_falls_through(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "homeshed-mcp"\n')
    monkeypatch.setattr(app_version, "PYPROJECT", tmp_path / "pyproject.toml")
    _installed(monkeypatch, "1.2.3")
    assert app_version.version() == "1.2.3"


def test_neither_says_dev(monkeypatch, tmp_path):
    monkeypatch.setattr(app_version, "PYPROJECT", tmp_path / "missing.toml")
    _installed(monkeypatch, None)
    assert app_version.version() == "dev"


def test_the_source_folder_reports_the_real_pyproject_version_everywhere(monkeypatch):
    _installed(monkeypatch, None)  # like the Docker image: no installed package
    with open(HERE / "pyproject.toml", "rb") as f:
        expected = tomllib.load(f)["project"]["version"]
    assert app_version.version() == server._version() == cli._version() == expected != "dev"
