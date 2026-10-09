# repo.readiness

Tells you how ready a project folder is to publish on GitHub, and exactly what to add for each gap. The list is the
layout HomeShed itself shipped with (2026-09-30), kept as data in `tools/repo/checklist.json`: README, licence,
security policy, contributing guide, code of conduct, changelog, CI with read-only permissions, Dependabot, issue and
pull request templates, code scanning, CODEOWNERS, and the files that only matter for some projects.

## When to use it

Before making a repository public, before a first release, or to see what a new project still lacks. It reads only a
handful of small files and changes nothing.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `"."` | The project's root folder. It must be inside the folders this server may read (see Configuration). |
| `detail` | string | `"compact"` | `"compact"`: the score and the gaps (at most 20). `"full"`: passing checks too. |

## What it checks
- **Required:** `README.md` (and no leftover `TODO`, `DRAFT` or `{{placeholders}}`), `LICENSE`, and a `.gitignore`
  that lists `.env`.
- **Recommended:** `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `.editorconfig`,
  `.gitattributes`, `docs/getting-started.md`, `.github/workflows/ci.yml` (with `permissions:`), `.github/dependabot.yml`,
  issue forms, a pull request template, `codeql.yml` and `CODEOWNERS`.
- **Only when they apply** (detected): `pyproject.toml` for a Python project; `NOTICE` under the Apache licence;
  `THIRD_PARTY.md` when there are dependencies; `.env.example` and `docs/configuration.md` when the code reads
  environment variables; `.dockerignore` listing `.env` and `.env.*` when there's a Dockerfile; a release workflow
  and `docs/RELEASING.md` when the project publishes a package or image.
- **Inside files:** a Dockerfile that runs as a non-root user, pins its base image by digest and has a health check;
  compose files that publish ports on `127.0.0.1` only.

## Returns
`{folder, score, applies, checks: [{id, level, ok, file, detail}], failed, counts, passed, more?}`. `score` is
"N of M" over the checks that apply; `applies` names what was detected (`python`, `apache`, `docker`, `settings`,
`publishes`, `deps`); each failing check's `detail` says what to add or change.

## Errors
Raises `ReadinessError` if the folder doesn't exist, isn't a folder, or is outside the allowed folders, or if
`detail` isn't `"compact"` or `"full"`.

## Configuration
`READ_ALLOWED_ROOTS` (else `CLAUDE_PROJECT_DIR`, else the server's working folder) decides which folders it may read,
the same rule as the code scanners.

## Implementation
`tools/repo/readiness.py`; the checklist is `tools/repo/checklist.json`, the Github prepper's machine copy of
their structure guide, kept in step with it by their tests.

## Machine-readable definition
`readiness.json`

## Related
`mcp.install_links` (install buttons for the README), `secrets.transcript_scan`.
