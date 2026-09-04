"""Tables extracted as tables (R5), not flattened into prose.

A lab-results table read as running text becomes "Alanine aminotransferase 640 U/L 10 - 40 H
Aspartate aminotransferase 412 U/L 10 - 35 H", and the association between a value and its
reference range is gone. For a safety case that association is the whole point — 640 against a
range of 10-40 is the finding.

Three paths, in order of preference (E18):

1. **PyMuPDF `find_tables()`** — works on ruled tables and on well-aligned unruled ones.
2. **Vision fallback** — when `find_tables()` returns nothing but the block geometry clearly
   looks like a grid, the region is cropped, rendered, and sent to the model with a
   table-to-JSON prompt. Borderless tables are common in generated forms and this is the only
   honest way to read them.
3. **Cross-page merge** — a table split by a page break is reassembled when the next page's
   first table repeats the header row. Without this, a lab panel spanning two pages arrives as
   two unrelated tables and the second has no headers at all.

Rotation is already normalised by `loader.py` before any of this runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import pymupdf

from app.pdf.layout import PageLayout, TextBlock
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.tables")

METHOD_PYMUPDF = "PYMUPDF"
METHOD_VISION = "VISION"
METHOD_MERGED = "MERGED"


@dataclass
class ExtractedTable:
    """One table, with its structure preserved."""

    page_no: int
    table_index: int
    headers: list[str]
    rows: list[list[str]]
    bbox: tuple[float, float, float, float]
    method: str
    caption: str | None = None
    continued_from: int | None = None   # index of the table this continues

    @property
    def n_rows(self) -> int:
        return len(self.rows)

    @property
    def n_cols(self) -> int:
        return len(self.headers) if self.headers else (len(self.rows[0]) if self.rows else 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "table_index": self.table_index,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "caption": self.caption,
            "headers": self.headers,
            "rows": self.rows,
            "bbox": [round(v, 2) for v in self.bbox],
            "extraction_method": self.method,
            "continued_from": self.continued_from,
        }

    def as_markdown(self) -> str:
        """A compact rendering for the extraction prompt — far cheaper than JSON in tokens."""
        lines = []
        if self.headers:
            lines.append(" | ".join(self.headers))
            lines.append(" | ".join("---" for _ in self.headers))
        for row in self.rows:
            lines.append(" | ".join(cell.replace("\n", " ") for cell in row))
        return "\n".join(lines)


@dataclass
class TableRegion:
    """A region the geometry says is tabular but `find_tables()` did not return (E18)."""

    page_no: int
    bbox: tuple[float, float, float, float]
    row_count: int
    column_count: int


@dataclass
class PageTables:
    tables: list[ExtractedTable] = field(default_factory=list)
    vision_candidates: list[TableRegion] = field(default_factory=list)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def extract_native(page: pymupdf.Page, page_no: int, start_index: int = 0) -> list[ExtractedTable]:
    """PyMuPDF's own table finder — the primary path."""
    tables: list[ExtractedTable] = []
    try:
        finder = page.find_tables()
    except Exception as exc:
        log.warning("find_tables() failed on page %d: %s", page_no, exc)
        return tables

    for offset, table in enumerate(finder.tables):
        try:
            data = table.extract()
        except Exception as exc:
            log.warning("Could not extract table %d on page %d: %s", offset, page_no, exc)
            continue
        if not data:
            continue

        rows = [[_clean(cell) for cell in row] for row in data]
        # Drop rows that are entirely empty — a ruled grid often yields spacer rows.
        rows = [r for r in rows if any(cell for cell in r)]
        if not rows:
            continue

        header_row = getattr(table, "header", None)
        if header_row is not None and getattr(header_row, "names", None):
            headers = [_clean(name) for name in header_row.names]
            # find_tables includes the header in extract() output; drop the duplicate.
            if rows and [_clean(c) for c in rows[0]] == headers:
                rows = rows[1:]
        else:
            headers = rows[0]
            rows = rows[1:]

        if not rows:
            continue

        tables.append(ExtractedTable(
            page_no=page_no,
            table_index=start_index + offset,
            headers=headers,
            rows=rows,
            bbox=tuple(table.bbox),
            method=METHOD_PYMUPDF,
        ))

    return tables


def find_tabular_regions(
    layout: PageLayout,
    page_no: int,
    covered: Sequence[tuple[float, float, float, float]],
    settings: Settings | None = None,
) -> list[TableRegion]:
    """Spot grid-shaped areas that `find_tables()` missed, for the vision fallback.

    The heuristic is deliberately conservative: at least three rows whose blocks share two or
    more consistent x-positions. Being eager here costs a vision call per false positive, so
    the bar is set to catch real borderless tables and little else.
    """
    settings = settings or get_settings()

    def is_covered(block: TextBlock) -> bool:
        for box in covered:
            if (block.bbox[0] >= box[0] - 2 and block.bbox[2] <= box[2] + 2
                    and block.bbox[1] >= box[1] - 2 and block.bbox[3] <= box[3] + 2):
                return True
        return False

    blocks = [b for b in layout.blocks if b.text.strip() and not is_covered(b)]
    if len(blocks) < 6:
        return []

    # Group blocks into visual rows by y-overlap.
    rows: list[list[TextBlock]] = []
    for block in sorted(blocks, key=lambda b: (round(b.bbox[1], 0), b.bbox[0])):
        placed = False
        for row in rows:
            if abs(row[0].bbox[1] - block.bbox[1]) < 6:
                row.append(block)
                placed = True
                break
        if not placed:
            rows.append([block])

    multi_column_rows = [r for r in rows if len(r) >= 2]
    if len(multi_column_rows) < 3:
        return []

    # The x-positions must actually line up between rows, or this is just wrapped prose.
    starts = [sorted(round(b.bbox[0] / 5) * 5 for b in row) for row in multi_column_rows]
    reference = starts[0]
    consistent = sum(
        1 for candidate in starts
        if len(candidate) >= 2 and len(set(candidate) & set(reference)) >= 2)
    if consistent < 3:
        return []

    involved = [b for row in multi_column_rows for b in row]
    bbox = (
        min(b.bbox[0] for b in involved),
        min(b.bbox[1] for b in involved),
        max(b.bbox[2] for b in involved),
        max(b.bbox[3] for b in involved),
    )
    log.info("Page %d: block geometry suggests a %d-row table find_tables() missed — "
             "queuing the vision fallback (E18)", page_no, len(multi_column_rows))
    return [TableRegion(
        page_no=page_no,
        bbox=bbox,
        row_count=len(multi_column_rows),
        column_count=max(len(r) for r in multi_column_rows),
    )]


def extract_page_tables(
    page: pymupdf.Page,
    layout: PageLayout,
    page_no: int,
    start_index: int = 0,
    settings: Settings | None = None,
) -> PageTables:
    """Native extraction plus a list of regions worth a vision call."""
    tables = extract_native(page, page_no, start_index)
    covered = [t.bbox for t in tables]
    candidates = find_tabular_regions(layout, page_no, covered, settings)
    return PageTables(tables=tables, vision_candidates=candidates)


def _headers_match(a: Sequence[str], b: Sequence[str]) -> bool:
    """Same header row, ignoring case, spacing and empty trailing columns."""
    def normalise(cells: Sequence[str]) -> list[str]:
        out = [" ".join(str(c).split()).lower() for c in cells]
        while out and not out[-1]:
            out.pop()
        return out

    left, right = normalise(a), normalise(b)
    if not left or not right or len(left) != len(right):
        return False
    return left == right


def merge_continuations(tables: list[ExtractedTable]) -> list[ExtractedTable]:
    """Stitch a table split across a page break back together (E18).

    The signal is the next page's *first* table repeating the header row — which is exactly
    what a well-produced document does, because the header is repeated for the reader.
    """
    if len(tables) < 2:
        return tables

    ordered = sorted(tables, key=lambda t: (t.page_no, t.table_index))
    merged: list[ExtractedTable] = []

    for table in ordered:
        if merged:
            previous = merged[-1]
            is_next_page = table.page_no == previous.page_no + 1
            is_first_on_page = table.table_index == min(
                t.table_index for t in ordered if t.page_no == table.page_no)
            if is_next_page and is_first_on_page and _headers_match(previous.headers, table.headers):
                log.info("Merging table on page %d into the one on page %d — repeated header "
                         "row (E18)", table.page_no, previous.page_no)
                previous.rows.extend(table.rows)
                previous.method = METHOD_MERGED
                continue
        merged.append(table)

    return merged
