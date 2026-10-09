# Security policy

HomeShed can run commands on your machines, so we take security reports seriously and thank everyone who
reports responsibly.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately through GitHub:
[Security → Report a vulnerability](https://github.com/keefng8/homeshed-mcp/security/advisories/new).

Include what you found, how to reproduce it, the version, and the impact you think it has. Remove any real
tokens or hostnames.
We aim to acknowledge a report within **48 hours**, keep you updated until it's fixed, and agree the disclosure date
with you. We'll credit you in the advisory unless you'd rather stay anonymous.

## Supported versions

Security fixes go into the latest release. Please update before reporting, and check the
[changelog](CHANGELOG.md) for **Security** entries.

## What's in scope

- Bypassing authentication, per-client grants or rate limits
- A write tool running while it's switched off, including through `workflow.run`
- Reaching private or metadata addresses through `web.read` or `network.*` (SSRF)
- Leaking tokens, vault contents or request text into logs or responses
- Host or Origin header checks that fail to stop DNS rebinding

Out of scope: problems that need an attacker to already hold the owner token, and anything in third-party
backends you run yourself (report those to their projects; see [THIRD_PARTY.md](THIRD_PARTY.md)).

## Running it safely

The defaults are built to be safe: every request needs a token (except `/healthz`, which only says the server is up), the server listens on `127.0.0.1`, and tools
that act on your machines start switched off. If you change them:

- **Never expose it to the internet** without an access proxy (for example Cloudflare Access) in front of it.
- **Mounting the Docker socket gives root on the host.** Only uncomment the socket (and its `group_add`) lines in
  `docker-compose.yml` on a machine where that's acceptable.
- Give each app its own client token with only the tools it needs, and rotate tokens you no longer use.
- Code-execution tools (`dev.*`) run real code. Turn them on only in a disposable environment.
