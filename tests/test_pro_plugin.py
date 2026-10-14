"""pro_plugin.py: `pro update` downloads the Pro tools package with the Pro key (in a header), checks its size and
SHA-256, refuses an unsafe or wrong package and one this HomeShed is too old for, unpacks it next to the previous one,
and load() registers its tools and capability files without ever stopping the free tools. The Pro website is faked;
files go under tmp_path."""
import base64
import hashlib
import io
import json
import sys
import zipfile
from contextlib import contextmanager

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import manifest
import pro_plugin as pp
import pro_proof
import registry

KEY = "mav_" + "k" * 30  # secret-scan: allow (a dummy)


def _b64sha(data: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def wheel(version="0.2.0", requires=">=0.2.0", extra=None, tool_name="demo", record=True) -> bytes:
    files = {
        "homeshed_pro/__init__.py": "from homeshed_pro import demo  # noqa: F401\n",
        "homeshed_pro/demo.py": "from registry import tool\n\n"
                                f"@tool(name='{tool_name}', category='protest', doc='protest/{tool_name}.md')\n"
                                f"def {tool_name}() -> dict:\n    return {{'ok': True}}\n",
        f"homeshed_pro/capabilities/protest/{tool_name}.json": json.dumps(
            {"id": f"protest.{tool_name}", "name": "Demo", "version": "1.0.0", "description": "d",
             "category": "protest", "risk": "read", "transport": "mcp"}),
        f"homeshed_pro-{version}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: homeshed-pro\nVersion: {version}\n"
        + (f"Requires-Dist: homeshed-mcp{requires}\n" if requires else ""),
        **(extra or {}),
    }
    if record:  # the wheel's own list of every file and its hash (PEP 376), as a real build writes it
        rec = f"homeshed_pro-{version}.dist-info/RECORD"
        files[rec] = "".join(f"{n},{_b64sha(d.encode())},{len(d.encode())}\n" for n, d in files.items()) + f"{rec},,\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def record_sha(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if n.endswith(".dist-info/RECORD")]
        return hashlib.sha256(z.read(names[0])).hexdigest() if names else "0" * 64


RELEASE = Ed25519PrivateKey.generate()  # a test release key; pro_proof.RELEASE_KEYS gets its public half below
RELEASE_PUB = base64.urlsafe_b64encode(RELEASE.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw)).rstrip(b"=").decode()


def signature(version: str, rsha: str, key=RELEASE) -> str:
    return base64.urlsafe_b64encode(key.sign(pro_proof.wheel_message(version, rsha))).rstrip(b"=").decode()


class Site:
    def __init__(self, data: bytes, version="0.2.0", status=200, checksum=None, requires=None, signed=True,
                 key=RELEASE):
        self.data, self.version, self.status = data, version, status
        self.sha = checksum or hashlib.sha256(data).hexdigest()
        self.requires, self.seen = requires, []
        rsha = record_sha(data) if data[:2] == b"PK" else "0" * 64
        self.signed = {"kid": "r1", "sig": signature(version, rsha, key), "record_sha256": rsha} if signed else {}

    def get(self, url, headers=None, **kw):
        self.seen.append((url, headers))
        body = {"data": {"name": "homeshed-pro", "version": self.version, "filename": "x.whl", "size": len(self.data),
                         "sha256": self.sha, "url": f"/api/pro-api/packages/homeshed-pro/{self.version}",
                         **self.signed, **({"requires_homeshed": self.requires} if self.requires else {})}}
        return httpx.Response(self.status, json=body if self.status == 200 else {"error": {"message": "x"}},
                              request=httpx.Request("GET", url))

    @contextmanager
    def stream(self, method, url, headers=None, **kw):
        self.seen.append((url, headers))
        yield httpx.Response(200, content=self.data, headers={"x-checksum-sha256": self.sha},
                             request=httpx.Request(method, url))


@pytest.fixture
def env(monkeypatch, tmp_path):
    import mavis_pro
    import vault
    monkeypatch.setenv("HOMESHED_PRO_DIR", str(tmp_path / "pro"))
    monkeypatch.setattr(vault, "secret", lambda name: KEY if name == mavis_pro.TOKEN else None)
    monkeypatch.setattr(mavis_pro, "api_url", lambda: "https://pro.example/api")
    monkeypatch.setattr(mavis_pro, "base_url", lambda: "https://pro.example")
    monkeypatch.setattr(pp, "_loaded", {})
    monkeypatch.setattr(pro_proof, "RELEASE_KEYS", {"r1": RELEASE_PUB})
    monkeypatch.setattr(manifest, "PLUGIN_DIRS", [])
    monkeypatch.setattr(sys, "path", list(sys.path))
    sys.modules.pop("homeshed_pro", None)
    sys.modules.pop("homeshed_pro.demo", None)

    def use(site):
        monkeypatch.setattr(httpx, "get", site.get)
        monkeypatch.setattr(httpx, "stream", site.stream)
        return site
    yield use
    sys.modules.pop("homeshed_pro", None)
    sys.modules.pop("homeshed_pro.demo", None)
    registry._REGISTRY[:] = [c for c in registry._REGISTRY if c.category != "protest"]


def test_update_downloads_checks_unpacks_and_load_registers_the_tools(env, tmp_path):
    site = env(Site(wheel()))
    out = pp.update(have="0.2.0")
    assert out["updated"] is True and out["version"] == "0.2.0" and "Restart HomeShed" in out["note"]
    assert all(h["Authorization"] == f"Bearer {KEY}" for _, h in site.seen)  # the key in a header...
    assert all(KEY not in url for url, _ in site.seen)  # ...never in a URL
    assert pp.installed()["version"] == "0.2.0" and (tmp_path / "pro" / "0.2.0" / "homeshed_pro").is_dir()
    assert not list((tmp_path / "pro").glob("*.download"))
    assert pp.update(have="0.2.0")["updated"] is False  # up to date: nothing downloaded again
    loaded = pp.load()
    assert loaded == {"loaded": True, "version": "0.2.0"}
    assert registry.get_capability("protest.demo") is not None
    assert any(m["id"] == "protest.demo" for m in manifest.load_manifests())


def test_a_new_version_keeps_the_previous_one_for_a_rollback(env, tmp_path):
    env(Site(wheel("0.2.0"), "0.2.0"))
    pp.update(have="0.2.0")
    env(Site(wheel("0.2.1"), "0.2.1"))
    out = pp.update(have="0.2.0")
    assert out["previous"] == "0.2.0" and pp.installed()["previous"] == "0.2.0"
    assert sorted(p.name for p in (tmp_path / "pro").iterdir() if p.is_dir()) == ["0.2.0", "0.2.1"]


@pytest.mark.parametrize("site, match", [
    (lambda: Site(wheel(), checksum="0" * 64), "checksum"),
    (lambda: Site(wheel(extra={"evil.py": "x"})), "outside homeshed_pro"),
    (lambda: Site(wheel(extra={"homeshed_pro/../../evil.py": "x"})), "unsafe file name"),
    (lambda: Site(b"not a zip"), "valid package"),
    (lambda: Site(wheel(), status=403), "membership isn't active"),
    (lambda: Site(wheel(), status=401), "doesn't know this install's key"),
    (lambda: Site(wheel(), status=404), "no Pro tools package"),
    (lambda: Site(wheel(), requires=">=0.3.0"), "needs HomeShed 0.3.0"),
    (lambda: Site(wheel(requires=">=0.9.0")), "needs HomeShed 0.9.0"),
])
def test_bad_downloads_install_nothing(env, tmp_path, site, match):
    env(site())
    with pytest.raises(pp.ProPluginError, match=match):
        pp.update(have="0.2.0")
    assert pp.installed() is None and not [p for p in (tmp_path / "pro").glob("*") if p.is_dir()]


def test_no_key_and_a_broken_package_never_stop_the_free_tools(env, monkeypatch, tmp_path):
    import vault
    monkeypatch.setattr(vault, "secret", lambda name: None)
    with pytest.raises(pp.ProPluginError, match="pro connect"):
        pp.update(have="0.2.0")
    assert pp.load() == {"loaded": False, "reason": "no Pro package here"}
    monkeypatch.setattr(pp, "_loaded", {})
    (tmp_path / "pro" / "0.2.0" / "homeshed_pro").mkdir(parents=True)
    (tmp_path / "pro" / "0.2.0" / "homeshed_pro" / "__init__.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "pro" / "current.json").write_text(json.dumps({"version": "0.2.0", "sha256": "x"}))
    out = pp.load()  # a hand-made folder (to-do #19 t7): refused before anything in it is imported
    assert out["loaded"] is False and "release key" in out["reason"] and "homeshed_pro" not in sys.modules


def test_a_signed_package_that_fails_on_import_never_stops_the_free_tools(env, monkeypatch, tmp_path):
    env(Site(wheel(extra={"homeshed_pro/__init__.py": "raise RuntimeError('boom')\n"})))
    pp.update(have="0.2.0")
    out = pp.load()
    assert out["loaded"] is False and "boom" in out["reason"]


@pytest.mark.parametrize("site, match", [
    (lambda: Site(wheel(), signed=False), "isn't signed"),                         # t3: a fake or old site
    (lambda: Site(wheel(), key=Ed25519PrivateKey.generate()), "release key"),      # a fake site with its own key
    (lambda: Site(wheel(record=False)), "file list"),                              # no RECORD to sign
])
def test_unsigned_or_wrongly_signed_packages_install_nothing(env, tmp_path, site, match):
    env(site())
    with pytest.raises(pp.ProPluginError, match=match):
        pp.update(have="0.2.0")
    assert pp.installed() is None and not [p for p in (tmp_path / "pro").glob("*") if p.is_dir()]


@pytest.mark.parametrize("tamper, match", [
    (lambda f, cur: (f / "homeshed_pro" / "demo.py").write_text("def demo(): return 'free Pro'\n"), "changed"),
    (lambda f, cur: (f / "homeshed_pro" / "extra.py").write_text("x = 1\n"), "extra file"),
    (lambda f, cur: cur.update(record_sha256="0" * 64), "release key"),   # current.json edited to match a new list
    (lambda f, cur: cur.update(sig="AAAA"), "release key"),
    (lambda f, cur: cur.update(version="9.9.9"), "release key"),         # a signature moved to another version
])
def test_a_folder_changed_after_install_never_loads(env, tmp_path, tamper, match):
    env(Site(wheel()))
    pp.update(have="0.2.0")
    folder, cur = tmp_path / "pro" / "0.2.0", pp.installed()
    tamper(folder, cur)
    if cur["version"] != "0.2.0":
        (tmp_path / "pro" / "0.2.0").rename(tmp_path / "pro" / cur["version"])
    (tmp_path / "pro" / "current.json").write_text(json.dumps(cur))
    out = pp.load()
    assert out["loaded"] is False and match in out["reason"]
    assert registry.get_capability("protest.demo") is None


def test_no_release_key_shipped_means_no_package_loads(env, monkeypatch, tmp_path):
    env(Site(wheel()))
    pp.update(have="0.2.0")
    monkeypatch.setattr(pro_proof, "RELEASE_KEYS", {})
    assert pp.load()["loaded"] is False


def test_version_order_is_pep_440s():
    assert pp.version_key("0.2.0a1") < pp.version_key("0.2.0b1") < pp.version_key("0.2.0rc1") < pp.version_key("0.2.0")
    assert pp.version_key("0.10.0") > pp.version_key("0.9.9")
    with pytest.raises(pp.ProPluginError):
        pp.version_key("1.0; rm -rf")
