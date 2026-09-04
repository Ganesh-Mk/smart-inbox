"""The corpus itself: which documents exist, which emails carry them, and what the truth is.

Read this file as the answer to "how do you know the edge cases are actually handled?" — each
entry names the edge case it exists for, and `manifest.json` rolls that up so the coverage claim
in the write-up is checkable rather than asserted.
"""

from __future__ import annotations

import json
import random
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .cases import CaseSpec
from .fixtures import SAFETY_MAILBOX, SYNTHETIC_NOTICE
from .messages import (
    AttachmentSpec,
    MessageSpec,
    build_eml,
    html_version,
    original_message_reply,
    quoted_reply,
)
from .pdf_article import ArticleSpec, build_article_pdf
from .pdf_form import build_form_pdf, build_multipage_form_pdf
from .pdf_scanned import ScanStyle, build_scanned_pdf, handwritten_case_text
from .pdf_special import (
    build_company_logo,
    build_corrupt_pdf,
    build_defect_photo,
    build_encrypted_pdf,
    build_hybrid_pdf,
)


def _at(base: datetime, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return (base + timedelta(days=day)).replace(hour=hour, minute=minute)


# =======================================================================================
# PDFs
# =======================================================================================

def build_pdfs(cases: dict[str, CaseSpec], articles: list[ArticleSpec],
               pdf_dir: Path, asset_dir: Path) -> dict[str, Path]:
    """Render every PDF and image the corpus needs. Returns a name -> path map."""
    out: dict[str, Path] = {}

    # --- digital forms (the "normal PDF attachment" flavour) ---
    form_specs = [
        ("form_C01", cases["C01"], "AER-2026-00141", "14 March 2026", False),
        ("form_C02", cases["C02"], "AER-2026-00188", "6 May 2026", True),
        ("form_C03", cases["C03"], "AER-2026-00203", "9 February 2026", False),
        ("form_C07", cases["C07"], "AER-2026-00219", "23 May 2026", False),
        ("form_C08", cases["C08"], "AER-2026-00157", "30 March 2026", True),
        ("form_K01", cases["K01_form"], "AER-2026-00244", "29 May 2026", False),
    ]
    for name, case, ref, date, labs in form_specs:
        out[name] = build_form_pdf(
            pdf_dir / f"{name}.pdf", case,
            reference=ref, report_date=date, include_lab_table=labs)

    # A form long enough to break across pages, with the lab table continuing and repeating
    # its header row — the cross-page table merge of E18.
    out["form_C02_long"] = build_multipage_form_pdf(
        pdf_dir / "form_C02_long.pdf", cases["C02"],
        reference="AER-2026-00188-FU", report_date="21 May 2026", continuation_pages=1)

    # --- non-English forms (E16, E17) ---
    # Labels AND narrative in the source language, so these are genuinely non-English
    # documents rather than English ones wearing translated labels.
    out["form_de"] = build_form_pdf(
        pdf_dir / "form_de.pdf", _with_narrative(cases["C08"], GERMAN_NARRATIVE),
        reference="AER-2026-00301", report_date="30. März 2026", language="de")
    out["form_fr"] = build_form_pdf(
        pdf_dir / "form_fr.pdf", _with_narrative(cases["C05"], FRENCH_NARRATIVE),
        reference="AER-2026-00312", report_date="24 juillet 2026", language="fr")
    out["form_ja"] = build_form_pdf(
        pdf_dir / "form_ja.pdf", _with_narrative(cases["C04"], JAPANESE_NARRATIVE),
        reference="AER-2026-00325", report_date="2026年6月24日", language="ja",
        font_name=_japanese_font())
    # E17: English labels wrapped around German free text — the case that defeats
    # document-level language detection and forces block-level detection.
    out["form_mixed"] = build_form_pdf(
        pdf_dir / "form_mixed.pdf", _with_narrative(cases["C08"], GERMAN_NARRATIVE),
        reference="AER-2026-00318", report_date="30 March 2026",
        language="en", labels_language="en")

    # --- scanned and handwritten (E13, E34) ---
    out["scan_C01"] = build_scanned_pdf(
        pdf_dir / "scan_C01.pdf",
        [handwritten_case_text(cases["C01"], reference="PAPER-0091",
                               report_date="14/03/2026")],
        style=ScanStyle.handwritten(), seed=101)
    out["scan_C06"] = build_scanned_pdf(
        pdf_dir / "scan_C06.pdf",
        [handwritten_case_text(cases["C06"], reference="PAPER-0104",
                               report_date="02/08/2026")],
        style=ScanStyle.clean_scan(), seed=102)
    # Deliberately hard to read, so the legibility score has something to be low about and the
    # E34 confidence cap is exercised by real data rather than only by a unit test.
    out["scan_hard"] = build_scanned_pdf(
        pdf_dir / "scan_hard.pdf",
        [handwritten_case_text(cases["C10"], reference="PAPER-0118",
                               report_date="18/08/2026")],
        style=ScanStyle.hard(), seed=103)

    # --- hybrid: digital cover page + scanned annex, one file (E12) ---
    out["hybrid_C07"] = build_hybrid_pdf(
        pdf_dir / "hybrid_C07.pdf",
        out["form_C07"],
        [handwritten_case_text(cases["C07"], reference="AER-2026-00219",
                               report_date="23/05/2026")],
        seed=104)

    # --- password-protected (E7) and corrupt (E7) ---
    out["encrypted"] = build_encrypted_pdf(
        pdf_dir / "encrypted_report.pdf", out["form_C01"])
    out["corrupt"] = build_corrupt_pdf(
        pdf_dir / "corrupt_report.pdf", out["form_C03"])

    # --- articles ---
    for article in articles:
        out[f"article_{article.article_id}"] = build_article_pdf(
            pdf_dir / f"article_{article.article_id}.pdf", article)

    # --- non-PDF assets ---
    out["defect_photo"] = build_defect_photo(asset_dir / "blister_defect.jpg")
    out["logo"] = build_company_logo(asset_dir / "coreline_logo.png")

    # A .docx and a .zip: logged with skip_reason, never content-processed (E6).
    out["docx"] = _fake_docx(asset_dir / "reporting_guidance.docx")
    out["zip"] = _fake_zip(asset_dir / "case_bundle.zip", out["form_C01"])

    return out


def _japanese_font() -> str | None:
    """Register a CJK font with ReportLab if one is present, else fall back.

    MS Gothic / Yu Gothic are confirmed present on this machine (CLAUDE.md). Without a CJK
    face ReportLab renders empty boxes, so the Japanese document would be a blank page rather
    than a test.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    candidates = [
        ("MSGothic", Path("C:/Windows/Fonts/msgothic.ttc")),
        ("YuGothic", Path("C:/Windows/Fonts/YuGothM.ttc")),
        ("MSMincho", Path("C:/Windows/Fonts/msmincho.ttc")),
    ]
    for name, path in candidates:
        if not path.exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, str(path), subfontIndex=0))
            return name
        except Exception:
            continue

    # Last resort: ReportLab's bundled CID font. Renders Japanese correctly, cannot be
    # subset the same way, but the document is legible and that is what matters here.
    try:
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        return "HeiseiKakuGo-W5"
    except Exception:
        return None


GERMAN_NARRATIVE = (
    "Ein 34-jähriger Mann, der Domitrelle 2 mg zweimal täglich einnahm, stellte sich acht "
    "Wochen nach Therapiebeginn mit Fieber und Mundschleimhautulzerationen vor. Die "
    "Neutrophilenzahl betrug 0,3 x 10^9/L. Er wurde mit Granulozyten-Kolonie-stimulierendem "
    "Faktor behandelt und die Zahl erholt sich. Domitrelle wurde dauerhaft abgesetzt. Der "
    "Patient hatte keine bekannten Arzneimittelallergien und nahm keine weiteren Arzneimittel "
    "ein. Eine erneute Exposition ist nicht geplant."
)

FRENCH_NARRATIVE = (
    "Une femme de 29 ans, enceinte de 22 semaines, prenait Domitrelle 2 mg le soir depuis le "
    "début de l'année 2026 pour un trouble anxieux généralisé. Au mois de juillet, elle a "
    "présenté une éruption érythémateuse sur les zones exposées au soleil après seulement dix "
    "minutes à l'extérieur. La grossesse se poursuit normalement et aucune anomalie fœtale "
    "n'a été détectée à l'échographie. Le traitement a été interrompu."
)

JAPANESE_NARRATIVE = (
    "生後6週の女児に対し、胸部感染症の治療としてNuvexoral経口懸濁液125 mgを1日2回投与した。"
    "投与開始から2日以内に、1日10回を超える嘔吐と強い悪心が出現し、哺乳が困難となった。"
    "本剤の投与を中止し、経口補水療法を行ったところ、約36時間で回復した。"
    "併用薬はなく、既往歴に特記すべき事項はない。再投与は行っていない。"
)


def _with_narrative(case: CaseSpec, narrative: str) -> CaseSpec:
    """Same case, narrative rewritten in another language.

    Translating only the field *labels* would not produce a non-English document — it would
    produce an English document with translated furniture, and the language roll-up (weighted
    by character count) would correctly report it as English. The narrative is the bulk of the
    text and the part that carries the clinical content, so it is the part that has to be in
    the source language for E16 to mean anything.

    Passing a German narrative with English labels is what builds the *mixed* document (E17).
    """
    import copy

    variant = copy.deepcopy(case)
    variant.narrative = narrative
    return variant


def _fake_docx(path: Path) -> Path:
    """A structurally valid .docx — a real zip with the right parts (E6)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Adverse event reporting guidance for prescribers. "
        f"{SYNTHETIC_NOTICE}</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="rels" ContentType='
        '"application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml" ContentType='
        '"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return path


def _fake_zip(path: Path, inner_pdf: Path) -> Path:
    """A zip containing a PDF. Logged and skipped — we do not unpack archives (E6)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(inner_pdf, arcname="completed_form.pdf")
        z.writestr("README.txt", SYNTHETIC_NOTICE)
    return path


# =======================================================================================
# Messages
# =======================================================================================

def build_messages(cases: dict[str, CaseSpec], pdfs: dict[str, Path],
                   articles: list[ArticleSpec], base: datetime) -> list[MessageSpec]:
    """Every email in the corpus, with its ground truth."""
    m: list[MessageSpec] = []

    def sender(case_key: str) -> tuple[str, str]:
        r = cases[case_key].reporter
        return (r.name, r.email) if r else ("Website Contact Form", "noreply@coreline.example")

    # -----------------------------------------------------------------------------------
    # 1–12: emails about a reaction, at four levels of detail (brief §6, first bullet)
    # -----------------------------------------------------------------------------------
    c = cases["C01"]
    m.append(MessageSpec(
        key="icsr-01-complete-body",
        subject="Possible reaction to Velmoradine — 58F, rash",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 0, 9, 12),
        body_text=(
            f"Dear Safety team,\n\n{c.narrative}\n\n"
            "I am her GP and I am happy to provide follow-up information if it helps.\n\n"
            f"Kind regards,\n{c.reporter.name}\n{c.reporter.qualification}\n"
            f"{c.reporter.organisation}, {c.reporter.country}\n"),
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="All four ICSR minimum elements present in the body alone.",
        edge_cases=["E11"], expect_documents=1,
    ))

    c = cases["C02"]
    m.append(MessageSpec(
        key="icsr-02-serious-with-form",
        subject="Serious ADR report — hepatitis, Fenaquil (form attached)",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 0, 11, 40),
        body_text=(
            "Dear Safety team,\n\n"
            "Please find attached a completed report form for a serious adverse reaction. "
            "The patient required admission for five nights. Liver function results are in "
            "section G of the form.\n\n"
            f"Regards,\n{c.reporter.name}\n{c.reporter.qualification}\n"),
        attachments=[AttachmentSpec("AER-2026-00188.pdf", pdfs["form_C02"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="Digital form with a real lab-values table; seriousness = hospitalisation.",
        edge_cases=["E18"], expect_documents=2,
    ))

    c = cases["C03"]
    m.append(MessageSpec(
        key="icsr-03-fatal",
        subject="URGENT — fatal outcome following Cardexatine initiation",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 1, 8, 5),
        body_text=(
            "Dear colleagues,\n\n"
            f"{c.narrative}\n\n"
            "I am reporting this as an expedited case given the fatal outcome. Please "
            "acknowledge receipt.\n\n"
            f"{c.reporter.name}\n{c.reporter.qualification}, {c.reporter.organisation}\n"),
        attachments=[AttachmentSpec("fatal_case_form.pdf", pdfs["form_C03"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="Fatal outcome; seriousness criterion DEATH.",
        edge_cases=["E35"], expect_documents=2,
    ))

    c = cases["C04"]
    m.append(MessageSpec(
        key="icsr-04-paediatric-weeks",
        subject="Report — 6-week-old infant, vomiting after Nuvexoral",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 1, 14, 22),
        body_text=(
            f"Hello,\n\n{c.narrative}\n\n"
            "The mother is happy for this to be reported. Please let me know if you need the "
            "batch number, I can get it from the dispensing record.\n\n"
            f"{c.reporter.name}\n{c.reporter.qualification}\n"),
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="Age is 6 WEEKS, not years — must not be coerced to a year value (E30).",
        edge_cases=["E30"], expect_documents=1,
    ))

    c = cases["C05"]
    m.append(MessageSpec(
        key="icsr-05-pregnancy",
        subject="Photosensitivity in pregnancy — Domitrelle",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 2, 10, 3),
        body_text=(
            f"Dear Safety team,\n\n{c.narrative}\n\n"
            "The exact date of onset is not recorded in the notes; the patient says it was "
            "some time in July.\n\n"
            f"{c.reporter.name}\n"),
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="Onset precision is MONTH, not DAY — the source genuinely does not say (E28).",
        edge_cases=["E28"], expect_documents=1,
    ))

    c = cases["C06"]
    m.append(MessageSpec(
        key="icsr-06-relative-dates",
        subject="dizzy since starting migraine tablets",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 2, 16, 47),
        body_text=f"Hi\n\n{c.narrative}\n\nThanks\n{c.reporter.name}\n",
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Relative dates ('last March', 'about two weeks later') must be stored raw with "
            "is_relative=true and never resolved against today (E28)."),
        edge_cases=["E28"], expect_documents=1,
    ))

    c = cases["C07"]
    m.append(MessageSpec(
        key="icsr-07-multi-product",
        subject="Angioedema then dizziness — two medicines involved",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 3, 9, 30),
        body_text=(
            f"Dear Safety team,\n\n{c.narrative}\n\n"
            "I have marked Pralextin as the suspect medicine and Astelvia as concomitant, but "
            "I would not rule out the infusion.\n\n"
            f"{c.reporter.name}, {c.reporter.qualification}\n"),
        body_html=html_version("Reporting two reactions in one patient.", c.narrative),
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="Two products (one SUSPECT, one CONCOMITANT) and two reactions (E31).",
        edge_cases=["E3", "E31"], expect_documents=1,
    ))

    c = cases["C08"]
    m.append(MessageSpec(
        key="icsr-08-scanned-form",
        subject="Handwritten report form — agranulocytosis",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 3, 13, 15),
        body_text=(
            "Dear Safety team,\n\n"
            "Attached is a scan of the paper form completed on the ward. Apologies for the "
            "handwriting. The completed digital form follows separately if you prefer it.\n\n"
            f"{c.reporter.name}\n"),
        attachments=[
            AttachmentSpec("ward_form_scan.pdf", pdfs["scan_C01"]),
            AttachmentSpec("AER-2026-00157.pdf", pdfs["form_C08"]),
        ],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes="One scanned page (no text layer) and one digital form in the same message.",
        edge_cases=["E13", "E34"], expect_documents=3,
    ))

    c = cases["C09"]
    m.append(MessageSpec(
        key="icsr-09-no-reporter",
        subject="Anonymous website submission — rash on Velmoradine",
        sender_name="Coreline Website", sender_email="noreply@coreline.example",
        sent_at=_at(base, 4, 7, 55),
        body_text=(
            "Automated forward from the public contact form.\n\n"
            f"{c.narrative}\n\n"
            "-- no contact details were provided --\n"),
        golden_categories=["ICSR_INCOMPLETE"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Three of four minimum elements. No identifiable reporter, so the rule gives "
            "ICSR_INCOMPLETE and names the missing element (E22)."),
        edge_cases=["E22"], expect_documents=1,
    ))

    c = cases["C10"]
    m.append(MessageSpec(
        key="icsr-10-no-product",
        subject="Fall on the ward — medicine unknown",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 4, 12, 8),
        body_text=(
            f"Hello,\n\n{c.narrative}\n\n"
            "I did not want to guess at the drug name. Please tell me what else you need.\n\n"
            f"{c.reporter.name}, {c.reporter.qualification}\n"),
        attachments=[AttachmentSpec("ward_note_scan.pdf", pdfs["scan_hard"])],
        golden_categories=["ICSR_INCOMPLETE"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "No suspect product anywhere in the message. The correct answer for "
            "product.name is NOT_STATED — a guessed drug name here is a false-confident "
            "value, scored separately from a miss. The attachment is the hard-to-read scan, "
            "so page legibility should cap field confidence (E34)."),
        edge_cases=["E22", "E34"], expect_documents=2,
    ))

    c = cases["C11"]
    m.append(MessageSpec(
        key="icsr-11-vague",
        subject="not sure who to tell",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 5, 19, 2),
        body_text=f"{c.narrative}\n",
        golden_categories=["ICSR_INCOMPLETE"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Almost nothing is stated. Only the reporter is identifiable. Nearly every field "
            "should come back NOT_STATED — this message is the abstention test."),
        edge_cases=["E22"], expect_documents=1,
    ))

    c = cases["X02"]
    m.append(MessageSpec(
        key="icsr-12-mi-and-icsr",
        subject="my rash is worse, should I stop the tablets?",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 5, 20, 41),
        body_text=f"Hello\n\n{c.narrative}\n\nThank you\n{c.reporter.name}\n",
        golden_categories=["ICSR", "MI"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Both labels, independently true: four ICSR elements present AND a genuine "
            "question asked. They must not compete for probability mass (E24)."),
        edge_cases=["E24"], expect_documents=1,
    ))

    # -----------------------------------------------------------------------------------
    # PQC-only
    # -----------------------------------------------------------------------------------
    for i, (key, subject, extra_attachments, notes, edges) in enumerate([
        ("pqc-01-broken-seal", "Complaint — broken tamper seal, Velmoradine 20 mg", [],
         "Physical defect, no patient and no reaction. PQC alone.", []),
        ("pqc-02-particulates", "Particles visible in Zorbitran vials — photo attached",
         [AttachmentSpec("blister_defect.jpg", pdfs["defect_photo"])],
         "A bare image attachment gets a vision description rather than being skipped (E6, E19).",
         ["E6", "E19"]),
        ("pqc-03-damaged-carton", "Damaged delivery — Nuvexoral cartons torn", [],
         "Transit damage. Still a quality complaint, still no patient.", []),
    ]):
        c = cases[f"P0{i + 1}"]
        m.append(MessageSpec(
            key=key, subject=subject,
            sender_name=c.reporter.name, sender_email=c.reporter.email,
            sent_at=_at(base, 6 + i, 10 + i, 20),
            body_text=(
                f"Dear Quality team,\n\n{c.narrative}\n\n"
                f"Batch: {c.defect_batch}\n"
                f"Expiry: {c.defect_expiry}\n\n"
                f"{c.reporter.name}\n{c.reporter.organisation}\n"),
            attachments=extra_attachments,
            golden_categories=["PQC"],
            golden_cases=[_golden_case(c)],
            golden_notes=notes, edge_cases=edges,
            expect_documents=1 + len(extra_attachments),
        ))

    # -----------------------------------------------------------------------------------
    # MI-only
    # -----------------------------------------------------------------------------------
    for i in range(1, 4):
        c = cases[f"M0{i}"]
        m.append(MessageSpec(
            key=f"mi-0{i}-{c.mi_topic.replace(' ', '-')}",
            subject=f"Enquiry — {c.mi_topic}",
            sender_name=c.reporter.name, sender_email=c.reporter.email,
            sent_at=_at(base, 8 + i, 11, 5 * i),
            body_text=(
                f"Dear Medical Information,\n\n{c.mi_question}\n\n"
                "No patient has come to any harm and there is nothing wrong with the product "
                "itself; this is purely a question.\n\n"
                f"{c.reporter.name}\n{c.reporter.qualification or ''}\n"),
            golden_categories=["MI"],
            golden_cases=[_golden_case(c)],
            golden_notes=(
                "A genuine question with no reaction and no defect. Explicitly says no harm "
                "occurred, which is the distinction from an ICSR."),
            expect_documents=1,
        ))

    # -----------------------------------------------------------------------------------
    # Clearly irrelevant (E21)
    # -----------------------------------------------------------------------------------
    m.append(MessageSpec(
        key="irr-01-marketing",
        subject="Register now: 14th Annual Pharmacovigilance Excellence Summit",
        sender_name="Summit Events Team", sender_email="events@pv-summit.example",
        sent_at=_at(base, 12, 6, 30),
        body_text=(
            "Early-bird registration closes on Friday.\n\n"
            "Join 400 delegates for two days of keynotes on signal detection, AI in case "
            "processing and regulatory convergence. Group discounts available for teams of "
            "five or more.\n\n"
            "Unsubscribe: https://pv-summit.example/unsubscribe\n"),
        body_html=html_version(
            "Early-bird registration closes on Friday.",
            "Join 400 delegates for two days of keynotes.\n\nGroup discounts available."),
        golden_categories=["NOT_RELEVANT"],
        golden_notes=(
            "Marketing. NOT_RELEVANT must be the only label — it is assigned if and only if "
            "the other three sets are empty, enforced in code (E21)."),
        edge_cases=["E21"], expect_documents=1,
    ))

    m.append(MessageSpec(
        key="irr-02-out-of-office",
        subject="Automatic reply: Serious ADR report — hepatitis, Fenaquil",
        sender_name="Marcus Delane", sender_email="m.delane@harbourside-pharmacy.example",
        sent_at=_at(base, 12, 6, 31),
        body_text=(
            "I am out of the office until 14 September and will not be reading email.\n\n"
            "For urgent dispensing queries please contact the duty pharmacist on the number "
            "below. For anything relating to drug safety please write to the safety mailbox "
            "directly.\n"),
        golden_categories=["NOT_RELEVANT"],
        golden_notes=(
            "An auto-reply whose subject line quotes a real ADR report. Classifying on the "
            "subject alone gets this wrong."),
        edge_cases=["E21"], expect_documents=1,
    ))

    # -----------------------------------------------------------------------------------
    # Combined ICSR + PQC (E23)
    # -----------------------------------------------------------------------------------
    c = cases["X01"]
    m.append(MessageSpec(
        key="combo-01-icsr-and-pqc",
        subject="Contaminated vial AND reaction in the same patient",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 13, 9, 18),
        body_text=(
            f"Dear Safety and Quality teams,\n\n{c.narrative}\n\n"
            f"Batch: {c.defect_batch}\n\n"
            f"{c.reporter.name}, {c.reporter.qualification}\n"),
        golden_categories=["ICSR", "PQC"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Both labels. A defective product that also caused a reaction — the brief calls "
            "this case out explicitly (E23). Two chips, two confidences, both pipelines run."),
        edge_cases=["E23"], expect_documents=1,
    ))

    # -----------------------------------------------------------------------------------
    # Non-English (E16, E17)
    # -----------------------------------------------------------------------------------
    m.append(MessageSpec(
        key="lang-01-german",
        subject="UAW-Meldung — Agranulozytose unter Domitrelle",
        sender_name="Dr Ingrid Halvorsen", sender_email="i.halvorsen@fjordklinikk.example",
        sent_at=_at(base, 14, 8, 40),
        body_text=(
            "Sehr geehrte Damen und Herren,\n\n"
            "anbei übersende ich Ihnen einen ausgefüllten Meldebogen für eine schwerwiegende "
            "unerwünschte Arzneimittelwirkung. Der Patient wurde stationär behandelt.\n\n"
            "Mit freundlichen Grüßen\nDr Ingrid Halvorsen\n"),
        attachments=[AttachmentSpec("Meldebogen_AER-2026-00301.pdf", pdfs["form_de"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C08"])],
        golden_notes=(
            "German throughout. Evidence quotes must point at the German original, never at "
            "the English translation (E16)."),
        edge_cases=["E16"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="lang-02-french",
        subject="Déclaration d'effet indésirable — Domitrelle, grossesse",
        sender_name="Dr Aoife Whitfield", sender_email="a.whitfield@northgate-clinic.example",
        sent_at=_at(base, 14, 15, 12),
        body_text=(
            "Bonjour,\n\n"
            "Veuillez trouver ci-joint le formulaire de déclaration complété concernant une "
            "patiente enceinte de 22 semaines. La grossesse se poursuit normalement.\n\n"
            "Cordialement,\nDr Aoife Whitfield\n"),
        attachments=[AttachmentSpec("declaration_AER-2026-00312.pdf", pdfs["form_fr"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C05"])],
        golden_notes="French throughout.",
        edge_cases=["E16"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="lang-03-japanese",
        subject="副作用報告 — Nuvexoral 経口懸濁液",
        sender_name="Sister Priya Raghunathan", sender_email="p.raghunathan@st-elwyn.example",
        sent_at=_at(base, 15, 7, 25),
        body_text=(
            "ご担当者様\n\n"
            "医薬品副作用報告書を添付いたします。患者は生後6週の乳児です。\n\n"
            "よろしくお願いいたします。\n"),
        attachments=[AttachmentSpec("副作用報告書.pdf", pdfs["form_ja"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C04"])],
        golden_notes="Japanese, non-Latin script, with a non-ASCII attachment filename.",
        edge_cases=["E16"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="lang-04-mixed",
        subject="ADR report form (English form, German narrative)",
        sender_name="Dr Ingrid Halvorsen", sender_email="i.halvorsen@fjordklinikk.example",
        sent_at=_at(base, 15, 16, 50),
        body_text=(
            "Dear Safety team,\n\n"
            "Our clinic uses the English form but the narrative was written in German by the "
            "treating physician. I hope that is acceptable.\n\n"
            "Best regards\nDr Ingrid Halvorsen\n"),
        attachments=[AttachmentSpec("mixed_language_form.pdf", pdfs["form_mixed"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C08"])],
        golden_notes=(
            "English field labels wrapped around a German narrative. Document-level language "
            "detection gets this wrong; block-level detection with a page roll-up is required "
            "(E17)."),
        edge_cases=["E17"], expect_documents=2,
    ))

    # -----------------------------------------------------------------------------------
    # Adversarial set
    # -----------------------------------------------------------------------------------
    c = cases["C01"]
    m.append(MessageSpec(
        key="adv-01-duplicate-pdf",
        subject="Report form (sending twice in case the first did not arrive)",
        sender_name=c.reporter.name, sender_email=c.reporter.email,
        sent_at=_at(base, 16, 9, 0),
        body_text=(
            "Dear Safety team,\n\n"
            "I am attaching the same completed form twice under different names because our "
            "mail system flagged the first attempt. Apologies for the duplication.\n\n"
            f"{c.reporter.name}\n"),
        attachments=[
            AttachmentSpec("AER-2026-00141.pdf", pdfs["form_C01"]),
            AttachmentSpec("copy_of_report_form.pdf", pdfs["form_C01"]),
        ],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(c)],
        golden_notes=(
            "Byte-identical PDF under two filenames. The content-addressed blob store must "
            "recognise the second as a duplicate and reuse the cached parse — zero extra LLM "
            "calls (E9)."),
        edge_cases=["E9"], expect_documents=3,
    ))

    inner = MessageSpec(
        key="adv-02-inner-original",
        subject="Fwd: patient reaction — please pass to safety",
        sender_name=cases["C02"].reporter.name, sender_email=cases["C02"].reporter.email,
        sent_at=_at(base, 15, 14, 30),
        body_text=(
            f"Dear Safety team,\n\n{cases['C02'].narrative}\n\n"
            f"{cases['C02'].reporter.name}, {cases['C02'].reporter.qualification}\n"),
        attachments=[AttachmentSpec("AER-2026-00188.pdf", pdfs["form_C02"])],
    )
    m.append(MessageSpec(
        key="adv-02-forwarded-rfc822",
        subject="FW: patient reaction — please pass to safety",
        sender_name="Reception Desk", sender_email="reception@meadowvale-hosp.example",
        sent_at=_at(base, 16, 11, 15),
        body_text=(
            "Hello,\n\n"
            "This came into the general enquiries inbox. I do not think it should have come "
            "to us. Forwarding it on unchanged — I have not read the attachment.\n\n"
            "Reception\n"),
        forwarded=inner,
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C02"])],
        golden_notes=(
            "The outer body says nothing clinical; the real case is one level down inside a "
            "message/rfc822 part. Classifying the outer body alone yields NOT_RELEVANT, which "
            "is wrong. Recurse one level and hoist the attachments (E5)."),
        # Two documents, not three: the message/rfc822 part is consumed by the walker and its
        # PDF hoisted onto the parent, so it never becomes a document in its own right.
        edge_cases=["E5", "E25"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="adv-03-encrypted-pdf",
        subject="Completed form attached (password protected)",
        sender_name=cases["C01"].reporter.name, sender_email=cases["C01"].reporter.email,
        sent_at=_at(base, 17, 8, 45),
        body_text=(
            "Dear Safety team,\n\n"
            "Our practice policy requires attachments to be encrypted. The password will "
            "follow by telephone.\n\n"
            "In the meantime: a 58-year-old female developed an itchy rash nine days after "
            "starting Velmoradine 20 mg once daily. The drug has been stopped.\n\n"
            f"{cases['C01'].reporter.name}\n"),
        attachments=[AttachmentSpec("encrypted_report.pdf", pdfs["encrypted"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C01"])],
        golden_notes=(
            "The PDF cannot be opened. The document must be marked PARSE_FAILED with a "
            "reason and the message must still be classified from its body and reach the "
            "reviewer flagged NEEDS_ATTENTION rather than disappearing (E7)."),
        edge_cases=["E7", "E39"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="adv-04-unsupported-types",
        subject="Guidance document and case bundle",
        sender_name="Coreline Quality Admin", sender_email="quality.admin@coreline.example",
        sent_at=_at(base, 17, 13, 20),
        body_text=(
            "Circulating the updated reporting guidance and a zipped bundle of last "
            "quarter's forms for the archive. No action required.\n"),
        attachments=[
            AttachmentSpec("reporting_guidance.docx", pdfs["docx"]),
            AttachmentSpec("case_bundle.zip", pdfs["zip"]),
        ],
        golden_categories=["NOT_RELEVANT"],
        golden_notes=(
            "Neither attachment is content-processed. Both are recorded with processed='N' "
            "and skip_reason='UNSUPPORTED_TYPE' — logged, not silently dropped (E6)."),
        edge_cases=["E6"], expect_documents=1,
    ))

    m.append(MessageSpec(
        key="adv-05-mislabelled-octet-stream",
        subject="Report attached (our system mangles the file type)",
        sender_name=cases["C08"].reporter.name, sender_email=cases["C08"].reporter.email,
        sent_at=_at(base, 18, 10, 10),
        body_text=(
            "Dear Safety team,\n\n"
            "Our document system strips the file type on export, so the attachment may look "
            "like a generic binary. It is a normal PDF.\n\n"
            f"{cases['C08'].reporter.name}\n"),
        attachments=[AttachmentSpec(
            "report_export.dat", pdfs["form_C08"], declared_type="application/octet-stream")],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C08"])],
        golden_notes=(
            "Declared application/octet-stream with a .dat extension, but the bytes start "
            "%PDF-. Magic-byte sniffing must win over the declared type, and the disagreement "
            "itself is recorded (E4)."),
        edge_cases=["E4"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="adv-06-hybrid-pdf",
        subject="Typed cover sheet with handwritten annex",
        sender_name=cases["C07"].reporter.name, sender_email=cases["C07"].reporter.email,
        sent_at=_at(base, 18, 15, 35),
        body_text=(
            "Dear Safety team,\n\n"
            "The first page was generated by our system; the annex was completed by hand at "
            "the bedside and scanned in. Both are in the one file.\n\n"
            f"{cases['C07'].reporter.name}\n"),
        attachments=[AttachmentSpec("report_with_annex.pdf", pdfs["hybrid_C07"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C07"])],
        golden_notes=(
            "One document, two renderings: page 1 DIGITAL, page 2 SCANNED, document-level "
            "MIXED. This is the file that makes per-page flavour detection necessary rather "
            "than merely tidy (E12)."),
        edge_cases=["E12"], expect_documents=2,
    ))

    m.append(MessageSpec(
        key="adv-07-body-form-conflict",
        subject="Photosensitivity report — form attached",
        sender_name=cases["K01"].reporter.name, sender_email=cases["K01"].reporter.email,
        sent_at=_at(base, 19, 9, 5),
        body_text=(
            "Dear Safety team,\n\n"
            "Reporting a photosensitivity reaction in a 58-year-old female who started "
            "Pralextin on 14 May 2026. The completed form is attached.\n\n"
            f"{cases['K01'].reporter.name}\n"),
        attachments=[AttachmentSpec("AER-2026-00244.pdf", pdfs["form_K01"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["K01"]), _golden_case(cases["K01_form"])],
        golden_notes=(
            "The body says the patient is 58; the attached form says 71. Neither source is "
            "authoritative. The correct behaviour is status=CONFLICT with both values and "
            "their own evidence, surfaced for the reviewer to choose — silently picking one "
            "would be wrong in a regulated context (E33)."),
        edge_cases=["E33"], expect_documents=2,
    ))

    earlier = cases["C01"]
    earlier_body = (
        f"Dear Safety team,\n\n{earlier.narrative}\n\n"
        f"Kind regards,\n{earlier.reporter.name}\n")
    m.append(MessageSpec(
        key="adv-08-quoted-reply-chain",
        subject="RE: Possible reaction to Velmoradine — 58F, rash",
        sender_name=earlier.reporter.name, sender_email=earlier.reporter.email,
        sent_at=_at(base, 19, 14, 50),
        body_text=(
            "Thank you for the acknowledgement.\n\n"
            "One update only: the rash has now completely resolved and the patient has not "
            "been rechallenged. There is nothing else to add.\n\n"
            "Aoife\n"),
        quoted_history=quoted_reply(
            f"{earlier.reporter.name} <{earlier.reporter.email}>",
            _at(base, 0, 9, 12), earlier_body),
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(earlier)],
        golden_notes=(
            "The quoted history repeats the case from icsr-01 in full. It is follow-up on an "
            "existing report, not a second independent report — the new text above the quote "
            "boundary is the primary source (E10)."),
        edge_cases=["E10"], expect_documents=1,
    ))

    m.append(MessageSpec(
        key="adv-09-original-message-chain",
        subject="FW: Enquiry — storage temperature",
        sender_name="Coreline Medical Information", sender_email="medinfo@coreline.example",
        sent_at=_at(base, 20, 11, 30),
        body_text=(
            "Passing this to the safety mailbox in case it needs logging. As far as I can see "
            "it is purely a storage question and no patient has been harmed.\n"),
        quoted_history=original_message_reply(
            f"{cases['M02'].reporter.name} <{cases['M02'].reporter.email}>",
            "Enquiry — storage temperature", _at(base, 9, 11, 10), cases["M02"].mi_question),
        golden_categories=["MI"],
        golden_cases=[_golden_case(cases["M02"])],
        golden_notes=(
            "The other common quoting style. The enquiry itself is only in the quoted block, "
            "so unlike adv-08 the quoted text here is the *only* source of the fact — quoted "
            "text is de-prioritised, not discarded (E10)."),
        edge_cases=["E10"], expect_documents=1,
    ))

    m.append(MessageSpec(
        key="adv-10-corrupt-pdf",
        subject="Form attached — apologies if it is damaged",
        sender_name=cases["C03"].reporter.name, sender_email=cases["C03"].reporter.email,
        sent_at=_at(base, 20, 16, 12),
        body_text=(
            "Dear Safety team,\n\n"
            "Our export tool has been unreliable this week. If the attachment will not open, "
            "the essentials are: a 62-year-old male with stage 3 chronic kidney disease died "
            "of a ventricular arrhythmia on 6 February 2026, four weeks after starting "
            "Cardexatine 5 mg once daily.\n\n"
            f"{cases['C03'].reporter.name}\n"),
        attachments=[AttachmentSpec("damaged_form.pdf", pdfs["corrupt"])],
        golden_categories=["ICSR"],
        golden_cases=[_golden_case(cases["C03"])],
        golden_notes=(
            "The bytes start %PDF- so sniffing correctly calls it a PDF, but it is truncated "
            "and will not open. PARSE_FAILED, message still classified from the body, and the "
            "completion barrier must not stall on the failed document (E7, E39)."),
        edge_cases=["E7", "E39"], expect_documents=2,
    ))

    # -----------------------------------------------------------------------------------
    # Literature articles arriving by email (the batch upload path is exercised separately)
    # -----------------------------------------------------------------------------------
    for i, article in enumerate(articles[:3]):
        m.append(MessageSpec(
            key=f"lit-{i + 1:02d}-{article.article_id.lower()}",
            subject=f"Literature alert: {article.title}",
            sender_name="Literature Monitoring Service",
            sender_email="alerts@lit-monitor.example",
            sent_at=_at(base, 21 + i, 6, 15),
            body_text=(
                "Weekly literature screening alert.\n\n"
                f"Title: {article.title}\n"
                f"Authors: {article.authors}\n"
                f"Journal: {article.journal} {article.year}\n"
                f"DOI: {article.doi}\n\n"
                "Full text attached for assessment.\n"),
            attachments=[AttachmentSpec(
                f"{article.article_id}_{article.year}.pdf",
                Path("__PDF__") / f"article_{article.article_id}")],
            golden_categories=["ICSR"] if article.is_case_report else ["NOT_RELEVANT"],
            golden_cases=[_golden_case(c) for c in article.cases],
            golden_notes=(
                f"{article.article_kind}. "
                + (f"Contains {len(article.cases)} distinct patient(s) which must be split "
                   "into separate cases (E32). " if len(article.cases) > 1 else "")
                + "The References section names ages and sexes and must be excluded from "
                  "extraction, or the model will invent patients from citations (E15)."),
            edge_cases=["E14", "E15"] + (["E32"] if len(article.cases) > 1 else []),
            expect_documents=2,
        ))

    return m


def _golden_case(case: CaseSpec) -> dict[str, Any]:
    """Serialise one case's ground truth."""
    return {
        "case_id": case.case_id,
        "case_type": case.case_type,
        "case_index": case.case_index,
        "icsr_elements": case.icsr_elements(),
        "icsr_label": case.icsr_label(),
        "is_serious": case.is_serious(),
        "seriousness_criteria": case.seriousness_criteria(),
        "fields": case.golden_fields(),
    }


def build_corpus(*, cases, articles, rng: random.Random, corpus_dir: Path, pdf_dir: Path,
                 asset_dir: Path, golden_dir: Path) -> dict[str, Any]:
    """Render everything and write it to disk. Returns the manifest."""
    pdfs = build_pdfs(cases, articles, pdf_dir, asset_dir)

    base = datetime(2026, 8, 24, 9, 12, tzinfo=None).replace(tzinfo=None)
    from datetime import timezone
    base = base.replace(tzinfo=timezone.utc)

    messages = build_messages(cases, pdfs, articles, base)

    # Late-bind the literature attachments, which reference PDFs by key rather than path so
    # that build_messages does not need the map threaded through it twice.
    for spec in messages:
        for attachment in spec.attachments:
            if attachment.path.parent.name == "__PDF__":
                attachment.path = pdfs[attachment.path.name]

    eml_dir = corpus_dir / "emails"
    eml_dir.mkdir(parents=True, exist_ok=True)

    edge_cases: set[str] = set()
    golden_count = 0

    for spec in messages:
        message = build_eml(spec)
        (eml_dir / f"{spec.key}.eml").write_bytes(message.as_bytes())

        golden = {
            "key": spec.key,
            "subject": spec.subject,
            "sender": f"{spec.sender_name} <{spec.sender_email}>",
            "sent_at": spec.sent_at.isoformat(),
            "expected_categories": spec.golden_categories,
            "expected_document_count": spec.expect_documents,
            "edge_cases": spec.edge_cases,
            "notes": spec.golden_notes,
            "cases": spec.golden_cases,
            "attachments": [
                {"filename": a.filename,
                 "declared_type": a.declared_type,
                 "sha256_of": a.path.name}
                for a in spec.attachments
            ],
            "has_forwarded_message": spec.forwarded is not None,
            "has_quoted_history": spec.quoted_history is not None,
        }
        (golden_dir / f"{spec.key}.json").write_text(
            json.dumps(golden, indent=2, ensure_ascii=False), encoding="utf-8")
        golden_count += 1
        edge_cases.update(spec.edge_cases)

    document_total = sum(s.expect_documents for s in messages)

    return {
        "seed": 20260904,
        "generated_for": SAFETY_MAILBOX,
        "notice": SYNTHETIC_NOTICE,
        "counts": {
            "emails": len(messages),
            "documents": document_total,
            "pdfs": len([p for p in pdf_dir.glob("*.pdf")]),
            "goldens": golden_count,
        },
        "edge_cases_covered": sorted(edge_cases, key=lambda e: int(e[1:])),
        "messages": [
            {"key": s.key, "subject": s.subject,
             "categories": s.golden_categories,
             "documents": s.expect_documents,
             "edge_cases": s.edge_cases}
            for s in messages
        ],
    }
