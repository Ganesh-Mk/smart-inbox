"""Deciding which images are worth looking at (E19).

The brief asks for images to be "described and flagged for human review". Taken literally that
means describing every letterhead, every 3x3 pixel spacer and every rule line — which costs a
vision call each, clutters the reviewer's screen with "a blue rectangle containing the word
CORELINE", and buries the one image that actually matters: the photograph of the cracked
blister pack.

So images are filtered before they are described, on three tests, each aimed at a specific
kind of non-image:

* **area >= 3% of the page** — kills spacers, bullets and rule lines.
* **colour standard deviation above a floor** — kills solid blocks, gradients and flat
  backgrounds, which have area but no content.
* **not repeated across pages by xref** — kills letterheads and logos. This is the one that
  does the real work, and it is why the corpus embeds the same logo on several pages: an image
  that appears identically on page 1, 2 and 3 is furniture, not evidence.

One deliberate exception: an image covering more than ~80% of the page **is** the scanned page
and is handled by the page path, not as an embedded image. Describing it twice would double
the cost and produce two contradictory accounts of the same thing.

Survivors get `needs_review='Y'` unconditionally — the brief requires a human to check image
interpretation, and that is not a judgement the model gets to make about its own output.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

import pymupdf

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.images")


@dataclass
class CandidateImage:
    """One embedded image and everything the filter needs to judge it."""

    page_no: int
    image_index: int
    xref: int
    bbox: tuple[float, float, float, float]
    width: int
    height: int
    area_ratio: float
    colour_stddev: float
    repeated_on_pages: int
    keep: bool
    reject_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "page_no": self.page_no,
            "image_index": self.image_index,
            "xref": self.xref,
            "bbox": [round(v, 2) for v in self.bbox],
            "width": self.width,
            "height": self.height,
            "area_ratio": round(self.area_ratio, 6),
            "keep": self.keep,
            "reject_reason": self.reject_reason,
        }


def _colour_stddev(doc: pymupdf.Document, xref: int) -> float:
    """Rough colour variance of an image, 0 for a solid block.

    Sampled rather than computed exactly: a full pass over a large photograph costs more than
    the decision is worth, and the threshold is coarse by design.
    """
    try:
        pixmap = pymupdf.Pixmap(doc, xref)
    except Exception:
        return 0.0
    try:
        if pixmap.n > 3:                       # drop alpha / CMYK before sampling
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
        samples = pixmap.samples
        if not samples:
            return 0.0
        step = max(1, len(samples) // 4096)
        values = samples[::step]
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return variance ** 0.5
    except Exception:
        return 0.0
    finally:
        del pixmap


def collect_candidates(
    doc: pymupdf.Document,
    page_count: int,
    settings: Settings | None = None,
) -> list[CandidateImage]:
    """Find every embedded image and decide which are worth describing."""
    settings = settings or get_settings()

    # First pass: how many distinct pages does each xref appear on? An image repeated across
    # pages is a letterhead by definition.
    pages_by_xref: dict[int, set[int]] = defaultdict(set)
    raw: list[tuple[int, int, int, pymupdf.Rect]] = []

    for page_index in range(page_count):
        page = doc[page_index]
        page_area = page.rect.get_area()
        if page_area <= 0:
            continue
        for image_index, image in enumerate(page.get_images(full=True)):
            xref = image[0]
            try:
                rects = page.get_image_rects(xref)
            except Exception:
                continue
            if not rects:
                continue
            pages_by_xref[xref].add(page_index)
            largest = max(rects, key=lambda r: r.get_area())
            raw.append((page_index, image_index, xref, largest))

    candidates: list[CandidateImage] = []
    for page_index, image_index, xref, rect in raw:
        page = doc[page_index]
        page_area = page.rect.get_area()
        area_ratio = rect.get_area() / page_area if page_area else 0.0
        repeated = len(pages_by_xref.get(xref, ()))

        reject: str | None = None

        if area_ratio >= settings.image_full_page_ratio:
            # This is the scanned page itself. Handled by the page path (E13), not here.
            reject = "FULL_PAGE_SCAN"
        elif area_ratio < settings.image_min_area_ratio:
            reject = (f"too small: {area_ratio:.2%} of the page "
                      f"(minimum {settings.image_min_area_ratio:.0%})")
        elif repeated > 1:
            reject = f"repeated on {repeated} pages — letterhead or logo"

        stddev = 0.0
        if reject is None:
            stddev = _colour_stddev(doc, xref)
            if stddev < settings.image_min_stddev:
                reject = (f"flat colour (stddev {stddev:.1f} < {settings.image_min_stddev}) — "
                          "solid block or rule")

        candidates.append(CandidateImage(
            page_no=page_index + 1,
            image_index=image_index,
            xref=xref,
            bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
            width=int(rect.width),
            height=int(rect.height),
            area_ratio=area_ratio,
            colour_stddev=stddev,
            repeated_on_pages=repeated,
            keep=reject is None,
            reject_reason=reject,
        ))

    kept = sum(1 for c in candidates if c.keep)
    if candidates:
        log.info("Image filter: %d candidate(s), %d meaningful, %d rejected (E19)",
                 len(candidates), kept, len(candidates) - kept)
    return candidates


def meaningful(candidates: Sequence[CandidateImage]) -> list[CandidateImage]:
    return [c for c in candidates if c.keep]
