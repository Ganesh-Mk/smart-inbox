"""Opening a PDF safely, and saying honestly when we cannot.

Three things go wrong before a single word is extracted, and all three are real:

* **E7 — the file will not open.** Password-protected, corrupt, or zero bytes. Each is
  distinguishable and each gets its own reason, because "could not parse" is not an answer a
  reviewer can act on. Crucially the document fails, not the *message*: the email body is still
  classified and the item still reaches the queue flagged for attention rather than vanishing.

* **E8 — the file is enormous.** A 60 MB colour scan must not take the service down on an 8 GB
  machine. Caps are configurable; over the cap we process the first N pages and say so, rather
  than silently returning a partial answer that looks complete.

* **Page rotation.** A page with `/Rotate 90` reports coordinates in an unrotated space, so
  every bounding box computed from it would be wrong — and bounding boxes are what the whole
  evidence-highlighting story rests on. Normalising rotation is the first thing that happens.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.loader")


class LoadFailure(str, Enum):
    """Why a document could not be read. Each maps to a reviewer-visible reason."""

    ENCRYPTED = "ENCRYPTED"
    CORRUPT = "CORRUPT"
    EMPTY = "EMPTY"
    TOO_LARGE = "TOO_LARGE"
    NOT_A_PDF = "NOT_A_PDF"


class DocumentLoadError(Exception):
    """A document could not be opened. Carries a typed reason, not just a message."""

    def __init__(self, failure: LoadFailure, detail: str) -> None:
        super().__init__(f"{failure.value}: {detail}")
        self.failure = failure
        self.detail = detail


@dataclass
class LoadedDocument:
    """An open PDF plus the facts the rest of the pipeline needs about how it was opened."""

    doc: pymupdf.Document
    content_sha256: str
    page_count: int            # pages we will actually process
    total_page_count: int      # pages the file really has
    truncated: bool            # E8: page_count < total_page_count
    is_encrypted: bool
    size_bytes: int
    rotations: dict[int, int]  # original /Rotate per page index, before normalisation

    def __enter__(self) -> "LoadedDocument":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self.doc is not None and not self.doc.is_closed:
            self.doc.close()

    def pages(self):
        """Iterate the pages we are allowed to process, in order."""
        for index in range(self.page_count):
            yield index, self.doc[index]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(
    data: bytes,
    *,
    settings: Settings | None = None,
    filename: str | None = None,
) -> LoadedDocument:
    """Open `data` as a PDF, or raise `DocumentLoadError` with a typed reason."""
    settings = settings or get_settings()
    label = filename or "<bytes>"

    if not data:
        raise DocumentLoadError(LoadFailure.EMPTY, f"{label} is zero bytes")

    max_bytes = settings.max_attachment_mb * 1024 * 1024
    if len(data) > max_bytes:
        # Refusing outright rather than truncating bytes: a partial PDF is not a PDF, and
        # pretending otherwise produces confident nonsense.
        raise DocumentLoadError(
            LoadFailure.TOO_LARGE,
            f"{label} is {len(data) / 1e6:.1f} MB, over the {settings.max_attachment_mb} MB cap",
        )

    if not data.startswith(b"%PDF-"):
        raise DocumentLoadError(
            LoadFailure.NOT_A_PDF, f"{label} does not begin with the %PDF- signature")

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises several unrelated types here
        raise DocumentLoadError(
            LoadFailure.CORRUPT, f"{label} could not be opened: {exc}") from exc

    # An encrypted document opens but refuses its content. Try the empty password first —
    # some files are "encrypted" only to set permissions and open with no password at all.
    if doc.needs_pass:
        if not doc.authenticate(""):
            doc.close()
            raise DocumentLoadError(
                LoadFailure.ENCRYPTED,
                f"{label} is password-protected and no password was supplied",
            )
        log.info("%s was encrypted but opened with an empty password", label)

    total_pages = doc.page_count
    if total_pages == 0:
        doc.close()
        raise DocumentLoadError(LoadFailure.CORRUPT, f"{label} contains no pages")

    page_count = min(total_pages, settings.max_pdf_pages)
    truncated = page_count < total_pages
    if truncated:
        log.warning(
            "%s has %d pages; processing the first %d and flagging truncated=true (E8)",
            label, total_pages, page_count)

    rotations = _normalise_rotation(doc, page_count)

    return LoadedDocument(
        doc=doc,
        content_sha256=sha256_bytes(data),
        page_count=page_count,
        total_page_count=total_pages,
        truncated=truncated,
        is_encrypted=bool(doc.needs_pass),
        size_bytes=len(data),
        rotations=rotations,
    )


def _normalise_rotation(doc: pymupdf.Document, page_count: int) -> dict[int, int]:
    """Set every page's rotation to 0, remembering what it was.

    PyMuPDF reports text coordinates in the page's *unrotated* space unless rotation is
    normalised. Every bounding box we later hand to the UI would then be drawn in the wrong
    place on the rendered image — and the evidence highlight is the one interaction the whole
    traceability story is judged on. So this runs before anything reads a coordinate.
    """
    original: dict[int, int] = {}
    for index in range(page_count):
        page = doc[index]
        rotation = page.rotation
        original[index] = rotation
        if rotation:
            page.set_rotation(0)
            log.debug("Normalised page %d rotation from %d to 0", index + 1, rotation)
    return original


def load_path(path: str | Path, *, settings: Settings | None = None) -> LoadedDocument:
    """Convenience wrapper for scripts and tests."""
    path = Path(path)
    return load(path.read_bytes(), settings=settings, filename=path.name)
