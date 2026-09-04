"""Published-article PDFs — two-column, with the section structure a journal case report has.

Two edge cases live or die on this file:

* **E14 — reading order.** The body is laid out in genuine two-column frames, so a naive
  `get_text()` interleaves the columns into nonsense and the column-aware reader does not.
  That contrast is a side-by-side demo in the walkthrough, which is only possible if the
  corpus actually contains real two-column pages.

* **E15 — excluded sections.** Every article ends with a References list whose entries name
  ages and sexes ("a 71-year-old woman"), plus Acknowledgements, Funding and Conflict of
  interest. These are the trap: a model that reads them will happily manufacture a patient out
  of a citation. Section segmentation must mark them `excluded_from_case`.

Two of the articles are **case series** carrying two and three distinct patients, which is what
the literature-screening bonus has to split apart (E32).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
)

from .cases import CaseSpec
from .fixtures import SYNTHETIC_NOTICE

PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
GUTTER = 7 * mm


@dataclass
class ArticleSpec:
    """One article: its metadata, the cases inside it, and whether it is a case report."""

    article_id: str
    title: str
    authors: str
    journal: str
    year: int
    doi: str
    keywords: list[str]
    cases: list[CaseSpec]
    is_case_report: bool
    abstract: str
    introduction: str
    discussion: str
    conclusion: str
    # A review or a trial write-up is deliberately *not* a case report — the negative examples
    # that stop the screening prompt saying "yes" to every PDF it is shown.
    article_kind: str = "CASE_REPORT"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("ArtTitle", parent=base["Title"], fontSize=15, leading=18,
                                alignment=TA_CENTER, spaceAfter=3 * mm),
        "authors": ParagraphStyle("ArtAuthors", parent=base["BodyText"], fontSize=9.5,
                                  leading=12, alignment=TA_CENTER, spaceAfter=1.5 * mm),
        "meta": ParagraphStyle("ArtMeta", parent=base["BodyText"], fontSize=8, leading=10,
                               alignment=TA_CENTER, textColor=colors.HexColor("#5A6B7A"),
                               spaceAfter=4 * mm),
        "h": ParagraphStyle("ArtHeading", parent=base["Heading2"], fontSize=10.5, leading=13,
                            spaceBefore=3.5 * mm, spaceAfter=1.5 * mm,
                            textColor=colors.HexColor("#1F3B54")),
        "h2": ParagraphStyle("ArtSubHeading", parent=base["Heading3"], fontSize=9.5, leading=12,
                             spaceBefore=2.5 * mm, spaceAfter=1 * mm),
        "body": ParagraphStyle("ArtBody", parent=base["BodyText"], fontSize=8.6, leading=11.4,
                               alignment=TA_JUSTIFY, spaceAfter=1.8 * mm),
        "ref": ParagraphStyle("ArtRef", parent=base["BodyText"], fontSize=7.6, leading=9.6,
                              spaceAfter=1 * mm, leftIndent=4 * mm, firstLineIndent=-4 * mm),
        "footer": ParagraphStyle("ArtFooter", parent=base["BodyText"], fontSize=6.5, leading=8,
                                 textColor=colors.HexColor("#7A8894")),
    }


def _document(path: Path, spec: ArticleSpec) -> BaseDocTemplate:
    """First page: a full-width banner for title/abstract, then two columns. Later pages: two
    columns throughout. This is the layout an actual journal uses, and it means page 1 has a
    *different* column count from page 2 — which the per-page flavour detector must handle."""
    doc = BaseDocTemplate(
        str(path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN,
        title=spec.title, author=spec.authors, subject=SYNTHETIC_NOTICE,
    )

    usable_w = PAGE_W - 2 * MARGIN
    col_w = (usable_w - GUTTER) / 2
    banner_h = 62 * mm

    banner = Frame(MARGIN, PAGE_H - MARGIN - banner_h, usable_w, banner_h,
                   id="banner", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    body_h_first = PAGE_H - 2 * MARGIN - banner_h - 4 * mm
    first_left = Frame(MARGIN, MARGIN, col_w, body_h_first, id="fl",
                       leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    first_right = Frame(MARGIN + col_w + GUTTER, MARGIN, col_w, body_h_first, id="fr",
                        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    body_h = PAGE_H - 2 * MARGIN
    left = Frame(MARGIN, MARGIN, col_w, body_h, id="l",
                 leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    right = Frame(MARGIN + col_w + GUTTER, MARGIN, col_w, body_h, id="r",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)

    def draw_rule(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#C9D4DE"))
        canvas.setLineWidth(0.4)
        x = MARGIN + col_w + GUTTER / 2
        canvas.line(x, MARGIN, x, PAGE_H - MARGIN)
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(colors.HexColor("#8A9BAA"))
        canvas.drawCentredString(PAGE_W / 2, MARGIN - 6, SYNTHETIC_NOTICE)
        canvas.restoreState()

    doc.addPageTemplates([
        PageTemplate(id="First", frames=[banner, first_left, first_right], onPage=draw_rule),
        PageTemplate(id="Rest", frames=[left, right], onPage=draw_rule),
    ])
    return doc


def _case_narrative(case: CaseSpec, index: int, total: int, styles) -> list:
    """One patient's story, headed so that a case boundary is findable from structure (E32)."""
    out: list = []
    if total > 1:
        out.append(Paragraph(f"Case {index + 1}", styles["h2"]))

    p = case.patient
    drug = case.drugs[0] if case.drugs else None
    event = case.reactions[0] if case.reactions else None

    intro = (
        f"We report {p.descriptor} with a history of {p.medical_history}, who was referred to "
        f"our department for assessment."
    ) if p else "We report a patient referred to our department for assessment."
    out.append(Paragraph(intro, styles["body"]))

    if drug:
        out.append(Paragraph(
            f"The patient had been prescribed {drug.product.name} "
            f"{drug.dose_amount or ''} {drug.dose_unit or ''} {drug.frequency or ''} "
            f"({drug.product.inn}, {drug.product.form}) for {drug.product.indication}, "
            f"commencing {drug.start_date_raw or 'at an unspecified date'}. "
            f"The route of administration was {(drug.route or drug.product.route).lower()}.",
            styles["body"]))

    if event:
        out.append(Paragraph(
            f"{(p.descriptor[0].upper() + p.descriptor[1:]) if p else 'The patient'} developed "
            f"{event.reaction.description}"
            f"{', beginning ' + event.onset_raw if event.onset_raw else ''}. "
            f"The reported term was {event.reaction.term}.",
            styles["body"]))
        if event.serious_criteria:
            out.append(Paragraph(
                "The event met the regulatory criteria for a serious adverse reaction "
                f"({', '.join(c.replace('_', ' ').lower() for c in event.serious_criteria)}).",
                styles["body"]))
        out.append(Paragraph(
            f"The suspected medicine was withdrawn. At the time of writing the outcome was "
            f"recorded as {(event.outcome or 'unknown').replace('_', ' ').lower()}.",
            styles["body"]))

    if case.narrative:
        out.append(Paragraph(case.narrative, styles["body"]))
    return out


# The references are the E15 trap: every entry names an age and a sex, so a model that reads
# this section will invent patients that the article never reported.
_REFERENCE_TEMPLATES = (
    "Ashworth PL, Meraldi K. Hepatic injury in a 71-year-old woman following prolonged therapy. "
    "J Synth Pharmacovig. 2021;14(3):118–124.",
    "Okonjo B, Prasetyo H, Lund V. Angioedema in a 46-year-old male: a case for early "
    "recognition. Ann Fict Clin Med. 2019;7(2):55–61.",
    "Marchetti R, Halloran S. Cutaneous adverse reactions: a retrospective series of 212 "
    "patients. Rev Imaginary Dermatol. 2022;31(4):402–415.",
    "Vasquez-Byrne T. Neutropenia in an 8-year-old boy after a single dose. "
    "Case Rep Invent Paediatr. 2020;3:e0142.",
    "Ferreira D, Nakamura Y, Osei-Bonsu A. Signal detection methodology in spontaneous "
    "reporting systems. J Synth Pharmacovig. 2023;16(1):9–27.",
    "Lindqvist G, Berhane T. Photosensitivity: a 29-year-old pregnant patient. "
    "Ann Fict Clin Med. 2018;6(4):233–236.",
)


def build_article_pdf(path: Path, spec: ArticleSpec) -> Path:
    """Render one two-column article to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    doc = _document(path, spec)

    story: list = []

    # --- banner frame: title block and abstract, full width ---
    story.append(Paragraph(spec.title, styles["title"]))
    story.append(Paragraph(spec.authors, styles["authors"]))
    story.append(Paragraph(
        f"{spec.journal} {spec.year};  doi:{spec.doi}", styles["meta"]))
    story.append(Paragraph("Abstract", styles["h"]))
    story.append(Paragraph(spec.abstract, styles["body"]))
    story.append(Paragraph(
        "<b>Keywords:</b> " + ", ".join(spec.keywords), styles["body"]))

    # --- move into the two-column frames ---
    story.append(NextPageTemplate("Rest"))
    story.append(Spacer(1, 0.1))

    story.append(Paragraph("Introduction", styles["h"]))
    story.append(Paragraph(spec.introduction, styles["body"]))

    if spec.cases:
        heading = "Case Series" if len(spec.cases) > 1 else "Case Report"
        story.append(Paragraph(heading, styles["h"]))
        for i, case in enumerate(spec.cases):
            story.extend(_case_narrative(case, i, len(spec.cases), styles))

    story.append(Paragraph("Discussion", styles["h"]))
    story.append(Paragraph(spec.discussion, styles["body"]))

    story.append(Paragraph("Conclusion", styles["h"]))
    story.append(Paragraph(spec.conclusion, styles["body"]))

    # --- E15: everything below here must be excluded from case extraction ---
    story.append(Paragraph("Acknowledgements", styles["h"]))
    story.append(Paragraph(
        "The authors thank the clinical pharmacy team at the participating centre for their "
        "assistance with record retrieval.", styles["body"]))

    story.append(Paragraph("Funding", styles["h"]))
    story.append(Paragraph(
        "This work received no specific grant from any funding agency in the public, "
        "commercial or not-for-profit sectors.", styles["body"]))

    story.append(Paragraph("Conflict of interest", styles["h"]))
    story.append(Paragraph(
        "The authors declare that they have no competing interests.", styles["body"]))

    story.append(Paragraph("References", styles["h"]))
    for i, ref in enumerate(_REFERENCE_TEMPLATES, start=1):
        story.append(Paragraph(f"{i}. {ref}", styles["ref"]))

    doc.build(story)
    return path
