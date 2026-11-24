"""PDF and Word files (KNW-04, docs/13 §15): read as text, cited by page or heading, refused safely."""

from __future__ import annotations

import zipfile
from pathlib import Path

import docx
import pytest
from pypdf import PdfWriter

from hedwig.core.types import MemoryKind
from hedwig.wiring import Container


def write_pdf(path: Path, pages: list[str]) -> None:
    """A minimal real PDF with one line of Helvetica per page."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        content_id = len(objects) + 1
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_id = len(objects) + 1
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents %d 0 R "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
            % content_id
        )
        kids.append(page_id)
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )
    body = b"%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.write_bytes(body)


@pytest.fixture
def papers(tmp_path: Path) -> Path:
    folder = tmp_path / "papers"
    folder.mkdir()
    write_pdf(
        folder / "tides.pdf",
        ["Tides rise and fall twice a day.", "Neap tides come at right angles."],
    )

    plan = docx.Document()
    plan.add_heading("Porto trip", level=1)
    plan.add_paragraph("We travel in June.")
    plan.add_heading("Budget", level=2)
    plan.add_paragraph("The hotel costs 90 euro a night.")
    table = plan.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Train", "35 euro"
    table.cell(1, 0).text, table.cell(1, 1).text = "Museum", "12 euro"
    plan.save(str(folder / "plan.docx"))

    keyed = docx.Document()
    keyed.add_paragraph("server access: AKIAABCDEFGHIJKLMNOP")
    keyed.save(str(folder / "access.docx"))

    blank = PdfWriter()
    blank.add_blank_page(612, 792)
    blank.write(str(folder / "scan.pdf"))

    locked = PdfWriter()
    locked.add_blank_page(612, 792)
    locked.encrypt("secret-pass")
    locked.write(str(folder / "locked.pdf"))

    (folder / "broken.pdf").write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with zipfile.ZipFile(folder / "bomb.docx", "w", zipfile.ZIP_DEFLATED) as bomb:
        bomb.writestr("word/document.xml", b"\0" * 60_000_000)
    return folder


async def test_pdfs_and_word_files_are_read_and_the_hostile_ones_reported(
    running_container: Container, papers: Path
) -> None:
    report = await running_container.library.add(str(papers))

    assert report["added"] == 2
    reasons = {Path(s["path"]).name: s["reason"] for s in report["skipped"]}
    assert reasons == {
        "access.docx": "contains what looks like a key",
        "scan.pdf": "no text in it (a scanned PDF?)",
        "locked.pdf": "encrypted",
        "broken.pdf": "could not be read as a PDF",
        "bomb.docx": "unzips to more than the safe limit",
    }
    rows = running_container.database.query("SELECT text FROM document_chunk")
    assert not any("AKIA" in r["text"] for r in rows)
    mimes = {
        Path(d["path"]).name: d.get("mime")
        for d in running_container.library.listing()["documents"]
    }
    assert set(mimes) == {"tides.pdf", "plan.docx"}


async def test_a_pdf_passage_is_cited_by_its_page(
    running_container: Container, papers: Path
) -> None:
    await running_container.library.add(str(papers))

    working = await running_container.retrieval.search(["neap tides right angles"])

    top = max(working.items, key=lambda item: item.score)
    assert top.kind is MemoryKind.DOCUMENT
    assert top.text.startswith("tides > Page 2:")
    assert "right angles" in top.text


async def test_a_word_file_keeps_its_headings_and_tables(
    running_container: Container, papers: Path
) -> None:
    await running_container.library.add(str(papers))

    working = await running_container.retrieval.search(["hotel budget"])
    top = max(working.items, key=lambda item: item.score)
    assert top.text.startswith("plan > Porto trip > Budget:")
    assert "90 euro" in top.text and "Train | 35 euro" in top.text


async def test_an_unchanged_pdf_is_not_read_again(
    running_container: Container, papers: Path
) -> None:
    await running_container.library.add(str(papers))
    assert (await running_container.library.reindex())["unchanged"] == 2
