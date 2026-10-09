# code.dead_scan

Tells you which Python files nothing uses, by following the imports from your real entry points, never by searching
for file names. It guards against three real incidents:

- **KB-0026:** unreachable code still costs (reviews, checks and AI context all read it).
- **KB-0029:** a safety checker aimed at a dead duplicate file, because a name search couldn't tell "imported" from
  "not".
- **KB-0030:** deleting a dead-looking module broke its whole package, because its `__init__.py` re-exported it.

## When to use it

Before deleting files, when a folder feels bloated, or before pointing a checker at a file (is it the live one?).

## Inputs

| Input | Meaning |
|---|---|
| `root` | The project folder. It must be inside the folders this server may read (`READ_ALLOWED_ROOTS`, else the project folder the server runs in). |
| `entries` | The files that start the program, relative to `root`, e.g. `["main.py", "server.py"]`. Add every one: a script started on its own, a test runner, a scheduled job. |
| `exclude` | Extra folder names to skip. `.venv`, `venv`, `node_modules`, `build`, `dist`, `__pycache__` and `.git` are always skipped. |
| `detail` | `compact` (the default): totals (`failed`, `counts` by id, `passed`), at most 20 failing rows and a few information rows, with unreached files grouped by folder (`folder`, `files`, three `examples`); `more` says what was left out. `full`: every row. |

In Docker the server can read only its own folder unless you mount project folders and list them in `READ_ALLOWED_ROOTS`. On your own PC (a stdio install) it reads the project it was opened in.

## Returns

`{root, unsure, checks, failed, summary}`. Each check: `{id, kb, ok, detail, file, line}`; `file` is relative to `root`.

| id | ok | Meaning |
|---|---|---|
| `dead-file` | false | no entry point reaches it |
| `unreached-file` | false | the same, while `unsure` is true: see `cant-follow` |
| `unused-reexport` | false | an `__init__.py` re-exports this name and nothing else in the reachable code uses it. It's reachable, but only because of that line, so check outside users before deleting its module (KB-0030) |
| `cant-follow` | true (info) | a call the walk can't follow: `importlib.import_module`, `__import__`, a subprocess running a `.py` file or `-m`, or packaging scripts/entry points. While any exist, `unsure` is true |
| `syntax-error` | true (info) | a file that doesn't parse: counted as reached, its imports not followed |

## Known limitations

- It only matches patterns for the calls it can't follow; it doesn't run them. A launch whose target is computed
  (not a plain string) is reported but not resolved.
- `from pkg import name`, where `name` is a submodule, is handled on a best-effort basis, not full import-system
  emulation.
- The re-export check doesn't search the `__init__.py` itself, so a name it uses again (in `__all__`, say) is still
  flagged. Read the finding; don't delete on its strength alone.
- Python only. KB-0026's original incident was a JavaScript/webpack site; that needs its own scanner.
- Up to 5,000 files, 2 MB each.

## Risks

Read-only: it parses files and never runs them. It reads only inside the allowed folders; symlinks pointing outside
are skipped.
