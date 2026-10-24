"""docs.read: the text of a PDF, Word (.docx), Excel (.xlsx) or CSV file. See ../../capabilities/docs/read.md.

R&D's verdict (researchandimprovements/homeshed-six-tools-verdicts-2026-10-24.md): build thin on pypdf (BSD-3),
openpyxl (MIT, read-only mode) and the stdlib csv; an all-in-one converter was too heavy and fetches URLs (an SSRF
path). Word is read straight from the .docx zip with defusedxml (no lxml).
Guards: only these four kinds, only inside the folders this server may read (readroots.py), never a dot-file or a
dot-folder; size, page, row and unzipped-size caps; a PDF is parsed in a child process with a time limit, so a
hostile file can't hang the server; a scanned PDF says it has no text layer instead of returning nothing.
"""
from __future__ import annotations

import csv
import io
import multiprocessing
import zipfile
from pathlib import Path

import readroots
from registry import tool

KINDS = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".csv": "csv"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UNZIPPED = 60 * 1024 * 1024  # a .docx or .xlsx whose parts unzip past this is refused (a zip bomb)
MAX_PAGES = 300
MAX_ROWS = 5_000      # per sheet / per CSV
MAX_COLS = 50
MAX_CHARS = 60_000    # one answer; offset reads on
PDF_TIMEOUT_S = 60
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DocsReadError(ValueError):
    """Safe to show."""


def _resolve(path: str) -> tuple[Path, str]:
    try:
        real = readroots.resolve_file(path, tuple(KINDS), MAX_FILE_BYTES)
    except readroots.OutsideRoots as exc:
        raise DocsReadError(str(exc)) from None
    return real, KINDS[real.suffix.lower()]


def _zip_guard(data: bytes) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DocsReadError("the file is damaged or isn't really that kind of file") from None
    if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
        raise DocsReadError("the file unpacks to more than 60 MB; refused")
    return z


def _docx(data: bytes) -> tuple[str, dict]:
    from defusedxml.ElementTree import fromstring
    z = _zip_guard(data)
    from defusedxml import DefusedXmlException
    try:
        root = fromstring(z.read("word/document.xml"))
    except KeyError:
        raise DocsReadError("no document body: is this really a .docx?") from None
    except DefusedXmlException:
        raise DocsReadError("the document uses XML entities, which aren't read (a known attack)") from None
    paras = []
    for p in root.iter(f"{W}p"):
        bits = []
        for el in p.iter():
            if el.tag == f"{W}t" and el.text:
                bits.append(el.text)
            elif el.tag in (f"{W}tab",):
                bits.append("\t")
            elif el.tag in (f"{W}br", f"{W}cr"):
                bits.append("\n")
        paras.append("".join(bits))
    text = "\n".join(paras).strip()
    return text, {"paragraphs": sum(1 for x in paras if x.strip())}


def _xlsx(data: bytes) -> tuple[str, dict]:
    import openpyxl
    _zip_guard(data)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many kinds on a bad file
        raise DocsReadError(f"couldn't open the workbook ({type(exc).__name__})") from None
    out, sheets, cut = [], [], False
    for ws in wb.worksheets:
        rows = 0
        out.append(f"## {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if rows >= MAX_ROWS:
                cut = True
                break
            cells = ["" if v is None else str(v) for v in row[:MAX_COLS]]
            if any(cells):
                out.append("\t".join(cells).rstrip("\t"))
                rows += 1
        sheets.append({"name": ws.title, "rows": rows})
    wb.close()
    return "\n".join(out).strip(), {"sheets": sheets, **({"rows_cut_at": MAX_ROWS} if cut else {})}


def _csv(data: bytes) -> tuple[str, dict]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows, out = 0, []
    for row in csv.reader(io.StringIO(text), dialect):
        if rows >= MAX_ROWS:
            break
        out.append("\t".join(row[:MAX_COLS]))
        rows += 1
    return "\n".join(out), {"rows": rows, **({"rows_cut_at": MAX_ROWS} if rows >= MAX_ROWS else {})}


def _pdf_worker(data: bytes, q) -> None:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    q.put(("error", "the PDF is password-protected"))
                    return
            except Exception:  # noqa: BLE001
                q.put(("error", "the PDF is password-protected"))
                return
        n = len(reader.pages)
        pages = [(reader.pages[i].extract_text() or "") for i in range(min(n, MAX_PAGES))]
        q.put(("ok", pages, n))
    except Exception as exc:  # noqa: BLE001 - reported as a short reason
        q.put(("error", f"couldn't read the PDF ({type(exc).__name__})"))


def _pdf(data: bytes) -> tuple[str, dict]:
    ctx = multiprocessing.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=_pdf_worker, args=(data, q), daemon=True)
    proc.start()
    try:
        got = q.get(timeout=PDF_TIMEOUT_S)
    except Exception:  # noqa: BLE001 - queue.Empty: it took too long
        got = ("error", f"the PDF took over {PDF_TIMEOUT_S} seconds to read; refused")
    finally:
        proc.join(timeout=2)
        if proc.is_alive():
            proc.kill()
    if got[0] == "error":
        raise DocsReadError(got[1])
    pages, total = got[1], got[2]
    text = "\n\n".join(f"[page {i + 1}]\n{t.strip()}" for i, t in enumerate(pages) if t.strip())
    meta = {"pages": total, **({"pages_cut_at": MAX_PAGES} if total > MAX_PAGES else {})}
    if not text.strip():
        meta["no_text_layer"] = True  # a scan or a picture of text: it needs OCR, which this doesn't do
    return text, meta


READERS = {"pdf": _pdf, "docx": _docx, "xlsx": _xlsx, "csv": _csv}


@tool(name="read", category="docs", doc="docs/read.md")
def read(path: str, offset: int = 0) -> dict:
    """The text of a document on this server: a PDF, a Word file (.docx), an Excel workbook (.xlsx) or a CSV, so the
    AI can summarise, search or answer questions about it. Runs locally, free; nothing leaves the machine. Only files
    inside the folders this server may read (READ_ALLOWED_ROOTS), never hidden files.

    Args:
        path: the file, absolute or relative to the first allowed folder (files.list shows what's there).
        offset: where to start in the text, for a long document: pass the "next_offset" a previous call gave.

    Returns:
        {"path", "kind": "pdf"|"docx"|"xlsx"|"csv", "text", "chars" (the whole text), "offset", "next_offset"
         (only when more is left), plus per kind: "pages" (and "no_text_layer": true for a scan, which needs OCR),
         "paragraphs", "sheets": [{name, rows}], or "rows"}. Up to 60,000 characters per call; spreadsheets give one
         line per row, cells separated by tabs, at most 5,000 rows a sheet and 50 columns.
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise DocsReadError("offset must be a whole number, 0 or more")
    real, kind = _resolve(path)
    text, meta = READERS[kind](real.read_bytes())
    part = text[offset:offset + MAX_CHARS]
    out = {"path": str(real), "kind": kind, "text": part, "chars": len(text), "offset": offset, **meta}
    if offset + MAX_CHARS < len(text):
        out["next_offset"] = offset + MAX_CHARS
    return out
