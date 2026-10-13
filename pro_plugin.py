"""HomeShed Pro's tools: a separate package (homeshed_pro), downloaded with the owner's Pro key and loaded at start.

The public HomeShed has only this hook. The Pro tools' code ships in the homeshed_pro wheel from the Pro website (Apache
code behind a check could simply be patched out, so it isn't in the public repo at all).

- `homeshed-mcp pro update` (update()): asks the Pro website for the latest wheel (with the Pro key, in a header, never
  in a URL), downloads it, checks its size and SHA-256 against what the site said, checks it's for this HomeShed's
  version, and unpacks it into DATA_DIR/usage/pro/<version>/. The previous version is kept for a rollback. Nothing is pip-
  installed: uvx environments are throwaway, so a pip install wouldn't last.
- At start (load(), from registry.discover()): the current version's folder goes on sys.path, its capability files join
  the manifest, and `import homeshed_pro` registers its tools. Whether a call may run is the package's own check (pro.py:
  the membership, with its grace). A Pro package that fails to load is logged and skipped: the free tools always start.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

from paths import data_path

PACKAGE = "homeshed_pro"
LATEST_PATH = "/pro-api/packages/homeshed-pro/latest"
MAX_WHEEL_BYTES = 50 * 2**20
MAX_UNPACKED_BYTES = 200 * 2**20
VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?:(a|b|rc)(\d{1,4}))?$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_log = logging.getLogger("homeshed")
_loaded: dict = {}


class ProPluginError(RuntimeError):
    """Safe to show: never a key."""


def pro_dir() -> Path:
    # under usage/, a Docker volume: a top-level folder would be lost when the container is recreated
    return Path(os.environ.get("HOMESHED_PRO_DIR") or data_path("usage/pro"))


def version_key(v: str) -> tuple:
    """PEP 440's order for the versions this package uses: 0.2.0a1 < 0.2.0b1 < 0.2.0rc1 < 0.2.0."""
    m = VERSION_RE.match(str(v or "").strip())
    if not m:
        raise ProPluginError(f"{v!r} isn't a version this HomeShed understands")
    major, minor, patch, stage, n = m.groups()
    return int(major), int(minor), int(patch), {"a": 0, "b": 1, "rc": 2, None: 3}[stage], int(n or 0)


def _base_version(v: str) -> tuple:
    """HomeShed's own version for comparisons: "dev" (a source checkout) counts as new enough."""
    try:
        return version_key(v)
    except ProPluginError:
        return (9999, 0, 0, 3, 0)


def installed() -> dict | None:
    """{"version", "dir", "sha256", "at", "previous"?} for the Pro package this install will load, or None."""
    try:
        cur = json.loads((pro_dir() / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cur, dict) or not VERSION_RE.match(str(cur.get("version") or "")):
        return None
    return cur


def load() -> dict:
    """Put the current Pro package on the path and import it (its tools register themselves). Never raises."""
    if _loaded:
        return dict(_loaded)
    cur = installed()
    if not cur:
        _loaded.update(loaded=False, reason="no Pro package here")
        return dict(_loaded)
    folder = pro_dir() / cur["version"]
    try:
        if not (folder / PACKAGE / "__init__.py").is_file():
            raise ProPluginError(f"the Pro package folder for {cur['version']} is incomplete")
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        import manifest
        caps = folder / PACKAGE / "capabilities"
        if caps.is_dir() and caps not in manifest.PLUGIN_DIRS:
            manifest.PLUGIN_DIRS.append(caps)
        import importlib
        importlib.import_module(PACKAGE)
        _loaded.update(loaded=True, version=cur["version"])
    except Exception as exc:  # noqa: BLE001 - a broken Pro package never stops the free tools from starting
        _log.error("HomeShed Pro %s didn't load: %s", cur.get("version"), exc)
        _loaded.update(loaded=False, version=cur.get("version"), reason=f"didn't load: {exc}"[:300])
    return dict(_loaded)


def _requires_ok(requirement: str | None, have: str) -> str | None:
    """Why this HomeShed is too old for the package (requirement like "homeshed-mcp>=0.2.0"), or None."""
    m = re.search(r">=\s*([0-9][0-9a-z.]*)", requirement or "")
    if not m:
        return None
    if _base_version(have) < version_key(m.group(1)):
        return (f"this Pro package needs HomeShed {m.group(1)} or newer and this is {have}: update HomeShed first "
                "(uvx homeshed-mcp@latest, or pull the new Docker image)")
    return None


def _check_members(z: zipfile.ZipFile) -> str | None:
    """The wheel's own Requires-Dist on homeshed-mcp, after checking every file in it is safe to unpack."""
    total, requirement = 0, None
    for info in z.infolist():
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name or "\\" in name:
            raise ProPluginError(f"the Pro package has an unsafe file name ({name[:80]}); nothing was installed")
        if not (name.startswith(f"{PACKAGE}/") or re.match(rf"^{PACKAGE}-[^/]+\.dist-info/", name)):
            raise ProPluginError(f"the Pro package has a file outside {PACKAGE}/ ({name[:80]}); nothing was installed")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ProPluginError("the Pro package unpacks to more than 200 MB, so it was refused")
        if name.endswith(".dist-info/METADATA"):
            for line in z.read(info).decode("utf-8", "replace").splitlines():
                if line.lower().startswith("requires-dist:") and "homeshed-mcp" in line.lower():
                    requirement = line.split(":", 1)[1].strip()
    if f"{PACKAGE}/__init__.py" not in z.namelist():
        raise ProPluginError("the download isn't the HomeShed Pro package; nothing was installed")
    return requirement


def update(have: str | None = None) -> dict:
    """Download, check and unpack the latest Pro package. Returns {"updated", "version", "previous"?, "note"}."""
    import httpx

    import mavis_pro
    import vault
    from app_version import version as app_version

    have = have or app_version()
    token = vault.secret(mavis_pro.TOKEN)
    if not token:
        raise ProPluginError("HomeShed Pro isn't connected here yet: run  homeshed-mcp pro connect  first.")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        r = httpx.get(mavis_pro.api_url() + LATEST_PATH, headers=headers, timeout=20, follow_redirects=False)
    except httpx.HTTPError:
        raise ProPluginError("Couldn't reach the Pro website. Try again in a minute.") from None
    if r.status_code == 401:
        raise ProPluginError("The Pro website doesn't know this install's key any more. Connect again with a new key.")
    if r.status_code == 403:
        raise ProPluginError("Your Pro membership isn't active, so the Pro tools can't be downloaded. Renew on the Pro "
                             "website.")
    if r.status_code == 404:
        raise ProPluginError("There's no Pro tools package to download yet.")
    if r.status_code != 200:
        raise ProPluginError(f"The Pro website answered HTTP {r.status_code}.")
    try:
        d = (r.json() or {}).get("data") or {}
        version, sha, size, url = str(d["version"]), str(d["sha256"]).lower(), int(d["size"]), str(d["url"])
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProPluginError("The Pro website's answer about the package wasn't readable.") from None
    version_key(version)
    if not SHA_RE.match(sha) or not 0 < size <= MAX_WHEEL_BYTES or not url.startswith(
            "/api/pro-api/packages/homeshed-pro/") or ".." in url:
        raise ProPluginError("The Pro website's answer about the package didn't look right, so nothing was downloaded.")
    if why := _requires_ok(d.get("requires_homeshed"), have):
        raise ProPluginError(why)
    cur = installed()
    if cur and cur.get("version") == version and cur.get("sha256") == sha:
        return {"updated": False, "version": version, "note": "The Pro tools are up to date."}
    root = pro_dir()
    root.mkdir(parents=True, exist_ok=True)
    wheel = root / f"{PACKAGE}-{version}.download"
    digest, got = hashlib.sha256(), 0
    try:
        with httpx.stream("GET", mavis_pro.base_url() + url, headers={"Authorization": f"Bearer {token}"},
                          timeout=120, follow_redirects=False) as resp:
            if resp.status_code != 200:
                raise ProPluginError(f"The download answered HTTP {resp.status_code}; nothing was installed.")
            told = (resp.headers.get("x-checksum-sha256") or "").lower()
            if told and told != sha:
                raise ProPluginError("The download's checksum doesn't match the Pro website's; nothing was installed.")
            with open(wheel, "wb") as f:
                for chunk in resp.iter_bytes():
                    got += len(chunk)
                    if got > size:
                        raise ProPluginError("The download is bigger than the Pro website said; nothing was installed.")
                    digest.update(chunk)
                    f.write(chunk)
        if got != size or digest.hexdigest() != sha:
            raise ProPluginError("The download didn't match its checksum, so nothing was installed. Try again.")
        part = root / f"{version}.part"
        shutil.rmtree(part, ignore_errors=True)
        with zipfile.ZipFile(wheel) as z:
            if why := _requires_ok(_check_members(z), have):
                raise ProPluginError(why)
            z.extractall(part)
        final = root / version
        shutil.rmtree(final, ignore_errors=True)
        os.replace(part, final)
    except httpx.HTTPError:
        raise ProPluginError("The download was interrupted; nothing was installed. Try again.") from None
    except zipfile.BadZipFile:
        raise ProPluginError("The download isn't a valid package; nothing was installed.") from None
    finally:
        wheel.unlink(missing_ok=True)
    previous = cur.get("version") if cur else None
    state = {"version": version, "sha256": sha, "at": time.time(), **({"previous": previous} if previous else {})}
    tmp = root / "current.json.tmp"
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, root / "current.json")
    for old in root.iterdir():  # keep this version and the one before it (a rollback), nothing older
        if old.is_dir() and old.name not in (version, previous) and VERSION_RE.match(old.name):
            shutil.rmtree(old, ignore_errors=True)
    return {"updated": True, "version": version, **({"previous": previous} if previous else {}),
            "note": "Installed. Restart HomeShed to load the Pro tools (your AI app restarts it, or restart the "
                    "container)."}
