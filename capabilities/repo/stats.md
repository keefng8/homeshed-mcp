# repo.stats

How a public GitHub repository is doing. The Overview's Release progress card shows it for each project that names
its repo.

## What it reads
- **GitHub, no token needed:** stars, forks, watchers, open issues, and each release's asset downloads.
- **PyPI:** downloads from pypistats.org for the package named like the repo, only when its PyPI page links the repo.
  pypistats counts a day behind, so a new package shows "no figures yet" for its first day.
- **GitHub traffic, token needed:** views, clones and the top referrers. The token is read from the vault, the first
  one set of `GITHUB_STATS_TOKEN`, `GITHUB_TOKEN`, `GITHUB_SEARCH_TOKEN`. It needs push access to the repo: a classic
  token with `repo`, or a fine-grained one for that repo with Administration: read-only. Without one, the answer says
  how to add it (`traffic_note`).

GitHub keeps traffic for 14 days only. Each look keeps the per-day figures (`usage/repo_stats.json`, up to 400 days), so
`views_total` and `clones_total` count from the first look (`since`). Stars, forks and downloads are noted daily too,
for `stars_week`. Answers are cached 30 minutes.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `repo` | string | required | owner/name, as on GitHub. |

## Returns
`{repo, url, private, stars, forks, watchers, open_issues, stars_week, release_downloads, releases: [{tag, published,
downloads}], pypi: {package, version, url, day, week, month} | null, traffic: {views_14d, clones_14d, views_total,
clones_total, since, referrers: [{from, views, people}], daily: [{day, views, clones}]} | traffic_note, checked}`

## Errors
`StatsError`: a malformed name, or GitHub didn't answer for it (wrong name, or private without access).
