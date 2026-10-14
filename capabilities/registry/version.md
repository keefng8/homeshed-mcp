# Package version

## ID
`registry.version` (read-only)

## Purpose
Say whether a release really went out: a package's latest version and when it was uploaded, from PyPI, npm or a
GitHub repository's releases, in a few hundred characters. Built 2026-10-14 (R&D C35): three releases had been checked
by hand, and `web.read` returned 77,000 characters for one PyPI version. The first step toward a release check (the
GitHub prepper owns the checklist).

## Inputs
| Input | |
|---|---|
| `ecosystem` | `pypi`, `npm` or `github` |
| `name` | the package name, or `owner/repo` for GitHub |

## Returns
`{ecosystem, name, latest, released (unix seconds), released_utc, url, previous: [{version, released_utc, yanked?}]}`,
newest first, at most 3 earlier versions.
- PyPI: `latest` is `info.version`; its date is that version's own upload time. Earlier versions are sorted by upload
  time, not as text (text puts 0.10 before 0.9); a version whose files were all yanked says `yanked`.
- npm: `latest` is `dist-tags.latest`; dates come from `time`.
- GitHub: the newest release that isn't a draft or a prerelease. None: `latest` null and `"note": "no releases"`.
- A missing package: `"note": "not found"`.

## Safety
Read-only, no credentials. Host-locked to `pypi.org`, `registry.npmjs.org` and `api.github.com`; names are checked
against each registry's own pattern before any request. User-Agent `HomeShed-registry-version/1.0` (GitHub refuses
requests without one). 10 s timeout, 8 MB cap. GitHub allows 60 unauthenticated requests an hour per address: past that
the tool says "rate-limited" instead of failing obscurely.
