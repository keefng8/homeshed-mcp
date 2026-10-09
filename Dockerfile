# Pinned by digest, so every build starts from the same base (2026-09-30); Dependabot's docker ecosystem keeps it current
# in the public repo. The tag stays for readers.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

# git installed here -- real gap found live 2026-09-24: git.status/.diff (deployed since
# 2026-09-20, previously believed "verified live") and the new git.commit/.branch/.clone/.pull/
# .push were ALL actually failing in the real production container with "git is not installed or
# not on PATH" -- python:3.12-slim's base image has no git. Earlier "verified live" testing must
# have exercised a local dev venv (which does have system git) rather than the actual deployed
# container's own PATH; not something this session broke, a pre-existing gap this session's
# git.commit work happened to surface. --no-install-recommends keeps the image from also pulling
# in git's large recommended-package set (email tools, etc.) that this project has no use for.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
# graphifyy (graphify.query's dependency, added 2026-09-22) pulls in ~20 tree-sitter grammar
# packages -- these ship manylinux wheels for the common case, so this should stay a pure `pip
# install`, no compiler needed. Not verified by an actual image build from this session (only
# verified in a local venv) -- if this build step ever needs a compiler unexpectedly, that's the
# first thing to check, not assume broken.
# Dependencies only, straight from pyproject.toml (release v1, 2026-09-29): the source is copied below and runs in
# place, and a package build here would need that source, which would lose this cached layer on every code change.
# The timezones extra: the slim base has no zone files.
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; print(chr(10).join(p['dependencies'] + p['optional-dependencies']['timezones']))" > /tmp/requirements.txt \
 && pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Runs as an ordinary user, not root (2026-09-30; R&D's plan, researchandimprovements/tool-server-packaging-review.md).
# /data is made here, owned by that user, so Docker seeds new named volumes with the right owner. /app stays root's,
# so the server can't rewrite its own code. safe.directory: git refuses repositories another user owns.
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --create-home app \
 && mkdir -p /data/usage /data/vault /data/observations && chown -R app:app /data \
 && git config --system --add safe.directory '*'

COPY . .

ENV MCP_PORT=8765
# Outside Docker the server listens on 127.0.0.1 only; inside the container it must accept the published port, which
# docker-compose.yml binds to the host's 127.0.0.1 (tests/test_serve_http.py checks this line is here).
ENV MCP_BIND=0.0.0.0
# Every state file under one folder (paths.py); the volumes in docker-compose.yml mount into it.
ENV DATA_DIR=/data
LABEL io.modelcontextprotocol.server.name="io.github.keefng8/homeshed-mcp"
EXPOSE 8765

# Runs inside the container against its own localhost:8765 -- MCP_ALLOWED_HOSTS on every real
# deployment already includes "localhost:8765" for exactly this (see .env.example). No new
# exposure: same bearer auth as any other caller, nothing reachable from outside the container.
# Directly closes the "no health check" gap Kuma's own docker-type monitor was flagging in its
# status messages for this container (uptime.status/status.md, 2026-09-23).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "\
import os, urllib.request, sys; \
req = urllib.request.Request('http://localhost:8765/capabilities', headers={'Host': 'localhost:8765', 'Authorization': 'Bearer ' + os.environ['MCP_AUTH_TOKEN']}); \
sys.exit(0 if urllib.request.urlopen(req, timeout=3).status == 200 else 1)" || exit 1

USER app
# `homeshed-mcp serve --http`, the same as `python server.py`; `docker compose run --rm <service> doctor` works too.
ENTRYPOINT ["python", "cli.py"]
CMD ["serve", "--http"]
