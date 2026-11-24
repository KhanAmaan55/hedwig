"""PDF and Word files as text the chunker understands (docs/13 §15, KNW-04).

Extraction only: no script in a PDF runs and no macro in a document is read. Output is
Markdown-style — a `## Page N` heading per PDF page, `#` headings from a Word file's heading
styles — so §14's chunker, citations and everything after them stay unchanged.

The parsers are imported when first needed; without them, these files are skipped with a reason.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

BINARY_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_PAGES = 1_000
MAX_DOCX_UNZIPPED = 50_000_000
"""A .docx is a zip; one that would unzip past this is refused before it is parsed."""


# pypdf narrates repairs and font guesses at WARNING ("fontTools is required…", "Ignoring wrong
# pointing object"): measured on a real 1.5 MB PDF, a screenful per file, none of it actionable.
# What matters reaches the user as a skip reason instead.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class UnreadableError(Exception):
    """Why a file could not be read, in words shown to the user."""


def extract(path: Path, *, max_chars: int) -> str:
    suffix = path.suffix.lower()
    text = _pdf(path) if suffix == ".pdf" else _docx(path)
    if not text.strip():
        raise UnreadableError(
            "no text in it (a scanned PDF?)" if suffix == ".pdf" else "no text in it"
        )
    if len(text) > max_chars:
        raise UnreadableError("more text than the size limit")
    return text


def mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[path.suffix.lower()]


def _pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as error:
        raise UnreadableError("PDF reading is not installed") from error
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise UnreadableError("encrypted")
        if len(reader.pages) > MAX_PAGES:
            raise UnreadableError(f"more than {MAX_PAGES} pages")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            words = (page.extract_text() or "").strip()
            if words:
                pages.append(f"## Page {number}\n\n{words}")
        return "\n\n".join(pages)
    except UnreadableError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as error:
        raise UnreadableError("could not be read as a PDF") from error


def _docx(path: Path) -> str:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as error:
        raise UnreadableError("Word reading is not installed") from error
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_DOCX_UNZIPPED:
                raise UnreadableError("unzips to more than the safe limit")
        document = docx.Document(str(path))
    except UnreadableError:
        raise
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as error:
        raise UnreadableError("could not be read as a Word document") from error

    lines: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(block, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "") or ""
            level = _heading_level(style)
            lines.append(f"{'#' * level} {text}" if level else text)
        elif tag == "tbl":
            table = Table(block, document)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(" | ".join(cells))
    return "\n\n".join(lines)


def _heading_level(style: str) -> int:
    """ "Heading 2" → 2, "Title" → 1, anything else → 0."""
    if style == "Title":
        return 1
    if style.startswith("Heading "):
        number = style.removeprefix("Heading ").strip()
        if number.isdigit():
            return min(6, max(1, int(number)))
    return 0
