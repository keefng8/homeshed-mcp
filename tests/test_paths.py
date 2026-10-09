"""paths.py: every state file lives under DATA_DIR (release v1, 2026-09-29). On someone's own machine /data doesn't
exist (on Windows it would land at the root of the current drive), so no module may hard-code it."""
import importlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARD_CODED = re.compile(r'["\']/data/')


def _in_a_virtualenv(rel) -> bool:
    """A virtualenv inside the repo can have any name (.venv, venv, env, .venv-e): it holds pyvenv.cfg, and installed
    packages live in site-packages (the Github prepper, 2026-09-30: a venv named .venv-e failed this test)."""
    return "site-packages" in rel.parts or (ROOT / rel.parts[0] / "pyvenv.cfg").is_file()


def test_no_module_hard_codes_a_data_path():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] == "tests" or "__pycache__" in rel.parts or rel.name == "paths.py" or _in_a_virtualenv(rel):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if HARD_CODED.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{number}")
    assert offenders == [], "use paths.data_path() instead of a /data literal: " + ", ".join(offenders)


def test_every_state_file_sits_in_a_volume():
    """A state file whose default isn't inside a folder docker-compose.yml mounts lives in the container and every
    redeploy forgets it (state files once sat at /data's top)."""
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    volumes = set(re.findall(r":/data/([\w.-]+)", compose))
    assert {"usage", "vault", "observations"} <= volumes
    # Read-only inputs, not state: uptime.status reads Uptime Kuma's own database where the owner mounts it
    # (for example in a docker-compose.override.yml).
    volumes |= {"kuma"}
    # Rule packs live where Claude Code runs (its hook reads them there), never inside the container: `homeshed-mcp
    # packs` on the host has its own data folder (rule_packs.py, 2026-10-01).
    volumes |= {"packs", "guard-modes.json", "guard_engine.py"}
    outside = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in ("tests", ".venv") or "__pycache__" in rel.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        outside += [f"{rel}: {folder}/" for folder in re.findall(r'data_path\("([^"/]+)/', text) if folder not in volumes]
        outside += [f"{rel}: {name} at the top level" for name in re.findall(r'data_path\("([^"/]+)"\)', text)
                    if name not in volumes]  # a volume folder itself (observations/) is fine
    assert outside == [], "state outside every volume: " + ", ".join(outside)


def test_data_dir_moves_every_default(monkeypatch, tmp_path):
    import paths
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    try:
        importlib.reload(paths)
        assert Path(paths.data_path("usage/usage.json")) == tmp_path / "usage" / "usage.json"
        monkeypatch.delenv("DATA_DIR")
        importlib.reload(paths)
        assert Path(paths.data_path("usage/usage.json")) == Path("/data/usage/usage.json")  # Docker: unchanged
    finally:
        monkeypatch.undo()  # back to conftest's throwaway DATA_DIR, for every test after this one
        importlib.reload(paths)


def test_the_test_run_never_touches_real_data():
    import paths
    assert str(paths.DATA_DIR) == os.environ["DATA_DIR"] and "homeshed-test-data-" in str(paths.DATA_DIR)
