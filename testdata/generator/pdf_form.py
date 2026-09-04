"""Digital, filled-in report forms — the "normal PDF attachment" flavour.

A CIOMS-style adverse event form laid out with ReportLab: labelled fields in a grid, a
free-text narrative, and a **real table** of laboratory values so the table extraction of R5
has something genuine to find (`page.find_tables()` needs ruling lines or consistent column
geometry, and this produces both).

These pages carry a proper text layer, so the parser must classify them `DIGITAL` / `FORM`.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .cases import CaseSpec
from .fixtures import MARKETING_AUTHORISATION_HOLDER, SYNTHETIC_NOTICE

LABEL_BG = colors.HexColor("#E8EEF4")
RULE = colors.HexColor("#8A9BAA")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "FormTitle", parent=base["Heading1"], fontSize=14, leading=17, spaceAfter=2 * mm),
        "section": ParagraphStyle(
            "FormSection", parent=base["Heading2"], fontSize=10.5, leading=13,
            spaceBefore=4 * mm, spaceAfter=1.5 * mm, textColor=colors.HexColor("#1F3B54")),
        "label": ParagraphStyle(
            "FormLabel", parent=base["BodyText"], fontSize=7.5, leading=9.5,
            textColor=colors.HexColor("#40566B")),
        "value": ParagraphStyle(
            "FormValue", parent=base["BodyText"], fontSize=9, leading=11.5),
        "body": ParagraphStyle(
            "FormBody", parent=base["BodyText"], fontSize=9, leading=12.5, alignment=TA_LEFT),
        "footer": ParagraphStyle(
            "FormFooter", parent=base["BodyText"], fontSize=6.5, leading=8,
            textColor=colors.HexColor("#7A8894")),
    }


def _field_grid(pairs: list[tuple[str, str]], styles, columns: int = 2) -> Table:
    """Lay label/value pairs out as a bordered grid — the shape a real intake form has."""
    rows: list[list] = []
    row: list = []
    for label, value in pairs:
        cell = [
            Paragraph(label.upper(), styles["label"]),
            Paragraph(value if value else "—", styles["value"]),
        ]
        row.append(cell)
        if len(row) == columns:
            rows.append(row)
            row = []
    if row:
        while len(row) < columns:
            row.append([Paragraph("", styles["label"]), Paragraph("", styles["value"])])
        rows.append(row)

    # Each cell is itself a tiny two-row table so the label sits above its value.
    grid_rows = []
    for r in rows:
        grid_rows.append([
            Table([[c[0]], [c[1]]], colWidths=[None], style=TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            for c in r
        ])

    width = (A4[0] - 40 * mm) / columns
    table = Table(grid_rows, colWidths=[width] * columns)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def lab_table(styles, abnormal_flag: str = "H") -> Table:
    """A laboratory-results table. R5 wants tables extracted as rows and columns, not prose."""
    header = ["Test", "Result", "Units", "Reference range", "Flag"]
    data = [
        ["Alanine aminotransferase", "640", "U/L", "10 – 40", abnormal_flag],
        ["Aspartate aminotransferase", "412", "U/L", "10 – 35", abnormal_flag],
        ["Total bilirubin", "78", "umol/L", "3 – 21", abnormal_flag],
        ["Alkaline phosphatase", "196", "U/L", "30 – 130", abnormal_flag],
        ["Serum creatinine", "94", "umol/L", "60 – 110", ""],
        ["Neutrophil count", "3.8", "x10^9/L", "2.0 – 7.5", ""],
        ["Haemoglobin", "131", "g/L", "120 – 160", ""],
        ["C-reactive protein", "42", "mg/L", "< 5", abnormal_flag],
    ]
    cell = ParagraphStyle("Cell", parent=styles["value"], fontSize=8, leading=10)
    head = ParagraphStyle("Head", parent=styles["value"], fontSize=8, leading=10,
                          textColor=colors.white)

    rows = [[Paragraph(h, head) for h in header]]
    rows += [[Paragraph(str(c), cell) for c in row] for row in data]

    table = Table(rows, colWidths=[55 * mm, 20 * mm, 20 * mm, 35 * mm, 15 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B54")),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def form_flowables(
    case: CaseSpec,
    styles,
    *,
    reference: str,
    report_date: str,
    include_lab_table: bool,
    language: str = "en",
    labels_language: str | None = None,
) -> list:
    """Build the flowables for one form. Split out so the hybrid PDF can reuse it."""
    from .i18n import form_labels

    lang_for_labels = labels_language or language
    L = form_labels(lang_for_labels)
    V = form_labels(language)

    story: list = []
    story.append(Paragraph(L["title"], styles["title"]))
    story.append(Paragraph(
        f"{L['mah']}: {MARKETING_AUTHORISATION_HOLDER}", styles["label"]))
    story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(L["s_admin"], styles["section"]))
    story.append(_field_grid([
        (L["reference"], reference),
        (L["report_date"], report_date),
        (L["report_type"], V["report_type_spontaneous"]),
        (L["seriousness"], V["serious_yes"] if case.is_serious() else V["serious_no"]),
    ], styles))

    patient = case.patient
    story.append(Paragraph(L["s_patient"], styles["section"]))
    story.append(_field_grid([
        (L["initials"], patient.initials if patient else ""),
        (L["age"], patient.age_raw if patient else ""),
        (L["sex"], V.get(f"sex_{patient.sex.lower()}", patient.sex) if patient and patient.sex else ""),
        (L["weight"], patient.weight if patient and patient.weight else ""),
        (L["history"], patient.medical_history if patient else ""),
    ], styles))

    reporter = case.reporter
    story.append(Paragraph(L["s_reporter"], styles["section"]))
    story.append(_field_grid([
        (L["reporter_name"], reporter.name if reporter else ""),
        (L["qualification"], reporter.qualification if reporter else ""),
        (L["organisation"], reporter.organisation if reporter else ""),
        (L["country"], reporter.country if reporter else ""),
    ], styles))

    story.append(Paragraph(L["s_product"], styles["section"]))
    product_rows: list[tuple[str, str]] = []
    for i, drug in enumerate(case.drugs, start=1):
        prefix = f"{i}. " if len(case.drugs) > 1 else ""
        product_rows.append((f"{prefix}{L['product_name']}", drug.product.name))
        product_rows.append((f"{prefix}{L['dose']}",
                             f"{drug.dose_amount or ''} {drug.dose_unit or ''} "
                             f"{drug.frequency or ''}".strip()))
        product_rows.append((f"{prefix}{L['route']}", drug.route or drug.product.route))
        product_rows.append((f"{prefix}{L['batch']}", drug.batch or ""))
        product_rows.append((f"{prefix}{L['start_date']}", drug.start_date_raw or ""))
        product_rows.append((f"{prefix}{L['role']}",
                             V["role_suspect"] if drug.role == "SUSPECT" else V["role_concomitant"]))
    if product_rows:
        story.append(_field_grid(product_rows, styles))

    story.append(Paragraph(L["s_reaction"], styles["section"]))
    reaction_rows: list[tuple[str, str]] = []
    for i, event in enumerate(case.reactions, start=1):
        prefix = f"{i}. " if len(case.reactions) > 1 else ""
        reaction_rows.append((f"{prefix}{L['reaction_term']}", event.reaction.term))
        reaction_rows.append((f"{prefix}{L['onset']}", event.onset_raw or ""))
        reaction_rows.append((f"{prefix}{L['outcome']}",
                              V.get(f"outcome_{(event.outcome or '').lower()}", event.outcome or "")))
        reaction_rows.append((f"{prefix}{L['serious_criteria']}",
                              ", ".join(event.serious_criteria) if event.serious_criteria
                              else V["serious_no"]))
    if reaction_rows:
        story.append(_field_grid(reaction_rows, styles))

    if case.narrative:
        story.append(Paragraph(L["s_narrative"], styles["section"]))
        story.append(Paragraph(case.narrative, styles["body"]))

    if include_lab_table:
        story.append(Paragraph(L["s_labs"], styles["section"]))
        story.append(KeepTogether(lab_table(styles)))

    return story


def build_form_pdf(
    path: Path,
    case: CaseSpec,
    *,
    reference: str,
    report_date: str,
    include_lab_table: bool = False,
    language: str = "en",
    labels_language: str | None = None,
    font_name: str | None = None,
) -> Path:
    """Render one filled-in report form to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()

    if font_name:
        for key in ("title", "section", "label", "value", "body", "footer"):
            styles[key].fontName = font_name

    doc = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=16 * mm,
        title=f"Adverse Event Report {reference}",
        author="Smart Inbox synthetic corpus generator",
        subject=SYNTHETIC_NOTICE,
    )

    story = form_flowables(
        case, styles,
        reference=reference,
        report_date=report_date,
        include_lab_table=include_lab_table,
        language=language,
        labels_language=labels_language,
    )
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(SYNTHETIC_NOTICE, styles["footer"]))

    doc.build(story)
    return path


def build_multipage_form_pdf(
    path: Path,
    case: CaseSpec,
    *,
    reference: str,
    report_date: str,
    continuation_pages: int = 1,
) -> Path:
    """A form long enough to span pages, with the lab table continuing across the break (E18).

    The continuation repeats the header row, which is exactly the signal the cross-page table
    merge looks for.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=16 * mm,
        title=f"Adverse Event Report {reference}",
        author="Smart Inbox synthetic corpus generator",
        subject=SYNTHETIC_NOTICE,
    )

    story = form_flowables(
        case, styles, reference=reference, report_date=report_date, include_lab_table=True)

    for n in range(continuation_pages):
        story.append(PageBreak())
        story.append(Paragraph(
            f"Laboratory results (continued, sheet {n + 2})", styles["section"]))
        story.append(lab_table(styles, abnormal_flag="H"))
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(
            "Follow-up information was requested from the reporter on the date shown above. "
            "No further laboratory data was available at the time of this report.",
            styles["body"]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(SYNTHETIC_NOTICE, styles["footer"]))
    doc.build(story)
    return path
