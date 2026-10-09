"""network.exposure_check: verdicts per path, catch-all sites, secret-file content, public hosts only, limits. The
network is faked: _public_address and _get are replaced."""
import pytest

import tools.network.exposure_check as ec


def fake_site(monkeypatch, pages: dict, catch_all=False, address="203.0.113.7"):
    """pages: path -> (status, location, body). Anything else: 200 for a catch-all site, else 404."""
    asked = []

    def get(host, port, addr, path):
        asked.append((host, port, addr, path))
        if path in pages:
            return pages[path]
        return (200, "", "<html>app</html>") if catch_all else (404, "", "")
    monkeypatch.setattr(ec, "_public_address", lambda host: address)
    monkeypatch.setattr(ec, "_get", get)
    return asked


def test_open_protected_and_missing_paths_are_told_apart(monkeypatch):
    fake_site(monkeypatch, {"/admin": (200, "", "<html>Admin</html>"), "/.env": (200, "", "DB_PASSWORD=hunter2\n"),
                            "/wp-admin/": (302, "https://x.cloudflareaccess.com/cdn-cgi/access/login", ""),
                            "/phpmyadmin/": (403, "", ""), "/server-status": (302, "/login?next=/", "")})
    out = ec.exposure_check(["example.com"])
    v = {f["path"]: f["verdict"] for f in out["hosts"][0]["findings"]}
    assert v == {"/admin": "OPEN: answers without signing in", "/.env": "OPEN: a secret file is readable",
                 "/wp-admin/": "protected (Cloudflare Access)", "/phpmyadmin/": "protected",
                 "/server-status": "protected (redirects to a sign-in page)"}
    assert out["open"] == 2 and out["hosts"][0]["checked"] == len(ec.DEFAULT_PATHS)


def test_a_catch_all_site_is_not_reported_open(monkeypatch):
    fake_site(monkeypatch, {}, catch_all=True)
    out = ec.exposure_check(["spa.example.com"], paths=["/admin", "/.env"])
    v = {f["path"]: f["verdict"] for f in out["hosts"][0]["findings"]}
    assert v == {"/admin": "answers every path (catch-all page)", "/.env": "answers, but not with that file"}
    assert out["open"] == 0 and out["hosts"][0]["catch_all"] is True


def test_requests_go_to_the_checked_address_with_the_port(monkeypatch):
    asked = fake_site(monkeypatch, {"/": (200, "", "Proxmox")}, address="198.51.100.9")
    out = ec.exposure_check(["pve.example.com:8006"], paths=["/"])
    assert out["hosts"][0]["host"] == "pve.example.com:8006" and out["open"] == 1
    assert all(a[:3] == ("pve.example.com", 8006, "198.51.100.9") for a in asked)
    assert asked[0][3].startswith("/exposure-check-")  # the catch-all probe comes first


def test_private_or_unknown_hosts_are_skipped_not_fatal(monkeypatch):
    def refuse(host):
        raise ValueError("it resolves to a private address; this checks what the internet sees")
    monkeypatch.setattr(ec, "_public_address", refuse)
    out = ec.exposure_check(["nas.example.com"])
    assert out["hosts"] == [{"host": "nas.example.com", "skipped": "it resolves to a private address; this checks "
                                                                   "what the internet sees"}] and out["open"] == 0


def test_the_real_address_check_refuses_private_addresses():
    with pytest.raises(ValueError, match="private"):
        ec._public_address("127.0.0.1")


@pytest.mark.parametrize("hosts, paths, words", [
    ([], None, "1 to 10"), (["a.example.com"] * 11, None, "1 to 10"), (["example.com"], ["admin"], "starting with /"),
    (["example.com"], ["/x"] * 13, "up to 12"), (["http://example.com"], None, "plain hostname"),
    (["user@example.com"], None, "plain hostname"), (["exa mple.com"], None, "hostname"),
    (["example.com/admin"], None, "plain hostname")])
def test_bad_input_is_refused_before_any_request(monkeypatch, hosts, paths, words):
    asked = fake_site(monkeypatch, {})
    with pytest.raises(ec.ExposureError, match=words):
        ec.exposure_check(hosts, paths)
    assert asked == []
