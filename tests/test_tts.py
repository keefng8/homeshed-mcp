"""tts.*: Google text to speech (Chirp 3 HD) with Google faked by an httpx.MockTransport. No network; every file under
tmp_path."""
import base64
from datetime import datetime, timezone

import httpx
import pytest

import clients
import runtime_settings as rs
from tools.tts import _count
from tools.tts import _google as g
from tools.tts.fetch import fetch
from tools.tts.synthesize import mp3_seconds, synthesize
from tools.tts.usage import usage
from tools.tts.voices import voices

# MPEG-2 layer III, 32 kbit/s, 24 kHz: 96-byte frames of 576 samples (0.024 s each); 250 frames = 6 s
FRAME = bytes([0xFF, 0xF3, 0x44, 0xC4]) + bytes(92)
MP3 = FRAME * 250
VOICE = "en-GB-Chirp3-HD-Charon"
VOICE_LIST = {"voices": [
    {"languageCodes": ["en-GB"], "name": "en-GB-Chirp3-HD-Charon", "ssmlGender": "MALE"},
    {"languageCodes": ["en-GB"], "name": "en-GB-Chirp3-HD-Kore", "ssmlGender": "FEMALE"},
    {"languageCodes": ["en-GB"], "name": "en-GB-Neural2-A", "ssmlGender": "FEMALE"},  # not Chirp 3 HD: left out
]}


class FakeGoogle:
    def __init__(self):
        self.calls = []
        self.status = 200
        self.token_calls = []

    def token(self, request):
        self.token_calls.append(request)
        return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope test-key-123"}})
        if request.url.host == g.TOKEN_HOST:
            return self.token(request)
        if request.url.path == "/v1/voices":
            return httpx.Response(200, json=VOICE_LIST)
        return httpx.Response(200, json={"audioContent": base64.b64encode(MP3).decode()})


@pytest.fixture
def google(tmp_path, monkeypatch):
    monkeypatch.setenv("TTS_OUTPUT_DIR", str(tmp_path / "tts"))
    monkeypatch.setenv("TTS_USAGE_FILE", str(tmp_path / "tts_month.json"))
    monkeypatch.setattr(g, "secret", lambda name: "test-key-123" if name == g.KEY_NAME else None)
    monkeypatch.setattr(_count, "paid_allowance", lambda: 0)
    monkeypatch.setattr(g, "_voices_cache", {})
    monkeypatch.setattr(g, "_token", {"value": "", "until": 0.0, "account": ""})
    fake = FakeGoogle()
    monkeypatch.setattr(g, "_TRANSPORT", httpx.MockTransport(fake))
    return fake


def as_app(name, fn, *args, **kw):
    token = clients.current_client.set(name)
    try:
        return fn(*args, **kw)
    finally:
        clients.current_client.reset(token)


def utc(*args) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_synthesize_saves_an_mp3_and_counts(google, tmp_path):
    got = synthesize("Hello there. It was a dark night.", VOICE, speaking_rate=0.9)
    assert got["seconds"] == 6.0 and got["seconds_from"] == "mp3 frames" and got["bytes"] == len(MP3)
    assert got["characters"] == 33 and got["month_characters"] == 33 and got["free_left"] == 1_000_000 - 33
    assert (tmp_path / "tts" / got["name"]).read_bytes() == MP3 and got["name"].startswith("tts-owner-")
    req = google.calls[0]
    assert req.headers["x-goog-api-key"] == "test-key-123" and "key=" not in str(req.url)
    body = httpx.Response(200, content=req.content).json()
    assert body["voice"] == {"languageCode": "en-GB", "name": VOICE}
    assert body["audioConfig"] == {"audioEncoding": "MP3", "speakingRate": 0.9}


def test_over_the_request_limit_is_refused_before_anything_is_sent(google):
    with pytest.raises(g.TTSError, match="at most 5,000 bytes"):
        synthesize("a" * 5001, VOICE)
    with pytest.raises(g.TTSError, match="bytes"):
        synthesize("é" * 2501, VOICE)  # 2,501 characters but 5,002 bytes
    synthesize("a" * 5000, VOICE)
    assert len(google.calls) == 1 and usage()["characters"] == 5000


def test_bad_voice_and_rate_are_refused(google):
    for bad in ("en-GB-Neural2-A", "Charon", "../x"):
        with pytest.raises(g.TTSError, match="Chirp 3 HD"):
            synthesize("Hi.", bad)
    for rate in (0.1, 2.5, True):
        with pytest.raises(g.TTSError, match="speaking_rate"):
            synthesize("Hi.", VOICE, speaking_rate=rate)
    assert google.calls == []


def test_missing_key_says_where_to_add_it(google, monkeypatch):
    monkeypatch.setattr(g, "secret", lambda name: None)
    for call in (lambda: synthesize("Hi.", VOICE), lambda: voices()):
        with pytest.raises(g.TTSError, match="GOOGLE_TTS_SERVICE_ACCOUNT .*under Settings > Credentials"):
            call()
    assert google.calls == [] and usage()["characters"] == 0


def test_free_only_cap_refuses_at_the_limit(google, monkeypatch):
    monkeypatch.setattr(_count, "FREE_CHARS", 10)
    synthesize("123456", VOICE)
    with pytest.raises(g.TTSError, match="TTS_CAP_REACHED.*4 left"):
        synthesize("12345", VOICE)
    synthesize("1234", VOICE)  # exactly to the limit is allowed
    with pytest.raises(g.TTSError, match="TTS_CAP_REACHED"):
        synthesize("1", VOICE)
    u = usage()
    assert u["characters"] == 10 and u["left"] == 0 and len(google.calls) == 2
    assert u["clients"]["owner"] == {"calls": 2, "characters": 10, "refused": 2}


def test_paid_allowance_lets_it_go_further(google, monkeypatch):
    monkeypatch.setattr(_count, "FREE_CHARS", 10)
    monkeypatch.setattr(_count, "paid_allowance", lambda: 5)
    synthesize("123456789012345", VOICE)
    u = usage()
    assert u["free_left"] == 0 and u["left"] == 0 and u["paid_characters"] == 5


def test_paid_allowance_setting_defaults_to_free_only(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(rs, "_cache", (None, {}))
    assert rs.SCHEMA[_count.SETTING]["default"] == "0" and _count.paid_allowance() == 0
    rs.update({_count.SETTING: "100000"})
    assert _count.paid_allowance() == 100000


def test_google_refusal_gives_the_characters_back(google):
    google.status = 403
    with pytest.raises(g.TTSError, match="HTTP 403") as e:
        synthesize("Hello.", VOICE)
    assert "test-key-123" not in str(e.value)
    assert usage()["characters"] == 0 and usage()["calls"] == 0


def test_month_is_pacific_and_resets_on_the_first(google):
    late_oct = utc(2026, 11, 1, 3, 0)   # 31 Oct, evening, in California: still October's allowance
    nov = utc(2026, 11, 1, 10, 0)       # 1 Nov in California
    assert _count.month(late_oct) == "2026-10" and _count.resets(late_oct) == "2026-11-01"
    assert _count.month(nov) == "2026-11" and _count.resets(utc(2026, 12, 15)) == "2027-01-01"
    _count.reserve("station", 900, now=late_oct)
    assert _count.summary(late_oct)["characters"] == 900
    assert _count.summary(nov)["characters"] == 0 and _count.summary(nov)["free_left"] == 1_000_000


def test_cap_is_per_month(google, monkeypatch):
    monkeypatch.setattr(_count, "FREE_CHARS", 10)
    _count.reserve("station", 10, now=utc(2026, 10, 15))
    with pytest.raises(g.TTSError, match="2026-11-01"):
        _count.reserve("station", 1, now=utc(2026, 10, 20))
    assert _count.reserve("station", 10, now=utc(2026, 11, 15)) == "2026-11"


def test_host_is_fixed_and_redirects_refused(google, monkeypatch):
    synthesize("Hi.", VOICE)
    assert {r.url.host for r in google.calls} == {"texttospeech.googleapis.com"}
    assert all(r.url.scheme == "https" for r in google.calls)
    monkeypatch.setattr(g, "_TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"})))
    with pytest.raises(g.TTSError, match="redirect"):
        synthesize("Hi.", VOICE)


def test_voices_are_chirp_only_and_cached_for_a_day(google):
    got = voices("en-GB")
    assert got["count"] == 2 and got["voices"][0] == {"name": VOICE, "gender": "male", "language": "en-GB"}
    voices("en-GB")
    assert len(google.calls) == 1 and google.calls[0].url.params["languageCode"] == "en-GB"
    when, cached = g._voices_cache["en-gb"]
    g._voices_cache["en-gb"] = (when - g.VOICES_TTL_S - 1, cached)  # a day later
    voices("en-GB")
    assert len(google.calls) == 2
    with pytest.raises(g.TTSError, match="language"):
        voices("en GB; drop")


def test_fetch_returns_the_bytes(google):
    name = synthesize("Hello.", VOICE)["name"]
    got = fetch(name)
    assert base64.b64decode(got["audio_b64"]) == MP3 and got["bytes"] == len(MP3) and got["audio_mime"] == "audio/mpeg"
    with pytest.raises(g.TTSError, match="max_mb"):
        fetch(name, max_mb=0.001)
    for bad in ("../secret.mp3", "tts-owner-x.mp3", ""):
        with pytest.raises(g.TTSError, match="name"):
            fetch(bad)


def test_apps_are_counted_and_see_only_their_own_files(google):
    a = as_app("station", synthesize, "One.", VOICE)["name"]
    as_app("station", synthesize, "Two two.", VOICE)
    b = as_app("station-2", synthesize, "Three.", VOICE)["name"]
    u = usage()
    assert u["clients"]["station"] == {"calls": 2, "characters": 12, "refused": 0}
    assert u["clients"]["station-2"]["calls"] == 1 and u["calls"] == 3
    assert as_app("station", fetch, a)["name"] == a
    with pytest.raises(g.TTSError, match="another app"):
        as_app("station", fetch, b)  # "station-2"'s file, though its name starts the same way
    assert fetch(b)["name"] == b     # the owner gets any


def test_mp3_seconds_reads_frames_and_skips_an_id3_tag():
    tag = b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"xxxxx"
    assert mp3_seconds(tag + MP3) == 6.0
    assert mp3_seconds(b"not audio at all") is None


# --- service-account sign-in: Google refuses an API key for text to speech (the first real call, 28 Oct) -------------

def _account(**over):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    pkey = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = pkey.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    acct = {"type": "service_account", "client_email": "tts@example-project.iam.gserviceaccount.com",
            "private_key": pem, "token_uri": "https://oauth2.googleapis.com/token", **over}
    return acct, pkey.public_key()


def _with_account(monkeypatch, acct):
    import json as _j
    monkeypatch.setattr(g, "secret", lambda name: _j.dumps(acct) if name == g.ACCOUNT_NAME else None)


def test_a_service_account_signs_in_with_a_signed_jwt_and_sends_a_bearer_token(google, monkeypatch):
    import json as _j
    from urllib.parse import parse_qs
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    acct, public = _account()
    _with_account(monkeypatch, acct)
    voices()
    [tok] = google.token_calls
    form = parse_qs(tok.content.decode())
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    head, claims, sig = form["assertion"][0].split(".")
    pad = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    public.verify(pad(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())  # raises if wrong
    c = _j.loads(pad(claims))
    assert c["iss"] == acct["client_email"] and c["aud"] == g.TOKEN_URL and c["exp"] - c["iat"] == 3600
    api = [r for r in google.calls if r.url.host == g.HOST][0]
    assert api.headers["authorization"] == "Bearer ya29.test-token" and "x-goog-api-key" not in api.headers


def test_the_access_token_is_reused_until_it_nearly_runs_out(google, monkeypatch):
    acct, _ = _account()
    _with_account(monkeypatch, acct)
    synthesize("One.", VOICE)
    synthesize("Two.", VOICE)
    assert len(google.token_calls) == 1
    g._token["until"] = __import__("time").time() + 30  # inside the last minute: a fresh one
    synthesize("Three.", VOICE)
    assert len(google.token_calls) == 2


def test_a_bad_or_foreign_key_file_is_refused_before_any_call(google, monkeypatch):
    acct, _ = _account(token_uri="https://evil.example/token")
    _with_account(monkeypatch, acct)
    with pytest.raises(g.TTSError, match="token address other than Google"):
        voices()
    monkeypatch.setattr(g, "secret", lambda name: "not json" if name == g.ACCOUNT_NAME else None)
    with pytest.raises(g.TTSError, match="isn't a JSON key file"):
        voices()
    acct, _ = _account(type="authorized_user")
    _with_account(monkeypatch, acct)
    with pytest.raises(g.TTSError, match="isn't a service account"):
        voices()
    assert google.calls == []


def test_a_refused_access_token_is_not_reused(google, monkeypatch):
    acct, _ = _account()
    _with_account(monkeypatch, acct)
    voices()
    google.status = 401
    with pytest.raises(g.TTSError, match="HTTP 401"):
        synthesize("Hi.", VOICE)
    assert g._token["value"] == "" and "ya29" not in str(google.calls[-1].url)
