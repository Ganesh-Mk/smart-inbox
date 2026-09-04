"""Reading order and the span index (E14).

These run against the real committed corpus PDFs. The two-column article is the case the whole
edge case exists for, and the assertions below are the difference between a summary that reads
as English and one that reads as two spliced-together halves.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from app.pdf.layout import cluster_columns, layout_page, naive_page_text

PDFS = Path(__file__).resolve().parents[2] / "testdata" / "corpus" / "pdfs"


def _page(name: str, index: int = 0) -> pymupdf.Page:
    doc = pymupdf.open(str(PDFS / name))
    return doc[index]


@pytest.fixture(scope="module", autouse=True)
def corpus_present():
    if not PDFS.exists():
        pytest.skip("corpus not generated — run: python -m testdata.generator.build")


class TestColumnDetection:
    def test_a_two_column_article_is_detected_as_two_columns(self):
        page = _page("article_A01.pdf")
        layout = layout_page(page)
        assert layout.column_count == 2

    def test_columns_are_clustered_on_left_edges_not_midpoints(self):
        # The regression this guards: headings are narrower than paragraphs, so their
        # midpoints sit far from the column centre. On this page the left edges are cleanly
        # bimodal at 51 and 308 while the midpoints scatter from 72 to 426, and k-means on
        # midpoints fits three clusters on a two-column page (DECISIONS D-012).
        page = _page("article_A01.pdf")
        blocks = page.get_text("blocks")
        column_count, _ = cluster_columns(blocks, page.rect.width)
        assert column_count == 2

    def test_a_form_grid_is_not_read_as_columns(self):
        # A filled-in form's field grid clusters into two x-groups exactly like an article
        # does, but it must be read across each row, not down each column.
        page = _page("form_C01.pdf")
        layout = layout_page(page)
        assert layout.column_count == 1

    def test_a_scanned_page_has_no_columns_and_no_text(self):
        page = _page("scan_C01.pdf")
        layout = layout_page(page)
        assert layout.column_count == 1
        assert layout.text.strip() == ""


class TestReadingOrder:
    def test_column_aware_order_keeps_each_column_intact(self):
        page = _page("article_A01.pdf")
        layout = layout_page(page)
        text = layout.text

        # Left column, in order.
        introduction = text.index("Introduction")
        case_report = text.index("Case Report")
        discussion = text.index("Discussion")
        # Right column, after the whole left column.
        conclusion = text.index("Conclusion")
        references = text.index("References")

        assert introduction < case_report < discussion, "left column out of order"
        assert discussion < conclusion, "right column must follow the left, not interleave"
        assert conclusion < references, "right column out of order"

    def test_the_naive_order_really_does_interleave(self):
        # The comparison that makes the fix worth showing rather than merely claiming: sorting
        # top-to-bottom puts a left-column heading and a right-column heading on one line.
        page = _page("article_A01.pdf")
        naive = naive_page_text(page)

        interleaved_line = next(
            (line for line in naive.splitlines()
             if "Introduction" in line and "Conclusion" in line),
            None,
        )
        assert interleaved_line is not None, (
            "expected the naive reader to splice the two columns onto one line; "
            "if this ever stops happening the side-by-side demo needs revisiting")

        # And the correct reader must not do that.
        correct = layout_page(page).text
        assert not any(
            "Introduction" in line and "Conclusion" in line
            for line in correct.splitlines())

    def test_a_sentence_is_never_split_across_columns(self):
        page = _page("article_A03.pdf")
        # Whitespace is collapsed first because `text` keeps the source line breaks — the same
        # normalisation the evidence verifier applies before matching a quote.
        text = " ".join(layout_page(page).text.split())
        # A sentence from the left column must survive intact, not be interrupted by
        # right-column text landing in the middle of it.
        assert ("Cutaneous adverse reactions are among the most frequently reported classes "
                "of adverse drug reaction") in text


class TestSpanIndex:
    def test_every_span_maps_back_to_the_text_it_produced(self):
        layout = layout_page(_page("form_C01.pdf"))
        assert layout.spans, "a digital page must produce a span index"
        for span in layout.spans[:200]:
            # The index is only useful if the offsets are exact — this is what turns a
            # verified quote into a highlight rectangle.
            assert layout.text[span.char_start:span.char_end]
            assert span.bbox[2] > span.bbox[0]
            assert span.bbox[3] > span.bbox[1]

    def test_a_known_quote_resolves_to_a_bounding_box_on_the_page(self):
        page = _page("form_C01.pdf")
        layout = layout_page(page)

        needle = "Velmoradine"
        start = layout.text.index(needle)
        bbox = layout.bbox_for_range(start, start + len(needle))

        assert bbox is not None
        # Inside the page, and not a degenerate rectangle.
        assert 0 <= bbox[0] < bbox[2] <= page.rect.width + 1
        assert 0 <= bbox[1] < bbox[3] <= page.rect.height + 1

    def test_a_quote_that_is_not_on_the_page_resolves_to_nothing(self):
        # An honest None. The verifier will already have marked such evidence unverified;
        # inventing coordinates here would put a highlight over unrelated text.
        layout = layout_page(_page("form_C01.pdf"))
        assert layout.bbox_for_range(10 ** 7, 10 ** 7 + 20) is None

    def test_a_multi_line_quote_highlights_as_several_boxes(self):
        layout = layout_page(_page("article_A01.pdf"))
        # Take a long span that certainly wraps across lines.
        start = layout.text.index("Drug-induced liver injury")
        boxes = layout.line_boxes_for_range(start, start + 300)
        assert len(boxes) > 1, "a wrapped quote must not be one box swallowing the gutter"
        for box in boxes:
            assert box[2] > box[0] and box[3] > box[1]
