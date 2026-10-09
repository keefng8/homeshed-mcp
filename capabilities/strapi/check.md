# strapi.check

Finds six Strapi bugs that fail silently (no error, just wrong results), in a project's source, and optionally checks
whether its live `/admin` answers the public. Each rule comes from a real, repeated bug, and names
its known-bug id (KB-...) for reference.

## When to use it

Before deploying a Strapi v5 project, after a v4 → v5 migration, or when a list comes back short or empty for no
reason.

## Inputs

| Input | Meaning |
|---|---|
| `repo` | The Strapi project folder. It must be inside the folders this server may read (`READ_ALLOWED_ROOTS`, else the project folder the server runs in). |
| `url` | Optional. The live site's base address, e.g. `https://api.example.com`, for the `/admin` check. Public hosts only, unless the owner allows private ranges with `NETWORK_ALLOWED_CIDRS`. |
| `detail` | `compact` (the default): totals (`failed`, `counts` by id, `passed`), at most 20 failing rows and a few information rows; `more` says what was left out. `full`: every row. |

In Docker the server can read only its own folder unless you mount project folders and list them in `READ_ALLOWED_ROOTS`. On your own PC (a stdio install) it reads the project it was opened in.

## Returns

`{repo, checks: [...], failed}`. Each check: `{id, kb, ok, detail, file, line}`, plus `skipped: true` when it couldn't
run (no schemas, no Dockerfile, no url). `file` is relative to `repo`.

| id | Known bug | What it finds |
|---|---|---|
| `missing-pagination` | KB-0019 | an `/api/` call with no `pagination` or `$limit`: Strapi returns 25 rows by default |
| `v4-attributes` | KB-0020 | `data.attributes.x` reads, the v4 shape; v5 records are flat |
| `filter-path` | KB-0021 | a filter on a field no schema declares, which returns no rows instead of an error |
| `node-env` | KB-0027 | `ENV NODE_ENV=development` in the Dockerfile's last (runtime) stage |
| `route-api-prefix` | KB-0042 | a custom route whose path starts with `/api`: Strapi adds `/api` itself, giving `/api/api/…` |
| `admin-exposed` | none yet | `<url>/admin` answers 2xx with nothing in front of it |

## Known limitations

- The source checks are regex scans, not a JavaScript parser: they miss query strings built at runtime and can be wrong
  on unusual code.
- `filter-path` checks only the first segment of a path (`author` in `author.user.name`).
- `admin-exposed` counts any 2xx as exposed and any 3xx, 401 or 403 as protected. A custom login page that itself
  answers 200 reads as exposed.
- It reads `.js` files only (no TypeScript yet), up to 5,000 files and 1 MB each, and skips `node_modules` and build
  folders.

## Risks

Read-only. It reads files only inside the allowed folders (symlinks pointing outside are skipped) and makes at most one
GET request, to `<url>/admin`, through the same guard as `network.port_check` (vetted address, no second DNS lookup,
no redirects followed).
