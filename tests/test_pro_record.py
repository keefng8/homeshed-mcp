import base64
import hashlib
import zipfile

from pro_record import verify


def _b64(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _unpacked(tmp_path):
    files = {"homeshed_pro/__init__.py": b"GATE = 'signed proof'\n", "homeshed_pro/tools.py": b"def run(): pass\n"}
    record = "".join(f"{n},{_b64(d)},{len(d)}\n" for n, d in files.items())
    record += "homeshed_pro-0.2.0.dist-info/RECORD,,\n"
    whl = tmp_path / "w.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
        z.writestr("homeshed_pro-0.2.0.dist-info/RECORD", record)
    out = tmp_path / "0.2.0"
    zipfile.ZipFile(whl).extractall(out)
    return out, hashlib.sha256(record.encode()).hexdigest()


def test_untouched_folder_passes(tmp_path):
    folder, rh = _unpacked(tmp_path)
    assert verify(folder, rh) is None


def test_patched_gate_is_refused(tmp_path):
    folder, rh = _unpacked(tmp_path)
    (folder / "homeshed_pro/__init__.py").write_text("GATE = True\n")
    assert "changed" in verify(folder, rh)


def test_rewritten_record_is_refused(tmp_path):
    folder, rh = _unpacked(tmp_path)
    (folder / "homeshed_pro/__init__.py").write_text("GATE = True\n")
    rec = next(folder.glob("*.dist-info/RECORD"))
    rec.write_text(rec.read_text().replace(rec.read_text().split(",")[1], _b64(b"GATE = True\n")))
    assert "signed" in verify(folder, rh)


def test_added_file_is_refused(tmp_path):
    folder, rh = _unpacked(tmp_path)
    (folder / "homeshed_pro/extra.py").write_text("x = 1\n")
    assert "extra" in verify(folder, rh)


def test_missing_file_and_bad_hash_value(tmp_path):
    folder, rh = _unpacked(tmp_path)
    (folder / "homeshed_pro/tools.py").unlink()
    assert "missing" in verify(folder, rh)
    (tmp_path / "b").mkdir()
    folder2, _ = _unpacked(tmp_path / "b")
    assert "signed" in verify(folder2, "0" * 64)


def test_hand_made_folder_without_record(tmp_path):
    (tmp_path / "homeshed_pro").mkdir()
    (tmp_path / "homeshed_pro/__init__.py").write_text("GATE = True\n")
    assert "RECORD" in verify(tmp_path, "0" * 64)
