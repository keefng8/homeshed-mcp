"""Sets the env vars main.py needs before any test file imports it: main.py reads them at import, with no defaults.
Set when this file loads (before collection), so a test file may import main at its top. Same values as before.
"""
import os
import sys
from pathlib import Path

TEST_ENV = {
    "MCP_BASE_URL": "http://mcp-server:8765",
    "MCP_AUTH_TOKEN": "test-token",
    "MCP_HOST_HEADER": "localhost:8765",
    "MEMORY_CORE_BASE_URL": "http://memory-core:8420",
    "MEMORY_CORE_BEARER": "test-bearer",
    "MEMORY_SERVICE_ID": "default",
    "MEMORY_USER_KEY": "test-user-key",
    "MEMORY_TEAM_ID": "test-team",
    "MEMORY_USER_ID": "test-user",
    "MEMORY_AGENT_ID": "test-agent",
}
for _key, _value in TEST_ENV.items():
    os.environ.setdefault(_key, _value)
# DASHBOARD_PASSWORD deliberately left unset here: tests set or clear it themselves with monkeypatch, since
# main.DASHBOARD_PASSWORD is read once at import time.

# Every file a test writes must be in a temp folder: the run fails, naming it (scripts/test_write_guard.py, #16). The
# guard lives in the repository's scripts folder; a copy without that folder runs the tests without it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
try:
    import test_write_guard  # noqa: E402
except ImportError:
    pass
else:
    test_write_guard.install()
    pytest_sessionfinish = test_write_guard.sessionfinish
