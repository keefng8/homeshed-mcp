"""docs.read on real small files made in a temp folder: PDF (with and without a text layer), DOCX, XLSX, CSV, and the
guards (allowed folders, hidden files, kinds, sizes, zip bombs, paging)."""
from __future__ import annotations

import io
import zipfile

import pytest

from tools.docs import read as dr


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("READ_ALLOWED_ROOTS", str(tmp_path))
    return tmp_path


def text_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF with real text (Helvetica), built by hand with a correct xref table."""
    body = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({s}) Tj 0 -16 Td" for s in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def docx(paragraphs: list[str]) -> bytes:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_a_pdf_with_text(root):
    (root / "letter.pdf").write_bytes(text_pdf(["Dear Sam", "Your order shipped"]))
    out = dr.read("letter.pdf")
    assert out["kind"] == "pdf" and out["pages"] == 1 and "[page 1]" in out["text"]
    assert "Dear Sam" in out["text"] and "order shipped" in out["text"] and "no_text_layer" not in out


def test_a_scanned_pdf_says_it_has_no_text_layer(root):
    import pypdf
    w = pypdf.PdfWriter()
    w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    (root / "scan.pdf").write_bytes(buf.getvalue())
    out = dr.read(str(root / "scan.pdf"))
    assert out["no_text_layer"] is True and out["text"] == ""


def test_a_broken_pdf_is_a_short_reason(root):
    (root / "bad.pdf").write_bytes(b"%PDF-1.4 nonsense")
    with pytest.raises(dr.DocsReadError, match="couldn't read the PDF"):
        dr.read("bad.pdf")


def test_word(root):
    (root / "notes.docx").write_bytes(docx(["First line", "Second &amp; last"]))
    out = dr.read("notes.docx")
    assert out["text"] == "First line\nSecond & last" and out["paragraphs"] == 2


def test_excel(root):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Design", "Views"])
    ws.append(["D1 French Bulldog", 11])
    wb.create_sheet("Empty")
    wb.save(root / "sales.xlsx")
    out = dr.read("sales.xlsx")
    assert out["text"].startswith("## Sales\nDesign\tViews\nD1 French Bulldog\t11")
    assert out["sheets"] == [{"name": "Sales", "rows": 2}, {"name": "Empty", "rows": 0}]


def test_csv_with_semicolons_and_a_bom(root):
    (root / "list.csv").write_bytes("﻿name;qty\nmug;3\n".encode("utf-8"))
    out = dr.read("list.csv")
    assert out["text"] == "name\tqty\nmug\t3" and out["rows"] == 2


def test_long_text_reads_on_with_offset(root, monkeypatch):
    monkeypatch.setattr(dr, "MAX_CHARS", 10)
    (root / "a.csv").write_text("abcdefghijklmnopqrstuvwxyz\n")
    first = dr.read("a.csv")
    assert first["text"] == "abcdefghij" and first["next_offset"] == 10 and first["chars"] == 26
    last = dr.read("a.csv", offset=20)
    assert last["text"] == "uvwxyz" and "next_offset" not in last


@pytest.mark.parametrize("name, says", [
    ("../outside.csv", "outside the folders"), (".env", "hidden files"), (".secret/a.csv", "hidden files"),
    ("notes.txt", "only .pdf"), ("missing.pdf", "no such file"),
])
def test_guards(root, tmp_path, name, says):
    (root / ".secret").mkdir()
    for f in (".env", ".secret/a.csv", "notes.txt"):
        (root / f).write_text("x")
    (tmp_path.parent / "outside.csv").write_text("x")
    with pytest.raises(dr.DocsReadError, match=says):
        dr.read(name)


def test_big_files_and_zip_bombs_are_refused(root, monkeypatch):
    monkeypatch.setattr(dr, "MAX_FILE_BYTES", 100)
    (root / "big.csv").write_text("x" * 200)
    with pytest.raises(dr.DocsReadError, match="the limit is"):
        dr.read("big.csv")
    monkeypatch.setattr(dr, "MAX_FILE_BYTES", 10**7)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "a" * (dr.MAX_UNZIPPED + 1))
    (root / "bomb.docx").write_bytes(buf.getvalue())
    with pytest.raises(dr.DocsReadError, match="unpacks to more than"):
        dr.read("bomb.docx")


def test_xml_entity_tricks_are_refused(root):
    evil = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "boom">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">&x;</w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", evil)
    (root / "evil.docx").write_bytes(buf.getvalue())
    with pytest.raises(Exception, match="(?i)entit"):
        dr.read("evil.docx")
