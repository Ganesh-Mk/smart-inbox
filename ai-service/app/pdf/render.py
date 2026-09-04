"""Rendering pages and regions to PNG, cached by content hash.

Rendered pages serve two masters and both matter:

* **Vision input.** A scanned page has no text layer, so the only way to read it is to send a
  picture to the model. This is where the single-model constraint is honoured concretely — we
  render locally with PyMuPDF and send PNGs to Claude's own vision, rather than enabling
  OpenRouter's PDF plugin, whose default engine is another vendor's OCR model.

* **The review UI.** The left pane shows these exact images with highlight rectangles drawn
  over them. Because the coordinates come from the same PyMuPDF pass that produced the text,
  the highlight is guaranteed to land on the right words — no PDF.js, no second coordinate
  space to reconcile.

The cache is keyed by `(content hash, page, dpi)`, so re-running a document that has already
been parsed costs no rendering and, further downstream, no LLM calls at all (E9). That is what
makes the demo replayable offline.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.render")


@dataclass
class RenderedPage:
    """A rendered page image on disk, plus what it takes to send it to the model."""

    page_no: int
    path: Path
    width_px: int
    height_px: int
    dpi: int
    cached: bool

    def relative_to(self, root: Path) -> str:
        try:
            return str(self.path.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(self.path)

    def as_base64(self) -> str:
        return base64.b64encode(self.path.read_bytes()).decode("ascii")


def _render_dir(settings: Settings, content_sha256: str) -> Path:
    directory = settings.render_dir / content_sha256[:2] / content_sha256
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def render_page(
    page: pymupdf.Page,
    content_sha256: str,
    page_no: int,
    *,
    dpi: int | None = None,
    settings: Settings | None = None,
) -> RenderedPage:
    """Render one page to PNG, reusing the cached file when it already exists."""
    settings = settings or get_settings()
    dpi = dpi or settings.render_dpi

    directory = _render_dir(settings, content_sha256)
    target = directory / f"p{page_no}@{dpi}.png"

    if target.exists():
        with pymupdf.open(str(target)) as probe:
            rect = probe[0].rect
        return RenderedPage(page_no, target, int(rect.width), int(rect.height), dpi, cached=True)

    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    pixmap.save(str(target))
    log.debug("Rendered page %d at %d dpi -> %s (%dx%d)",
              page_no, dpi, target.name, pixmap.width, pixmap.height)
    return RenderedPage(page_no, target, pixmap.width, pixmap.height, dpi, cached=False)


def render_region(
    page: pymupdf.Page,
    content_sha256: str,
    page_no: int,
    bbox: tuple[float, float, float, float],
    *,
    label: str,
    dpi: int | None = None,
    padding: float = 4.0,
    settings: Settings | None = None,
) -> RenderedPage:
    """Render a cropped region — a borderless table, or an embedded image (E18, E19).

    A little padding is added because a bounding box that hugs the ink cuts off descenders and
    the outermost rule, and a table image missing its last column is worse than no image.
    """
    settings = settings or get_settings()
    dpi = dpi or settings.image_render_dpi

    directory = _render_dir(settings, content_sha256)
    key = hashlib.sha256(
        f"{page_no}:{label}:{bbox}:{dpi}".encode("utf-8")).hexdigest()[:16]
    target = directory / f"region-{key}.png"

    if target.exists():
        with pymupdf.open(str(target)) as probe:
            rect = probe[0].rect
        return RenderedPage(page_no, target, int(rect.width), int(rect.height), dpi, cached=True)

    clip = pymupdf.Rect(
        max(page.rect.x0, bbox[0] - padding),
        max(page.rect.y0, bbox[1] - padding),
        min(page.rect.x1, bbox[2] + padding),
        min(page.rect.y1, bbox[3] + padding),
    )
    zoom = dpi / 72.0
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    pixmap.save(str(target))
    return RenderedPage(page_no, target, pixmap.width, pixmap.height, dpi, cached=False)


def page_image_stats(page: pymupdf.Page) -> tuple[float, int]:
    """`(largest image area as a fraction of the page, number of images)`.

    Feeds the E13 "is this page a scan?" decision: a single image covering more than 80% of
    the page is the page, whatever text layer may also be present.
    """
    page_area = page.rect.get_area()
    if page_area <= 0:
        return 0.0, 0

    largest = 0.0
    images = page.get_images(full=True)
    for image in images:
        xref = image[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:  # a malformed xref should not sink the page
            continue
        for rect in rects:
            largest = max(largest, rect.get_area() / page_area)
    return largest, len(images)
