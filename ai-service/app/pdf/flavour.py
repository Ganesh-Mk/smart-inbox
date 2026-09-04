"""Per-page flavour detection — the most important structural decision in the PDF layer.

The brief lists four "flavours" (normal digital, scanned/handwritten, published article,
non-English) and implies one label per document. That is wrong for real submissions, and
saying so is the point of E12: a genuine safety attachment is a typed cover letter *plus* a
scanned annex, and a non-English document is *also* digital or scanned. Forcing one label loses
information the very next step needs.

So flavour is two orthogonal axes plus an attribute, decided **per page**:

* ``rendering`` — ``DIGITAL`` | ``SCANNED``. Decides whether this page can be read from its
  text layer or has to go to vision.
* ``genre`` — ``FORM`` | ``ARTICLE`` | ``LETTER``. Decides which prompt and which section
  rules apply.
* ``language`` — an attribute, not a flavour, because it is orthogonal to both (E16/E17).

The document-level values are a roll-up, and ``MIXED`` is a legal answer. That is what lets one
document exercise several of the brief's four handling paths at once, and it is what the hybrid
corpus file is there to prove.

**Detecting "scanned" (E13).** The naive rule — "no extractable text" — fails on scanners that
embed garbage OCR text, and those are common. The rule here is a composite, and any hit routes
the page to vision. It errs deliberately toward vision, which is safe: vision on a digital page
still works, whereas trusting a garbage text layer produces confident nonsense.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence

import pymupdf

from app.lang.detect import PageLanguage, detect_page
from app.pdf.layout import PageLayout
from app.pdf.render import page_image_stats
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pdf.flavour")

RENDERING_DIGITAL = "DIGITAL"
RENDERING_SCANNED = "SCANNED"
RENDERING_MIXED = "MIXED"

GENRE_FORM = "FORM"
GENRE_ARTICLE = "ARTICLE"
GENRE_LETTER = "LETTER"
GENRE_MIXED = "MIXED"

# Markers that identify a published article regardless of column count — a single-column
# preprint is still an article, and a two-column newsletter is not.
ARTICLE_MARKERS = (
    r"\babstract\b", r"\bdoi\s*:", r"\breferences\b", r"\bbibliography\b",
    r"\bkeywords\b", r"\bintroduction\b", r"\bdiscussion\b", r"\bcase report\b",
    r"\bcase series\b", r"\bconflict of interest\b", r"\backnowledge?ments\b",
    r"\bcorresponding author\b", r"\bmethods\b", r"\bconclusion\b",
    # non-English equivalents, so a German article is not mislabelled a letter
    r"\bzusammenfassung\b", r"\bliteratur\b", r"\bschlussfolgerung\b",
    r"\brésumé\b", r"\bréférences\b", r"\bmots-clés\b",
    r"要旨", r"参考文献", r"考察",
)

# A filled-in form is dense with short "Label: value" pairs; prose is not.
FORM_LABEL_PATTERN = re.compile(
    r"(?m)^[^\n]{0,60}?[:：]\s*\S", re.UNICODE)

FORM_TITLE_MARKERS = (
    r"\breport form\b", r"\badverse event report\b", r"\bcioms\b",
    r"\bpatient (initials|details)\b", r"\breporter details\b",
    r"\bbatch\s*/?\s*lot\b", r"\bmeldebogen\b", r"\bformulaire de déclaration\b",
    r"報告書",
)


@dataclass
class PageFlavour:
    """One page's verdict."""

    page_no: int
    rendering: str
    genre: str
    language: str | None
    lang_confidence: float
    languages: list[str]
    column_count: int
    char_count: int
    has_text_layer: bool
    image_area_ratio: float
    image_count: int
    printable_ratio: float
    scanned_reason: str | None
    genre_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "page_no": self.page_no,
            "rendering": self.rendering,
            "genre": self.genre,
            "language": self.language,
            "lang_confidence": round(self.lang_confidence, 4),
            "languages": self.languages,
            "column_count": self.column_count,
            "char_count": self.char_count,
            "has_text_layer": self.has_text_layer,
            "image_count": self.image_count,
            "scanned_reason": self.scanned_reason,
            "genre_reason": self.genre_reason,
        }


def printable_ratio(text: str) -> float:
    """Fraction of characters that are plausible text.

    Guards against the scanner that embeds junk OCR: a page whose "text layer" is largely
    control characters, replacement characters or private-use codepoints has no usable text,
    however many characters it reports.
    """
    if not text:
        return 0.0
    good = 0
    for ch in text:
        if ch.isspace():
            good += 1
            continue
        category = unicodedata.category(ch)
        # L* letters, N* numbers, P* punctuation, S* symbols, Zs space separators.
        if category[0] in ("L", "N", "P", "S") or category == "Zs":
            good += 1
    return good / len(text)


def detect_page_flavour(
    page: pymupdf.Page,
    layout: PageLayout,
    page_no: int,
    settings: Settings | None = None,
) -> PageFlavour:
    """Classify one page on both axes and attach its language."""
    settings = settings or get_settings()

    text = layout.text
    stripped = text.strip()
    char_count = len(stripped)
    image_area, image_count = page_image_stats(page)
    ratio = printable_ratio(stripped) if stripped else 0.0

    # --- E13: is this page a picture of a document rather than a document? ---
    scanned_reason: str | None = None
    if char_count < settings.scanned_min_chars:
        scanned_reason = (
            f"only {char_count} extractable characters "
            f"(threshold {settings.scanned_min_chars})")
    elif image_area > settings.scanned_image_area_ratio:
        scanned_reason = (
            f"a single image covers {image_area:.0%} of the page "
            f"(threshold {settings.scanned_image_area_ratio:.0%})")
    elif ratio < settings.scanned_printable_ratio:
        scanned_reason = (
            f"only {ratio:.0%} of the text layer is printable characters — "
            "likely embedded junk OCR")

    rendering = RENDERING_SCANNED if scanned_reason else RENDERING_DIGITAL

    # --- genre ---
    genre, genre_reason = _detect_genre(stripped, layout, settings)

    # --- language (E17) — only meaningful when there is a text layer to read ---
    if rendering == RENDERING_DIGITAL:
        page_language = detect_page([b.text for b in layout.blocks], settings)
    else:
        # No usable text: the language is whatever the vision transcription later reports.
        # Asserting one now would be a guess, and a guess here silently drives translation.
        page_language = PageLanguage(None, 0.0, [], False, [])

    return PageFlavour(
        page_no=page_no,
        rendering=rendering,
        genre=genre,
        language=page_language.primary_language,
        lang_confidence=page_language.confidence,
        languages=page_language.languages,
        column_count=layout.column_count,
        char_count=char_count,
        has_text_layer=char_count > 0,
        image_area_ratio=image_area,
        image_count=image_count,
        printable_ratio=ratio,
        scanned_reason=scanned_reason,
        genre_reason=genre_reason,
    )


def _detect_genre(text: str, layout: PageLayout, settings: Settings) -> tuple[str, str]:
    """ARTICLE / FORM / LETTER, with the reason recorded for the audit trail."""
    lowered = text.lower()

    article_hits = [m for m in ARTICLE_MARKERS if re.search(m, lowered, re.UNICODE)]
    form_hits = [m for m in FORM_TITLE_MARKERS if re.search(m, lowered, re.UNICODE)]

    label_lines = len(FORM_LABEL_PATTERN.findall(text))
    total_lines = max(text.count("\n") + 1, 1)
    label_density = label_lines / total_lines

    # A form is recognised by its title or by a high density of "Label: value" lines. Checked
    # first because a form can legitimately contain the word "Conclusion" or "Methods" and
    # would otherwise be pulled toward ARTICLE by a single marker.
    if form_hits and label_density > 0.20:
        return GENRE_FORM, (
            f"form title marker {form_hits[0]!r} with {label_density:.0%} label-style lines")
    if label_density > 0.45 and len(article_hits) < 3:
        return GENRE_FORM, f"{label_density:.0%} of lines are 'Label: value' pairs"

    # Two or more columns is a strong article signal, but only with corroboration — a
    # two-column newsletter is not a paper.
    if layout.column_count >= 2 and len(article_hits) >= 2:
        return GENRE_ARTICLE, (
            f"{layout.column_count} columns plus markers {article_hits[:3]}")
    if len(article_hits) >= 4:
        return GENRE_ARTICLE, f"article markers {article_hits[:4]}"
    if layout.column_count >= 2 and len(article_hits) >= 1:
        return GENRE_ARTICLE, (
            f"{layout.column_count} columns plus marker {article_hits[0]!r}")

    if form_hits:
        return GENRE_FORM, f"form title marker {form_hits[0]!r}"

    # Correspondence is the residual category, and it is the right residual: an email body or
    # a covering letter has neither a form's field density nor an article's apparatus.
    return GENRE_LETTER, "no form or article signals; treated as correspondence"


def roll_up(flavours: Sequence[PageFlavour]) -> tuple[str, str]:
    """Document-level `(rendering, genre)`. MIXED is a legal, meaningful answer (E12)."""
    if not flavours:
        return RENDERING_DIGITAL, GENRE_LETTER

    renderings = {f.rendering for f in flavours}
    rendering = renderings.pop() if len(renderings) == 1 else RENDERING_MIXED

    # Genre is rolled up by weight of content, not by page count: an article's one-page
    # covering letter should not turn the document into correspondence. But a genuine mix —
    # a form plus a scanned annex of a different genre — is reported as MIXED.
    weights: dict[str, int] = {}
    for flavour in flavours:
        weights[flavour.genre] = weights.get(flavour.genre, 0) + max(flavour.char_count, 1)

    if len(weights) == 1:
        genre = next(iter(weights))
    else:
        total = sum(weights.values())
        dominant = max(weights, key=lambda k: weights[k])
        # A clear majority wins; a real split is reported honestly as MIXED.
        genre = dominant if weights[dominant] / total >= 0.70 else GENRE_MIXED

    return rendering, genre
