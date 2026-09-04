"""Heading segmentation, and the exclusion that stops the model inventing patients (E15).

The brief says to "ignore references and general discussion" in articles. That instruction is
doing more work than it appears. A journal reference list looks like this:

    Ashworth PL, Meraldi K. Hepatic injury in a 71-year-old woman following prolonged
    therapy. J Synth Pharmacovig. 2021;14(3):118-124.

Give that to an extraction prompt and it will confidently report a 71-year-old female patient
who does not exist in the article — a fabricated case in a regulated system. It is one of the
most reliable failure modes in this whole domain, and it is why the corpus articles all carry
a References section whose entries name ages and sexes: the trap is deliberate.

So sections are segmented by heading, and `References`, `Bibliography`, `Acknowledgements`,
`Conflict of interest` and `Funding` are marked `excluded_from_case=True`. They are still
**stored**, and still fed to the *summary* prompt — a summary that omits the fact that a paper
has 40 references is a worse summary. They are withheld only from extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.pdf.layout import PageLayout, TextBlock

# Section kinds, and the patterns that identify each heading. Ordered: the first match wins,
# so more specific patterns come first.
SECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("REFERENCES", r"^\s*(references?|bibliograph(y|ie)|literatur(verzeichnis)?|"
                   r"références|参考文献|reference list)\s*$"),
    ("ACKNOWLEDGEMENTS", r"^\s*(acknowledge?ments?|danksagung|remerciements|謝辞)\s*$"),
    ("CONFLICT_OF_INTEREST", r"^\s*(conflicts?\s+of\s+interest|competing\s+interests?|"
                             r"declaration\s+of\s+interest|interessenkonflikt|"
                             r"conflits?\s+d'intérêts?|利益相反)\s*$"),
    ("FUNDING", r"^\s*(funding( (statement|sources?))?|financial\s+support|"
                r"finanzierung|financement)\s*$"),
    ("ABSTRACT", r"^\s*(abstract|summary|zusammenfassung|résumé|要旨|抄録)\s*$"),
    ("INTRODUCTION", r"^\s*(introduction|background|einleitung|hintergrund|"
                     r"introduction|序論|緒言)\s*$"),
    ("METHODS", r"^\s*(materials?\s+and\s+methods?|methods?|methodology|methoden|"
                r"méthodes?|方法)\s*$"),
    ("CASE_REPORT", r"^\s*(case\s+(report|presentation|description|series|summary)|"
                    r"case\s+\d+|patient\s+[a-z0-9]+|fallbericht|fallserie|"
                    r"présentation\s+du\s+cas|症例|症例報告)\s*[:.]?\s*$"),
    ("RESULTS", r"^\s*(results?|findings|ergebnisse|résultats|結果)\s*$"),
    ("DISCUSSION", r"^\s*(discussions?|diskussion|考察)\s*$"),
    ("CONCLUSION", r"^\s*(conclusions?|schlussfolgerung(en)?|conclusion|結論)\s*$"),
    ("KEYWORDS", r"^\s*(key\s?words?|schlüsselwörter|mots-clés|キーワード)\s*[:.]?\s*$"),
)

# The sections a case must never be extracted from. Each is here for a specific reason:
# references cite other patients, acknowledgements name people who are not reporters, and
# conflict-of-interest statements name products that are not suspect drugs.
EXCLUDED_KINDS = frozenset({
    "REFERENCES", "ACKNOWLEDGEMENTS", "CONFLICT_OF_INTEREST", "FUNDING",
})

# Lettered/numbered form sections: "A. Administrative information", "Section 3 - Patient".
FORM_SECTION_PATTERN = re.compile(
    r"^\s*(?:section\s+)?(?:[A-H]|\d{1,2})[.)]\s+\S.{0,80}$", re.IGNORECASE)


@dataclass
class Section:
    """One segmented section of a document."""

    page_no: int
    section_index: int
    heading: str
    kind: str
    char_start: int
    char_end: int
    excluded_from_case: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "page_no": self.page_no,
            "section_index": self.section_index,
            "heading": self.heading,
            "section_kind": self.kind,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "excluded_from_case": self.excluded_from_case,
        }


def classify_heading(text: str) -> str | None:
    """The section kind a heading denotes, or `None` when it is not a heading we know."""
    candidate = text.strip().rstrip(":.").strip()
    if not candidate or len(candidate) > 90:
        return None
    for kind, pattern in SECTION_PATTERNS:
        if re.match(pattern, candidate, re.IGNORECASE | re.UNICODE):
            return kind
    return None


def looks_like_heading(block: TextBlock, body_font_size: float) -> bool:
    """Typographic test, used only as corroboration.

    A heading is short, and is either bold or noticeably larger than the body text. This never
    decides a section kind on its own — it only stops a sentence that happens to begin with
    the word "Discussion" from being treated as a heading.
    """
    text = block.text.strip()
    if not text or len(text) > 90 or text.count("\n") > 1:
        return False
    return block.any_bold or block.max_font_size >= body_font_size * 1.12


def segment_page(
    layout: PageLayout,
    page_no: int,
    *,
    genre: str,
    start_index: int = 0,
) -> list[Section]:
    """Segment one page's text into sections."""
    if not layout.blocks:
        return []

    sizes = sorted(b.max_font_size for b in layout.blocks if b.text.strip())
    body_font_size = sizes[len(sizes) // 2] if sizes else 10.0

    # Rebuild each block's character span in the same order layout.py concatenated them, so
    # section offsets index into exactly the text we stored as `text_original`.
    spans_by_block: dict[int, tuple[int, int]] = {}
    for span in layout.spans:
        start, end = spans_by_block.get(span.block_index, (span.char_start, span.char_end))
        spans_by_block[span.block_index] = (
            min(start, span.char_start), max(end, span.char_end))

    ordered = [b for b in layout.blocks if b.index in spans_by_block]
    if not ordered:
        return []

    sections: list[Section] = []
    current_heading = "(document start)"
    current_kind = "TITLE" if page_no == 1 else "OTHER"
    current_start = spans_by_block[ordered[0].index][0]
    index = start_index

    for block in ordered:
        block_start, block_end = spans_by_block[block.index]
        kind = classify_heading(block.text)

        is_heading = kind is not None and (
            looks_like_heading(block, body_font_size) or len(block.text.strip()) <= 40)

        if not is_heading and genre == "FORM" and FORM_SECTION_PATTERN.match(block.text.strip()):
            kind, is_heading = "FORM_FIELDS", True

        if is_heading and block_start > current_start:
            sections.append(Section(
                page_no=page_no,
                section_index=index,
                heading=current_heading,
                kind=current_kind,
                char_start=current_start,
                char_end=block_start,
                excluded_from_case=current_kind in EXCLUDED_KINDS,
            ))
            index += 1
            current_heading = block.text.strip()
            current_kind = kind or "OTHER"
            current_start = block_start

    last_end = spans_by_block[ordered[-1].index][1]
    sections.append(Section(
        page_no=page_no,
        section_index=index,
        heading=current_heading,
        kind=current_kind,
        char_start=current_start,
        char_end=last_end,
        excluded_from_case=current_kind in EXCLUDED_KINDS,
    ))
    return sections


def segment_document(
    layouts: Sequence[tuple[int, PageLayout, str]],
) -> list[Section]:
    """Segment every page, carrying the current section across page breaks.

    Carrying matters: a References list that starts on page 2 and continues onto page 3 must
    stay excluded on page 3, where there is no heading to re-trigger the rule.
    """
    all_sections: list[Section] = []
    index = 0
    carried_heading: str | None = None
    carried_kind: str | None = None

    for page_no, layout, genre in layouts:
        page_sections = segment_page(layout, page_no, genre=genre, start_index=index)
        if not page_sections:
            continue

        # The first section of a continuation page has no heading of its own; inherit the
        # section that was open when the previous page ended.
        if carried_kind and page_sections[0].heading == "(document start)":
            page_sections[0].heading = carried_heading or page_sections[0].heading
            page_sections[0].kind = carried_kind
            page_sections[0].excluded_from_case = carried_kind in EXCLUDED_KINDS
        elif carried_kind and page_sections[0].kind == "OTHER":
            page_sections[0].heading = f"{carried_heading} (continued)"
            page_sections[0].kind = carried_kind
            page_sections[0].excluded_from_case = carried_kind in EXCLUDED_KINDS

        all_sections.extend(page_sections)
        index = page_sections[-1].section_index + 1
        carried_heading = page_sections[-1].heading
        carried_kind = page_sections[-1].kind

    return all_sections


def extractable_ranges(sections: Sequence[Section], page_no: int) -> list[tuple[int, int]]:
    """Character ranges on `page_no` that extraction is allowed to read (E15)."""
    return [
        (s.char_start, s.char_end)
        for s in sections
        if s.page_no == page_no and not s.excluded_from_case
    ]


def redact_excluded(text: str, sections: Sequence[Section], page_no: int) -> str:
    """The page text with excluded sections removed, for the extraction prompt.

    Replaced by a marker rather than deleted silently, so the model can see that something was
    withheld and does not treat the remaining text as the whole document.
    """
    ranges = [
        (s.char_start, s.char_end, s.heading)
        for s in sections
        if s.page_no == page_no and s.excluded_from_case
    ]
    if not ranges:
        return text

    result: list[str] = []
    cursor = 0
    for start, end, heading in sorted(ranges):
        start = max(start, 0)
        end = min(end, len(text))
        if start > cursor:
            result.append(text[cursor:start])
        result.append(f"\n[section '{heading}' withheld from case extraction]\n")
        cursor = max(cursor, end)
    result.append(text[cursor:])
    return "".join(result)
