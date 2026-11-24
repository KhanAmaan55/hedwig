"""PDF and Word files as text the chunker understands (docs/13 §15, KNW-04).

Extraction only: no script in a PDF runs and no macro in a document is read. Output is
Markdown-style — a `## Page N` heading per PDF page, `#` headings from a Word file's heading
styles — so §14's chunker, citations and everything after them stay unchanged.

The parsers are imported when first needed; without them, these files are skipped with a reason.

Every file here is untrusted input to a third-party parser, so each is read inside budgets: the
pages, the bytes any one PDF stream may decode to, the wall time between pages, the characters
kept, and the size a Word file unzips to. Whatever a parser raises — a malformed file, a budget
reached, an encryption scheme it cannot handle — becomes a skip reason, never a stopped folder.
"""

from __future__ import annotations

import logging
import re
import time
import zipfile
from pathlib import Path
from typing import Any

BINARY_SUFFIXES = frozenset({".pdf", ".docx"})
MAX_PAGES = 1_000
MAX_PDF_STREAM = 20_000_000
"""Bytes one PDF stream may decode to. pypdf's own default is 75 MB; text pages need far less,
and text extraction costs many times the decoded size in memory."""
MAX_PDF_SECONDS = 60.0
"""Wall time for one PDF, checked between pages (each page is bounded by the stream budget)."""
MAX_DOCX_UNZIPPED = 50_000_000
"""A .docx is a zip; one that would unzip past this is refused before it is parsed."""

_LOOKS_LIKE_HEADING = re.compile(r"^(\s*)(#{1,6}\s)", re.MULTILINE)


# pypdf narrates repairs and font guesses at WARNING ("fontTools is required…", "Ignoring wrong
# pointing object"): measured on a real 1.5 MB PDF, a screenful per file, none of it actionable.
# What matters reaches the user as a skip reason instead.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class UnreadableError(Exception):
    """Why a file could not be read, in words shown to the user."""


def extract(path: Path, *, max_chars: int) -> str:
    suffix = path.suffix.lower()
    text = _pdf(path, max_chars=max_chars) if suffix == ".pdf" else _docx(path)
    if not text.strip():
        raise UnreadableError(
            "no text in it (a scanned PDF?)" if suffix == ".pdf" else "no text in it"
        )
    if len(text) > max_chars:
        raise UnreadableError(TOO_MUCH_TEXT)
    return text


TOO_MUCH_TEXT = "more text than the size limit"


def mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[path.suffix.lower()]


def _pdf(path: Path, *, max_chars: int) -> str:
    try:
        from pypdf import PdfReader, apply_configuration, get_configuration  # pypdf ≥ 7.1
        from pypdf.errors import DependencyError, LimitReachedError
    except ImportError as error:
        raise UnreadableError("PDF reading is not installed") from error

    budget = get_configuration().with_overwrites(
        **dict.fromkeys(
            (
                "zlib_maximum_output_length",
                "lzw_maximum_output_length",
                "run_length_maximum_output_length",
                "array_based_stream_maximum_output_length",
                "brotli_maximum_output_length",
                "maximum_declared_stream_length",
            ),
            MAX_PDF_STREAM,
        )
    )
    deadline = time.monotonic() + MAX_PDF_SECONDS
    try:
        with apply_configuration(budget):
            reader = PdfReader(path)
            if reader.is_encrypted:
                raise UnreadableError("encrypted")
            if len(reader.pages) > MAX_PAGES:
                raise UnreadableError(f"more than {MAX_PAGES} pages")
            pages: list[str] = []
            kept = 0
            for number, page in enumerate(reader.pages, start=1):
                if time.monotonic() > deadline:
                    raise UnreadableError("took too long to read")
                words = _plain((page.extract_text() or "").strip())
                if words:
                    pages.append(f"## Page {number}\n\n{words}")
                    kept += len(pages[-1]) + 2
                    if kept > max_chars:  # stop now, not after the last page
                        raise UnreadableError(TOO_MUCH_TEXT)
            return "\n\n".join(pages)
    except UnreadableError:
        raise
    except DependencyError as error:  # e.g. AES without a crypto library, raised while opening
        raise UnreadableError("encrypted") from error
    except LimitReachedError as error:
        raise UnreadableError("too large inside to read safely") from error
    except Exception as error:  # a parser boundary: malformed input raises anything
        raise UnreadableError("could not be read as a PDF") from error


def _docx(path: Path) -> str:
    try:
        import docx
    except ImportError as error:
        raise UnreadableError("Word reading is not installed") from error
    try:
        with zipfile.ZipFile(path) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_DOCX_UNZIPPED:
                raise UnreadableError("unzips to more than the safe limit")
        return _docx_text(docx.Document(str(path)))
    except UnreadableError:
        raise
    except Exception as error:  # a parser boundary: bad zips, bad XML (lxml), missing parts
        raise UnreadableError("could not be read as a Word document") from error


def _docx_text(document: Any) -> str:
    """Paragraphs and tables in document order; headings from the heading styles."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph

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
            lines.append(f"{'#' * level} {text}" if level else _plain(text))
        elif tag == "tbl":
            for row in Table(block, document).rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    lines.append(_plain(" | ".join(cells)))
    return "\n\n".join(lines)


def _plain(text: str) -> str:
    """Text that only looks like a Markdown heading ("# 3 items") is kept as text.

    The chunker reads `#` lines as headings; one inside a page would replace "Page N" in every
    citation after it. A leading space is enough for it to stay a line of text.
    """
    return _LOOKS_LIKE_HEADING.sub(r" \1\2", text)


def _heading_level(style: str) -> int:
    """ "Heading 2" → 2, "Title" → 1, anything else → 0."""
    if style == "Title":
        return 1
    if style.startswith("Heading "):
        number = style.removeprefix("Heading ").strip()
        if number.isdigit():
            return min(6, max(1, int(number)))
    return 0
