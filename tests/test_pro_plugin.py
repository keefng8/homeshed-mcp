"""pro_plugin.py: `pro update` downloads the Pro tools package with the Pro key (in a header), checks its size and
SHA-256, refuses an unsafe or wrong package and one this HomeShed is too old for, unpacks it next to the previous one,
and load() registers its tools and capability files without ever stopping the free tools. The Pro website is faked;
files go under tmp_path."""
import hashlib
import io
import json
import sys
import zipfile
from contextlib import contextmanager

import httpx
import pytest

import manifest
import pro_plugin as pp
import registry

KEY = "mav_" + "k" * 30  # secret-scan: allow (a dummy)


def wheel(version="0.2.0", requires=">=0.2.0", extra=None, tool_name="demo") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("homeshed_pro/__init__.py", "from homeshed_pro import demo  # noqa: F401\n")
        z.writestr("homeshed_pro/demo.py", "from registry import tool\n\n"
                   f"@tool(name='{tool_name}', category='protest', doc='protest/{tool_name}.md')\n"
                   f"def {tool_name}() -> dict:\n    return {{'ok': True}}\n")
        z.writestr(f"homeshed_pro/capabilities/protest/{tool_name}.json", json.dumps(
            {"id": f"protest.{tool_name}", "name": "Demo", "version": "1.0.0", "description": "d",
             "category": "protest", "risk": "read", "transport": "mcp"}))
        z.writestr(f"homeshed_pro-{version}.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: homeshed-pro\nVersion: {version}\n"
                   + (f"Requires-Dist: homeshed-mcp{requires}\n" if requires else ""))
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


class Site:
    def __init__(self, data: bytes, version="0.2.0", status=200, checksum=None, requires=None):
        self.data, self.version, self.status = data, version, status
        self.sha = checksum or hashlib.sha256(data).hexdigest()
        self.requires, self.seen = requires, []

    def get(self, url, headers=None, **kw):
        self.seen.append((url, headers))
        body = {"data": {"name": "homeshed-pro", "version": self.version, "filename": "x.whl", "size": len(self.data),
                         "sha256": self.sha, "url": f"/api/pro-api/packages/homeshed-pro/{self.version}",
                         **({"requires_homeshed": self.requires} if self.requires else {})}}
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
    out = pp.load()
    assert out["loaded"] is False and "boom" in out["reason"]


def test_version_order_is_pep_440s():
    assert pp.version_key("0.2.0a1") < pp.version_key("0.2.0b1") < pp.version_key("0.2.0rc1") < pp.version_key("0.2.0")
    assert pp.version_key("0.10.0") > pp.version_key("0.9.9")
    with pytest.raises(pp.ProPluginError):
        pp.version_key("1.0; rm -rf")
