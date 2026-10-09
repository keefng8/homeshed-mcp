# The Control Panel, HomeShed's optional web add-on. Release gate D11 (2026-10-01): a base image pinned by digest (the
# tool server's), an ordinary user, a .dockerignore that keeps .env out, and a HEALTHCHECK that needs no password.
# Only public files are copied, and everything it needs is inside this folder, so it builds from here alone. Private
# add-ons are mounted by their owner's own docker-compose.yml.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --root-user-action=ignore -r requirements.txt \
 && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:app /data
COPY main.py ./
COPY static ./static
COPY guides ./guides
USER app
EXPOSE 8080

# /healthz answers without a login and says only that the panel is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=3).status == 200 else 1)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
