"""backup.export / backup.restore in a temp data folder: what goes in and stays out (vault, images, logs, secrets),
the dry run and confirm, the safety backup, damaged and unsafe backups, owner only."""
from __future__ import annotations

import io
import json
import zipfile

import pytest

import clients
import paths
from tools.backup import backup as bk


@pytest.fixture
def data(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "usage" / "images").mkdir(parents=True)
    (root / "vault").mkdir()
    (root / "usage" / "settings.json").write_text('{"schedule_timezone": "Europe/London"}')
    (root / "usage" / "schedules.json").write_text('{"schedules": {}}')
    (root / "usage" / "client_audit.jsonl").write_text('{"x": 1}\n')
    (root / "usage" / "images" / "a.png").write_bytes(b"\x89PNG")
    (root / "usage" / "grok.json.lock").write_text("")
    (root / "vault" / "vault.json").write_text('{"credentials": {}}')
    leak = "sk-" + "ant-" + "Zq8" * 10  # secret-scan: allow (a dummy built at runtime)
    (root / "usage" / "oops.json").write_text(json.dumps({"api_key": leak}))
    monkeypatch.setattr(paths, "DATA_DIR", root)
    monkeypatch.setenv("BACKUPS_DIR", str(root / "backups"))
    import vault
    monkeypatch.setattr(vault, "list_credentials", lambda: [{"name": "XAI_API_KEY"}, {"name": "ETSY_KEY"}])
    import pro
    monkeypatch.setattr(pro, "check", lambda: (True, ""))
    return root


def names(zpath):
    with zipfile.ZipFile(zpath) as z:
        return sorted(z.namelist()), json.loads(z.read("manifest.json"))


def test_export_keeps_state_and_leaves_out_secrets_vault_images_and_logs(data):
    out = bk.export()
    listed, manifest = names(out["file"])
    assert listed == ["data/usage/schedules.json", "data/usage/settings.json", "manifest.json"]
    assert out["skipped_secret"] == ["usage/oops.json"] and out["encrypted"] is False
    assert manifest["vault_key_names"] == ["ETSY_KEY", "XAI_API_KEY"] and out["vault_keys"] == 2
    assert "Zq8Zq8" not in open(out["file"], "rb").read().decode("latin-1")
    with_logs, _ = names(bk.export(include_logs=True)["file"])
    assert "data/usage/client_audit.jsonl" in with_logs


def test_restore_is_a_dry_run_then_a_confirm_with_a_safety_backup(data):
    made = bk.export()["file"]
    (data / "usage" / "settings.json").write_text('{"schedule_timezone": "UTC"}')
    (data / "usage" / "schedules.json").unlink()
    dry = bk.restore(made.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
    assert dry["dry_run"] is True and dry["changed"] == ["usage/settings.json"] and dry["new"] == ["usage/schedules.json"]
    assert dry["vault_keys_to_reenter"] == ["ETSY_KEY", "XAI_API_KEY"]
    assert "Europe/London" not in (data / "usage" / "settings.json").read_text()  # nothing changed yet
    with pytest.raises(bk.BackupError, match="confirm code isn't valid"):
        bk.restore(made, confirm="nope")
    done = bk.restore(made, confirm=dry["confirm"])
    assert done["restored"] == 2 and "restart" in done["next"]
    assert "Europe/London" in (data / "usage" / "settings.json").read_text()
    assert (data / "usage" / "schedules.json").exists()
    _, safety = names(done["safety_backup"])
    assert {f["name"] for f in safety["files"]} == {"usage/settings.json"}  # the state before the restore
    with pytest.raises(bk.BackupError, match="confirm code isn't valid"):
        bk.restore(made, confirm=dry["confirm"])  # a code works once


def forged(data, files: dict, entries: list) -> str:
    target = data / "backups" / "homeshed-backup-forged.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
        z.writestr("manifest.json", json.dumps({"format": 1, "files": entries}))
    target.write_bytes(buf.getvalue())
    return str(target)


@pytest.mark.parametrize("name", ["../evil.json", "/etc/passwd", "vault/vault.json", ".ssh/id", "a\\..\\b", "c:x"])
def test_unsafe_names_are_refused(data, name):
    import hashlib
    f = forged(data, {f"data/{name}": b"x"}, [{"name": name, "sha256": hashlib.sha256(b"x").hexdigest()}])
    with pytest.raises(bk.BackupError, match="unsafe name"):
        bk.restore(f)


def test_a_damaged_backup_is_refused(data):
    f = forged(data, {"data/usage/a.json": b"changed"}, [{"name": "usage/a.json", "sha256": "0" * 64}])
    with pytest.raises(bk.BackupError, match="checksum"):
        bk.restore(f)
    with pytest.raises(bk.BackupError, match="backups folder"):
        bk.restore(str(data / "usage" / "settings.json"))


def test_only_the_newest_ten_are_kept(data, monkeypatch):
    monkeypatch.setattr(bk, "KEEP", 2)
    stamps = iter(["20260101-000001", "20260101-000002", "20260101-000003"])
    monkeypatch.setattr(bk.time, "strftime", lambda fmt, *a: next(stamps) if fmt == "%Y%m%d-%H%M%S" else "x")
    for _ in range(3):
        bk.export()
    assert sorted(p.name for p in (data / "backups").iterdir()) == [
        "homeshed-backup-20260101-000002.zip", "homeshed-backup-20260101-000003.zip"]


def test_owner_only(data):
    tok = clients.current_client.set("shop-bot")
    try:
        with pytest.raises(bk.BackupError, match="only the owner"):
            bk.export()
    finally:
        clients.current_client.reset(tok)


def test_pro_only(data, monkeypatch):
    import pro
    monkeypatch.setattr(pro, "check", lambda: (False, ""))
    with pytest.raises(bk.BackupError, match="HomeShed Pro feature"):
        bk.export()
    with pytest.raises(bk.BackupError, match="HomeShed Pro feature"):
        bk.restore("x.zip")
