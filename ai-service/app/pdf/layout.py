"""Reading order, and the character-offset → bounding-box index.

Two jobs, and the second is the one the whole submission rests on.

**Reading order (E14).** `page.get_text()` returns text in whatever order the producer wrote
it into the content stream, and sorting naively by y-coordinate interleaves the columns of a
two-column article into nonsense — "Introduction    Conclusion" on one line, then two unrelated
paragraphs spliced together sentence by sentence. Everything downstream inherits that damage:
the summary is incoherent, and an extraction can pull a patient's age from one column and their
drug from the other. So blocks are clustered into columns by their x-midpoint first, and only
then sorted top-to-bottom *within* each column.

**The span index.** As `text_original` is assembled, we record for every character range which
span produced it and where that span sits on the page. That index is what turns a verified
evidence quote into a highlight rectangle over the rendered page image — without embedding a
PDF renderer in the browser, and with coordinates guaranteed to agree with the extraction
because both came from the same pass over the same document.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

import pymupdf

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.layout")

BBox = tuple[float, float, float, float]


@dataclass
class SpanRef:
    """One run of characters in `text_original`, and where it sits on the page."""

    char_start: int
    char_end: int
    bbox: BBox
    block_index: int
    column: int
    font_size: float
    bold: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "s": self.char_start,
            "e": self.char_end,
            "b": [round(v, 2) for v in self.bbox],
            "col": self.column,
        }


@dataclass
class TextBlock:
    """A block of text with its geometry, after column assignment."""

    index: int
    bbox: BBox
    text: str
    column: int
    lines: int
    max_font_size: float
    any_bold: bool

    @property
    def x_mid(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def y_top(self) -> float:
        return self.bbox[1]


@dataclass
class PageLayout:
    """The result of laying out one page."""

    text: str
    spans: list[SpanRef]
    blocks: list[TextBlock]
    column_count: int
    width: float
    height: float
    char_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.char_count = len(self.text)

    # -- the lookup the UI overlay depends on -----------------------------------------

    def bbox_for_range(self, char_start: int, char_end: int) -> BBox | None:
        """Union bounding box of every span overlapping [char_start, char_end).

        Returns `None` when the range touches no span — which is a real answer, not a failure
        to paper over: it means the quote is not in this page's text, and the evidence
        verifier will already have marked it unverified.
        """
        boxes = [s.bbox for s in self.spans
                 if s.char_start < char_end and s.char_end > char_start]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )

    def line_boxes_for_range(self, char_start: int, char_end: int) -> list[BBox]:
        """One box per line of the range, so a multi-line quote highlights as several
        rectangles rather than one huge one swallowing the space between columns."""
        overlapping = [s for s in self.spans
                       if s.char_start < char_end and s.char_end > char_start]
        if not overlapping:
            return []
        rows: list[list[SpanRef]] = []
        for span in sorted(overlapping, key=lambda s: (s.column, s.bbox[1], s.bbox[0])):
            placed = False
            for row in rows:
                # Same visual line when the vertical centres are within half a line height.
                if abs(row[0].bbox[1] - span.bbox[1]) < max(2.0, span.font_size * 0.6) \
                        and row[0].column == span.column:
                    row.append(span)
                    placed = True
                    break
            if not placed:
                rows.append([span])
        return [
            (min(s.bbox[0] for s in row), min(s.bbox[1] for s in row),
             max(s.bbox[2] for s in row), max(s.bbox[3] for s in row))
            for row in rows
        ]

    def span_index_json(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.spans]


# =======================================================================================
# Column detection
# =======================================================================================

def cluster_columns(
    blocks: Sequence[tuple[float, float, float, float, str, int, int]],
    page_width: float,
    settings: Settings | None = None,
) -> tuple[int, dict[int, int]]:
    """Work out how many text columns the page has, and which column each block is in.

    A 1-D k-means over block **left edges** for k in {1, 2, 3}, accepting the largest k whose
    clusters are genuinely separated.

    Left edge, not x-midpoint. The plan specified the midpoint, and measuring it on a real
    two-column article showed why that is wrong: a column's blocks share a left edge exactly
    (they are laid out in the same frame) but their midpoints depend on how wide each block
    happens to be. On page 1 of `article_A01.pdf` the left edges are cleanly bimodal at 51 and
    308, while the midpoints scatter across 72, 82, 169, 199, 328, 336, 357 and 426 — because a
    heading like "Discussion" is 56pt wide and the paragraph beneath it is 237pt. k-means on
    those midpoints fits **three** clusters on a two-column page, splitting the left column
    into headings and body. Reading order then emits every left-column heading, then every
    left-column paragraph, then the right column — which is worse than not clustering at all.
    See DECISIONS D-012.

    Two kinds of block are excluded from the fit and assigned to column 0, because they belong
    to no column and would drag a centroid toward the middle:

    * full-width blocks — a title, or an abstract spanning the page;
    * centred blocks — an author list or a journal line, which is narrow but sits on the page
      centreline rather than on a column edge.

    Returns `(column_count, {block_index: column})`.
    """
    settings = settings or get_settings()
    text_blocks = [(i, b) for i, b in enumerate(blocks) if b[6] == 0 and b[4].strip()]
    if len(text_blocks) < 4:
        return 1, {i: 0 for i, _ in text_blocks}

    page_mid = page_width / 2
    full_width_cut = page_width * 0.65
    centred_tolerance = page_width * 0.04

    def spans_page(b) -> bool:
        return (b[2] - b[0]) >= full_width_cut

    def is_centred(b) -> bool:
        return abs(((b[0] + b[2]) / 2) - page_mid) < centred_tolerance

    banner = {i for i, b in text_blocks if spans_page(b) or is_centred(b)}
    column_candidates = [(i, b[0]) for i, b in text_blocks if i not in banner]

    if len(column_candidates) < 4:
        return 1, {i: 0 for i, _ in text_blocks}

    lefts = [x for _, x in column_candidates]
    best_k, best_centroids = 1, [sum(lefts) / len(lefts)]

    for k in range(2, settings.max_columns + 1):
        if len(lefts) < k * 2:
            break
        centroids = _kmeans_1d(lefts, k)
        if centroids is None:
            continue
        centroids = sorted(centroids)
        gaps = [centroids[j + 1] - centroids[j] for j in range(len(centroids) - 1)]
        if min(gaps, default=0.0) < page_width * settings.column_separation_ratio:
            break  # the clusters are not actually separated; k-1 was the honest answer
        # Every cluster must be non-empty. The separation check above is the meaningful test —
        # requiring a *share* of the blocks is too strict: a short article can leave its right
        # column holding a single trailing reference, and calling that page single-column puts
        # that reference in the middle of the left column when the blocks are sorted by y.
        counts = [0] * k
        for x in lefts:
            counts[_nearest(centroids, x)] += 1
        if min(counts) < 1:
            break
        best_k, best_centroids = k, centroids

    assignment = {i: _nearest(best_centroids, b[0]) for i, b in text_blocks}

    # Banner blocks read before the body, so they belong at the head of column 0.
    for index in banner:
        assignment[index] = 0

    if best_k > 1 and _is_row_aligned_grid(text_blocks, assignment, banner):
        # Not columns at all — a grid. See _is_row_aligned_grid for why this matters.
        return 1, {i: 0 for i, _ in text_blocks}

    return best_k, assignment


def _is_row_aligned_grid(
    text_blocks: Sequence[tuple[int, tuple]],
    assignment: dict[int, int],
    banner: set[int],
) -> bool:
    """True when the "columns" are the columns of a table, not independent text flows.

    This distinction decides reading order, and getting it wrong damages the most common
    document in the corpus. A filled-in report form is a grid: "Report reference | Date of
    report" on one row, "Type of report | Serious?" on the next. Its blocks cluster into two
    x-groups exactly like a two-column article does — but it must be read **across** each row,
    not down each column. Reading it column-major separates every field in the right-hand
    column from the row it belongs to.

    The test is direct: in a grid, a block in a right-hand column almost always has a
    vertically-aligned partner in column 0. In a genuine two-column article the columns are
    independent flows and such pairings are incidental.
    """
    left_tops = [b[1] for i, b in text_blocks
                 if i not in banner and assignment.get(i, 0) == 0]
    others = [b for i, b in text_blocks
              if i not in banner and assignment.get(i, 0) > 0]
    # With only one or two blocks outside column 0 there is nothing to generalise from: a
    # single trailing reference that happens to sit level with a left-column paragraph would
    # otherwise be read as a 100%-aligned grid and collapse a real two-column page.
    if len(others) < 3 or not left_tops:
        return False

    paired = sum(
        1 for b in others
        if any(abs(b[1] - top) < 6.0 for top in left_tops))
    return paired / len(others) > 0.6


def _kmeans_1d(values: Sequence[float], k: int, iterations: int = 40) -> list[float] | None:
    """Deterministic 1-D k-means. Seeded by quantiles, so the result is reproducible."""
    ordered = sorted(values)
    if len(ordered) < k:
        return None
    centroids = [ordered[int((j + 0.5) * len(ordered) / k)] for j in range(k)]

    for _ in range(iterations):
        buckets: list[list[float]] = [[] for _ in range(k)]
        for value in ordered:
            buckets[_nearest(centroids, value)].append(value)
        if any(not bucket for bucket in buckets):
            return None
        moved = [sum(bucket) / len(bucket) for bucket in buckets]
        if all(abs(a - b) < 0.5 for a, b in zip(moved, centroids)):
            centroids = moved
            break
        centroids = moved
    return centroids


def _nearest(centroids: Sequence[float], value: float) -> int:
    best, best_distance = 0, abs(value - centroids[0])
    for index in range(1, len(centroids)):
        distance = abs(value - centroids[index])
        if distance < best_distance:
            best, best_distance = index, distance
    return best


# =======================================================================================
# Layout
# =======================================================================================

def layout_page(page: pymupdf.Page, settings: Settings | None = None) -> PageLayout:
    """Extract a page's text in correct reading order, with a span index alongside."""
    settings = settings or get_settings()
    raw_blocks = page.get_text("blocks")
    column_count, assignment = cluster_columns(raw_blocks, page.rect.width, settings)

    data = page.get_text("dict")
    blocks: list[TextBlock] = []

    for block_index, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue  # image block; handled by pdf/images.py
        lines = block.get("lines", [])
        if not lines:
            continue
        text = "".join(
            span.get("text", "") for line in lines for span in line.get("spans", []))
        if not text.strip():
            continue
        bbox = tuple(block.get("bbox", (0, 0, 0, 0)))
        sizes = [span.get("size", 0.0) for line in lines for span in line.get("spans", [])]
        flags = [span.get("flags", 0) for line in lines for span in line.get("spans", [])]
        blocks.append(TextBlock(
            index=block_index,
            bbox=bbox,
            text=text,
            column=assignment.get(block_index, 0),
            lines=len(lines),
            max_font_size=max(sizes) if sizes else 0.0,
            any_bold=any(f & 2 ** 4 for f in flags),   # bit 4 is the bold flag
        ))

    # Column-major, then top-to-bottom. This one sort is the whole of E14.
    blocks.sort(key=lambda b: (b.column, round(b.y_top, 1), b.bbox[0]))

    text_parts: list[str] = []
    spans: list[SpanRef] = []
    cursor = 0

    block_lookup = {b.index: b for b in blocks}
    for block in blocks:
        source = data["blocks"][block.index]
        for line in source.get("lines", []):
            for span in line.get("spans", []):
                content = span.get("text", "")
                if not content:
                    continue
                spans.append(SpanRef(
                    char_start=cursor,
                    char_end=cursor + len(content),
                    bbox=tuple(span.get("bbox", block.bbox)),
                    block_index=block.index,
                    column=block.column,
                    font_size=span.get("size", 0.0),
                    bold=bool(span.get("flags", 0) & 2 ** 4),
                ))
                text_parts.append(content)
                cursor += len(content)
            # Line break: part of the text, so offsets stay exact, but owned by no span.
            text_parts.append("\n")
            cursor += 1
        text_parts.append("\n")
        cursor += 1

    text = "".join(text_parts)

    return PageLayout(
        text=text,
        spans=spans,
        blocks=blocks,
        column_count=column_count,
        width=page.rect.width,
        height=page.rect.height,
    )


def naive_page_text(page: pymupdf.Page) -> str:
    """What a column-unaware reader produces — kept so the difference can be *shown*.

    Used by `scripts/show_reading_order.py` to put the two side by side in the walkthrough.
    A claim that column handling matters is worth much less than the two outputs next to
    each other.
    """
    return page.get_text("text", sort=True)
