# Docs — Read

## ID
`docs.read` (risk `read`)

## Purpose
The text of a document, so the AI can summarise it, search it or answer questions about it (the owner,
2026-10-23: end-user tools that work). PDF, Word (`.docx`), Excel (`.xlsx`) and CSV. Local and free: nothing
leaves the machine. R&D's verdict (`researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md`): pypdf
(BSD-3), openpyxl (MIT, read-only), stdlib csv; Word read straight from the `.docx` zip with defusedxml. A heavier
all-in-one converter was turned down: large, and its URL fetching is an SSRF path.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `path` | string | — | The file, absolute or relative to the first allowed folder. `files.list` shows what's there. |
| `offset` | integer | `0` | Where to start in the text: the `next_offset` an earlier call gave. |

## Returns
`{path, kind, text, chars, offset, next_offset?}` plus, by kind: `pages` (and `no_text_layer: true` for a scanned
PDF, which needs OCR: not done here), `paragraphs`, `sheets: [{name, rows}]` or `rows`. Up to 60,000 characters a
call. PDF text is marked `[page N]`. Spreadsheets give one line per row, cells separated by tabs.

## Guards
- Only `.pdf`, `.docx`, `.xlsx`, `.csv`, and only inside the folders this server may read (`READ_ALLOWED_ROOTS`,
  `readroots.py`, the same rule as `files.list`). Never a hidden file or folder (a name starting with a dot), so a
  `.env` can't be read even inside an allowed folder.
- 25 MB a file; 300 pages; 5,000 rows a sheet and 50 columns; a `.docx`/`.xlsx` that unpacks past 60 MB is refused.
- XML is parsed with defusedxml (no entity tricks). A PDF is parsed in a child process with a 60-second limit, so a
  hostile file can't hang the server. A password-protected PDF says so.

## To read files from your own folders
Set `READ_ALLOWED_ROOTS` (`;` between folders on Windows, `:` elsewhere) and, in Docker, mount that folder into the
container.
