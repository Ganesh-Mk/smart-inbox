"""Build the whole synthetic corpus.

    python -m testdata.generator.build            # writes testdata/corpus + testdata/goldens
    python -m testdata.generator.build --clean    # wipe and rebuild from nothing

Everything is driven by a fixed seed, and the generator goes out of its way to be
reproducible: ReportLab runs with `rl_config.invariant`, MIME boundaries are derived from the
corpus key, ZIP entries carry a pinned timestamp, and `Message-ID` headers are derived from the
message key rather than from `make_msgid` (which embeds a clock reading and a random number —
that one mattered, because it made re-seeding create a *second* copy of every case instead of
being deduplicated).

Four files are still not byte-identical between builds, for reasons that are correct rather
than sloppy:

* `encrypted_report.pdf` — AES encryption uses a random salt and IV. Identical output would
  mean the encryption was broken.
* `hybrid_C07.pdf`, `scan_*.pdf` — PyMuPDF and Pillow each stamp their own creation date into
  the PDF trailer.

Their *content* is identical and their parse results are identical; only the container bytes
move. The property that actually matters — that re-seeding the same corpus never creates a
duplicate case — holds, because that is keyed on `Message-ID`.

The corpus is not a pile of plausible emails. Every item is here to exercise something
specific, and the `edge_cases` field on each message records which — so `eval/run_eval.py` can
report per-edge-case outcomes and the write-up can say "E12 is covered by this file", not
"we thought about hybrid documents".
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cases import CaseSpec, DrugExposure, ReactionEvent
from .fixtures import (
    DEFECTS,
    MI_QUESTIONS,
    PATIENTS,
    PRODUCTS_BY_NAME,
    REACTIONS,
    REPORTERS,
    SAFETY_MAILBOX,
)
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

# ReportLab stamps /CreationDate, /ModDate and a random /ID into every PDF, so without this
# the corpus is different bytes on every build: the blob store sees new content hashes, the
# parse cache misses, and `git status` is noisy after a regeneration. `invariant` freezes all
# three. It must be set before any canvas is created.
from reportlab import rl_config  # noqa: E402  (import order is deliberate)

rl_config.invariant = 1

SEED = 20260904
REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "testdata" / "corpus"
PDF_DIR = CORPUS_DIR / "pdfs"
ASSET_DIR = CORPUS_DIR / "assets"
GOLDEN_DIR = REPO_ROOT / "testdata" / "goldens"

BASE_DATE = datetime(2026, 8, 24, 9, 12, tzinfo=timezone.utc)


def _at(day_offset: int, hour: int = 9, minute: int = 0) -> datetime:
    return (BASE_DATE + timedelta(days=day_offset)).replace(hour=hour, minute=minute)


def reporter(index: int):
    return REPORTERS[index % len(REPORTERS)]


def patient(index: int):
    return PATIENTS[index % len(PATIENTS)]


def reaction(index: int):
    return REACTIONS[index % len(REACTIONS)]


def product(name: str):
    return PRODUCTS_BY_NAME[name]


# =======================================================================================
# Cases
# =======================================================================================

def make_case(
    case_id: str,
    *,
    patient_ix: int | None,
    reporter_ix: int | None,
    drugs: list[DrugExposure],
    reactions: list[ReactionEvent],
    narrative: str = "",
    case_type: str = "ICSR",
) -> CaseSpec:
    return CaseSpec(
        case_id=case_id,
        case_type=case_type,
        patient=patient(patient_ix) if patient_ix is not None else None,
        reporter=reporter(reporter_ix) if reporter_ix is not None else None,
        drugs=drugs,
        reactions=reactions,
        narrative=narrative,
    )


def dose(name: str, amount: str, unit: str, freq: str, batch: str,
         start_raw: str, start_iso: str | None, role: str = "SUSPECT") -> DrugExposure:
    p = product(name)
    return DrugExposure(
        product=p, role=role, dose_amount=amount, dose_unit=unit, frequency=freq,
        route=p.route, batch=batch, start_date_raw=start_raw, start_date_iso=start_iso,
        action_taken="DRUG_WITHDRAWN",
    )


def event(reaction_ix: int, onset_raw: str, onset_iso: str | None, *,
          precision: str = "DAY", relative: bool = False,
          outcome: str | None = None, criteria: list[str] | None = None) -> ReactionEvent:
    r = reaction(reaction_ix)
    return ReactionEvent(
        reaction=r,
        onset_raw=onset_raw,
        onset_iso=onset_iso,
        onset_precision=precision,
        onset_is_relative=relative,
        outcome=outcome or r.outcome,
        serious_criteria=criteria if criteria is not None
        else ([r.serious_criterion] if r.serious_criterion else []),
    )


def build_cases() -> dict[str, CaseSpec]:
    """Every case in the corpus, keyed by id."""
    c: dict[str, CaseSpec] = {}

    # --- complete ICSRs, all four minimum elements present ---
    c["C01"] = make_case(
        "C01", patient_ix=0, reporter_ix=0,
        drugs=[dose("Velmoradine", "20", "mg", "once daily", "VLM-4471B",
                    "3 March 2026", "2026-03-03")],
        reactions=[event(0, "12 March 2026", "2026-03-12")],
        narrative=(
            "The patient began Velmoradine 20 mg once daily for essential hypertension on "
            "3 March 2026. Nine days later she developed a widespread itchy maculopapular rash "
            "across the trunk and both forearms. The drug was withdrawn on 13 March and the "
            "rash began to settle within 48 hours. No corticosteroid was required. She had "
            "taken no other new medicine in the preceding month."),
    )

    c["C02"] = make_case(  # serious — hospitalisation
        "C02", patient_ix=5, reporter_ix=5,
        drugs=[dose("Fenaquil", "500", "mg", "up to three times daily", "FNQ-2210A",
                    "17 April 2026", "2026-04-17")],
        reactions=[event(2, "2 May 2026", "2026-05-02")],
        narrative=(
            "A 71-year-old female took Fenaquil 500 mg effervescent tablets for acute migraine "
            "over a two-week period. She presented on 2 May 2026 with jaundice and marked "
            "fatigue. Alanine aminotransferase was 640 U/L and total bilirubin 78 umol/L. She "
            "was admitted for five nights. Viral hepatitis serology was negative and there was "
            "no history of alcohol excess. Fenaquil was stopped on admission."),
    )

    c["C03"] = make_case(  # fatal
        "C03", patient_ix=8, reporter_ix=6,
        drugs=[dose("Cardexatine", "5", "mg", "once daily", "CDX-9903C",
                    "11 January 2026", "2026-01-11")],
        reactions=[event(5, "6 February 2026", "2026-02-06")],
        narrative=(
            "A 62-year-old male with chronic kidney disease stage 3 was started on Cardexatine "
            "5 mg once daily for chronic heart failure. He collapsed at home on 6 February 2026. "
            "Paramedics recorded a ventricular arrhythmia which did not respond to "
            "resuscitation. He was pronounced dead at the scene. A post-mortem has been "
            "requested but results are not yet available."),
    )

    c["C04"] = make_case(  # paediatric, age in weeks (E30)
        "C04", patient_ix=3, reporter_ix=2,
        drugs=[dose("Nuvexoral", "125", "mg", "twice daily", "NVX-1188D",
                    "20 June 2026", "2026-06-20")],
        reactions=[event(7, "22 June 2026", "2026-06-22")],
        narrative=(
            "A 6-week-old infant was prescribed Nuvexoral oral suspension 125 mg twice daily "
            "for a chest infection. Within two days she had unrelenting nausea with vomiting "
            "more than ten times a day and would not feed. The suspension was stopped and she "
            "recovered over the following 36 hours with oral rehydration."),
    )

    c["C05"] = make_case(  # pregnancy exposure
        "C05", patient_ix=7, reporter_ix=0,
        drugs=[dose("Domitrelle", "2", "mg", "once daily at night", "DMT-3320F",
                    "in early 2026", None)],
        reactions=[event(8, "in July", None, precision="MONTH")],
        narrative=(
            "A 29-year-old woman at 22 weeks gestation had been taking Domitrelle 2 mg at "
            "night for generalised anxiety disorder since early 2026. In July she developed an "
            "erythematous eruption on sun-exposed skin after only ten minutes outdoors. The "
            "pregnancy is ongoing and no fetal abnormality has been detected on ultrasound."),
    )

    c["C06"] = make_case(  # relative date, never resolved automatically (E28)
        "C06", patient_ix=4, reporter_ix=3,
        drugs=[dose("Fenaquil", "500", "mg", "as required", "FNQ-2210A",
                    "last March", None)],
        reactions=[event(9, "about two weeks later", None,
                         precision="UNKNOWN", relative=True)],
        narrative=(
            "I am a patient writing about myself. I was given Fenaquil last March for my "
            "migraines. About two weeks later I started getting very light-headed whenever I "
            "stood up, worst in the mornings. It is a bit better now but has not gone. I was "
            "born in 1966 if that helps."),
    )

    c["C07"] = make_case(  # multiple products and reactions (E31)
        "C07", patient_ix=6, reporter_ix=1,
        drugs=[
            dose("Pralextin", "100", "micrograms/dose", "two puffs as required", "PLX-7741E",
                 "5 May 2026", "2026-05-05"),
            dose("Astelvia", "1", "g", "three times daily", "AST-6612G",
                 "18 May 2026", "2026-05-18", role="CONCOMITANT"),
        ],
        reactions=[
            event(1, "21 May 2026", "2026-05-21"),
            event(9, "22 May 2026", "2026-05-22"),
        ],
        narrative=(
            "A 45-year-old male with lifelong asthma used Pralextin two puffs as required from "
            "5 May 2026. He was additionally given Astelvia 1 g three times daily on 18 May for "
            "post-operative prophylaxis. On 21 May he developed swelling of the lips and tongue "
            "with difficulty swallowing, and on the following day persistent dizziness. He "
            "attended the emergency department and was treated with intramuscular adrenaline. "
            "Both medicines were stopped."),
    )

    c["C08"] = make_case(  # agranulocytosis, serious
        "C08", patient_ix=1, reporter_ix=6,
        drugs=[dose("Domitrelle", "2", "mg", "twice daily", "DMT-3320F",
                    "2 February 2026", "2026-02-02")],
        reactions=[event(4, "29 March 2026", "2026-03-29")],
        narrative=(
            "A 34-year-old man taking Domitrelle 2 mg twice daily presented with fever and "
            "mouth ulceration eight weeks into treatment. The neutrophil count was "
            "0.3 x 10^9/L. He was managed with granulocyte colony-stimulating factor and the "
            "count is recovering. Domitrelle was permanently discontinued."),
    )

    # --- incomplete ICSRs: deliberately missing one minimum element (E22) ---
    c["C09"] = CaseSpec(  # no identifiable reporter
        case_id="C09", case_type="ICSR", patient=patient(0), reporter=None,
        drugs=[dose("Velmoradine", "20", "mg", "once daily", "VLM-4471B",
                    "in April", None)],
        reactions=[event(0, "a few days later", None, precision="UNKNOWN", relative=True)],
        narrative=(
            "Passing on a report received through the website contact form. A 58-year-old "
            "female taking Velmoradine 20 mg daily developed an itchy rash a few days after "
            "starting. No contact details were supplied and the form was submitted "
            "anonymously, so we cannot identify who sent it or follow up."),
    )

    c["C10"] = CaseSpec(  # no named product
        case_id="C10", case_type="ICSR", patient=patient(2), reporter=reporter(2),
        drugs=[],
        reactions=[event(3, "on Tuesday", None, precision="UNKNOWN", relative=True)],
        narrative=(
            "An elderly gentleman on our ward had a sudden loss of consciousness while standing "
            "on Tuesday and fell. He is on several regular medicines but the drug chart was not "
            "available to me when I wrote this and I do not want to guess which one is "
            "relevant. He was admitted overnight for observation and has recovered."),
    )

    c["C11"] = CaseSpec(  # vague: barely a patient, no product, no clear event
        case_id="C11", case_type="ICSR", patient=None, reporter=reporter(4),
        drugs=[], reactions=[],
        narrative=(
            "Hello, I think one of your medicines made a relative of mine feel unwell recently "
            "but I do not know which one it was or exactly what happened. They are fine now. I "
            "just thought somebody ought to know."),
    )

    # --- PQC ---
    c["P01"] = CaseSpec(
        case_id="P01", case_type="PQC", patient=None, reporter=reporter(1),
        defect=DEFECTS[0], defect_batch="VLM-4471B", defect_expiry="2027-11-30",
        photo_mentioned=False,
        narrative=(
            "A customer returned an unopened carton of Velmoradine 20 mg this morning. The "
            "outer tamper-evident seal was already split when the carton was opened, and the "
            "blister foil underneath was creased and partly lifted. The customer has not taken "
            "any of the tablets and reports no ill effects. The carton is being held at the "
            "pharmacy pending your instructions."),
    )

    c["P02"] = CaseSpec(
        case_id="P02", case_type="PQC", patient=None, reporter=reporter(2),
        defect=DEFECTS[1], defect_batch="ZBT-5530H", defect_expiry="2027-04-30",
        photo_mentioned=True,
        narrative=(
            "Three vials from this box of Zorbitran contain visible dark fibrous particles "
            "suspended in the solution, clearly visible when held against a white background. "
            "No product from this box has been administered to any patient. I have attached a "
            "photograph taken on the ward under standard lighting. The box has been quarantined "
            "in the clinical room."),
    )

    c["P03"] = CaseSpec(
        case_id="P03", case_type="PQC", patient=None, reporter=reporter(1),
        defect=DEFECTS[2], defect_batch="NVX-1188D", defect_expiry="2026-12-31",
        photo_mentioned=False,
        narrative=(
            "Our wholesaler delivery arrived this morning with the shipping carton crushed on "
            "one corner. Four of the twelve cartons inside were torn open and two blisters were "
            "punctured. Nothing has been dispensed. Please advise whether the undamaged cartons "
            "from the same delivery remain saleable."),
    )

    # --- MI ---
    for i, (topic, question) in enumerate(MI_QUESTIONS[:3], start=1):
        c[f"M0{i}"] = CaseSpec(
            case_id=f"M0{i}", case_type="MI", patient=None, reporter=reporter(i),
            mi_topic=topic, mi_question=question,
            narrative=question,
        )

    # --- combinations (E23, E24) ---
    c["X01"] = CaseSpec(  # ICSR + PQC: a defective product that also caused a reaction
        case_id="X01", case_type="ICSR", patient=patient(5), reporter=reporter(0),
        drugs=[dose("Zorbitran", "40", "mg/mL", "once weekly", "ZBT-5530H",
                    "9 June 2026", "2026-06-09")],
        reactions=[event(7, "9 June 2026", "2026-06-09")],
        defect=DEFECTS[1], defect_batch="ZBT-5530H", photo_mentioned=False,
        narrative=(
            "My patient injected Zorbitran from a vial which, on inspection afterwards, "
            "contained visible dark fibrous particles suspended in the solution. Within a few "
            "hours she had unrelenting nausea with vomiting more than ten times a day. She has "
            "since recovered. The remaining vials from the same box have been retained "
            "unopened and are available for you to collect."),
    )

    c["X02"] = CaseSpec(  # MI + ICSR: a question asked alongside a real reaction
        case_id="X02", case_type="ICSR", patient=patient(0), reporter=reporter(3),
        drugs=[dose("Velmoradine", "20", "mg", "once daily", "VLM-4471B",
                    "1 July 2026", "2026-07-01")],
        reactions=[event(0, "10 July 2026", "2026-07-10")],
        mi_topic="dose tapering",
        mi_question=("My rash has got worse over the last week. Should I stop the tablets all "
                     "at once or reduce the dose gradually, and how quickly?"),
        narrative=(
            "I started Velmoradine 20 mg once daily on 1 July. Around the 10th I got an itchy "
            "rash on my chest and arms and it has got worse over the last week. My rash has got "
            "worse over the last week. Should I stop the tablets all at once or reduce the dose "
            "gradually, and how quickly? I am 58 and female if that matters."),
    )

    # --- the conflict case (E33): the body and the form disagree on age ---
    c["K01"] = make_case(
        "K01", patient_ix=0, reporter_ix=0,
        drugs=[dose("Pralextin", "100", "micrograms/dose", "two puffs twice daily", "PLX-7741E",
                    "14 May 2026", "2026-05-14")],
        reactions=[event(8, "27 May 2026", "2026-05-27")],
        narrative=(
            "The attached completed form records this patient's details. She developed an "
            "erythematous eruption on sun-exposed skin thirteen days after starting Pralextin."),
    )
    # The attached form is generated from a *different* patient record, so the two sources
    # genuinely disagree about the age: the body says 58, the form says 71. Neither is
    # "wrong" from the extractor's point of view — the correct behaviour is to surface both
    # with status=CONFLICT and ask the reviewer to choose (E33).
    c["K01_form"] = CaseSpec(
        case_id="K01_form", case_type="ICSR",
        patient=PATIENTS[5],                  # 71-year-old female
        reporter=reporter(0),
        drugs=c["K01"].drugs,
        reactions=c["K01"].reactions,
        narrative=c["K01"].narrative,
    )

    return c


# =======================================================================================
# Articles
# =======================================================================================

def build_articles(cases: dict[str, CaseSpec]) -> list[ArticleSpec]:
    return [
        ArticleSpec(
            article_id="A01",
            title="Cholestatic hepatitis following short-course fenaquil: a case report",
            authors="R. Ashgrove, K. Meraldi, P. L. Ashworth",
            journal="Journal of Synthetic Pharmacovigilance",
            year=2026, doi="10.9999/jsp.2026.0142",
            keywords=["fenaquil", "drug-induced liver injury", "case report", "pharmacovigilance"],
            cases=[cases["C02"]],
            is_case_report=True,
            article_kind="CASE_REPORT",
            abstract=(
                "Drug-induced liver injury remains an important cause of acute hepatitis in "
                "older adults. We describe a single patient who developed a cholestatic "
                "picture after a short course of fenaquil for acute migraine, with a peak "
                "alanine aminotransferase of 640 U/L, and who required admission. Causality "
                "assessment favoured a probable association. The case is reported to raise "
                "awareness of hepatic monitoring in patients over seventy."),
            introduction=(
                "Fenaquil is an effervescent preparation licensed for the acute treatment of "
                "migraine. Hepatic adverse reactions have been described only rarely in the "
                "published literature, and the mechanism is not established. Older patients "
                "may be at greater risk because of reduced hepatic reserve and a higher burden "
                "of concomitant medication. We report a patient in whom a temporal association "
                "was clear and alternative causes were systematically excluded."),
            discussion=(
                "The temporal relationship between exposure and the onset of jaundice, the "
                "exclusion of viral and autoimmune causes, and the improvement following "
                "withdrawal together support a probable causal association. Rechallenge was "
                "not attempted and would not be justified. Clinicians should consider baseline "
                "liver function testing in older patients who are likely to take repeated "
                "courses. The pattern of enzyme elevation seen here was mixed rather than "
                "purely cholestatic, which is consistent with the small number of previously "
                "published observations."),
            conclusion=(
                "A single case cannot establish causality, but the pattern described here is "
                "consistent enough with previous observations to warrant vigilance. We "
                "recommend that hepatic adverse reactions to this agent continue to be "
                "reported to national pharmacovigilance schemes."),
        ),
        ArticleSpec(
            article_id="A02",
            title="Angioedema and neutropenia in two patients receiving domitrelle: a case series",
            authors="I. Halvorsen, B. Okonjo, T. Vasquez-Byrne",
            journal="Annals of Fictional Clinical Medicine",
            year=2026, doi="10.9999/afcm.2026.0088",
            keywords=["domitrelle", "angioedema", "agranulocytosis", "case series"],
            cases=[cases["C08"], cases["C07"]],
            is_case_report=True,
            article_kind="CASE_SERIES",
            abstract=(
                "We describe two unrelated patients who developed serious adverse reactions "
                "during treatment with domitrelle and a concomitant agent. The first developed "
                "agranulocytosis eight weeks into therapy; the second developed angioedema "
                "followed by persistent dizziness. Both required hospital assessment and both "
                "recovered following withdrawal. The cases are reported separately because the "
                "reactions, the time to onset and the outcomes differ materially."),
            introduction=(
                "Case series remain a useful early signal-detection tool where individual "
                "reports are too sparse to support formal disproportionality analysis. The two "
                "patients described here presented to different centres within a six-month "
                "period. Neither had a documented history of drug allergy. We present each "
                "case in turn, followed by a combined discussion."),
            discussion=(
                "The two presentations share a common suspect agent but differ in mechanism. "
                "Agranulocytosis is likely idiosyncratic and immune-mediated, whereas the "
                "angioedema in the second patient may reflect an interaction with the "
                "concomitant infusion. Neither patient was rechallenged. Reporting both "
                "together should not be read as implying a shared mechanism; they are grouped "
                "here only because the suspect agent is common to both."),
            conclusion=(
                "Two serious reactions in a six-month period, in patients managed at different "
                "centres, are sufficient to justify continued monitoring. Each case should be "
                "handled as a separate individual case safety report."),
        ),
        ArticleSpec(
            article_id="A03",
            title="Cutaneous and neurological reactions to velmoradine: three illustrative cases",
            authors="S. Halloran, R. Marchetti, D. Ferreira",
            journal="Review of Imaginary Dermatology",
            year=2026, doi="10.9999/rid.2026.0311",
            keywords=["velmoradine", "maculopapular rash", "peripheral neuropathy", "case series"],
            cases=[cases["C01"], cases["C05"], cases["C06"]],
            is_case_report=True,
            article_kind="CASE_SERIES",
            abstract=(
                "Three patients are described who developed cutaneous or neurological "
                "reactions during treatment with velmoradine or a related agent. The "
                "presentations were a maculopapular rash, a photosensitivity reaction during "
                "pregnancy, and persistent postural dizziness. Time to onset ranged from nine "
                "days to several weeks. All three are presented in full so that the differing "
                "latencies can be compared directly."),
            introduction=(
                "Cutaneous adverse reactions are among the most frequently reported classes of "
                "adverse drug reaction, but the published descriptions are often too brief to "
                "support causality assessment. We therefore present three cases in full, "
                "including negative findings and the reasoning behind each causality "
                "assessment."),
            discussion=(
                "Latency varied considerably across the three patients, which argues against a "
                "single shared mechanism. The second case is complicated by pregnancy, where "
                "the assessment must additionally consider the fetus. The third rests on "
                "patient-reported information with imprecise dates, and the confidence "
                "attached to it should be correspondingly lower. We have deliberately not "
                "harmonised the level of detail between cases."),
            conclusion=(
                "These three cases illustrate the range of presentations that may be "
                "encountered. Each meets the minimum criteria for an individual case safety "
                "report and each should be captured separately."),
        ),
        ArticleSpec(
            article_id="A04",
            title="Signal detection methodology in spontaneous reporting systems: a narrative review",
            authors="D. Ferreira, Y. Nakamura, A. Osei-Bonsu",
            journal="Journal of Synthetic Pharmacovigilance",
            year=2026, doi="10.9999/jsp.2026.0207",
            keywords=["signal detection", "disproportionality", "methodology", "review"],
            cases=[],
            is_case_report=False,
            article_kind="REVIEW",
            abstract=(
                "This narrative review surveys statistical approaches to signal detection in "
                "spontaneous reporting databases. We compare proportional reporting ratios, "
                "reporting odds ratios and Bayesian shrinkage estimators, and discuss the "
                "effect of reporting biases on each. No individual patient data are presented "
                "and no new cases are described."),
            introduction=(
                "Spontaneous reporting systems collect large volumes of unstructured, "
                "self-selected reports. Turning that into a reliable signal is a statistical "
                "problem as much as a clinical one. This review is intended for readers who "
                "assess signals operationally rather than for methodologists."),
            discussion=(
                "Every measure discussed shares a dependence on the denominator problem: the "
                "number of patients exposed is not known. Bayesian shrinkage mitigates the "
                "instability of ratios computed on small counts but cannot correct a biased "
                "numerator. Stimulated reporting after media coverage remains the single "
                "largest practical confounder."),
            conclusion=(
                "No single statistic is sufficient on its own. Quantitative screening should "
                "prioritise clinical review, not replace it."),
        ),
        ArticleSpec(
            article_id="A05",
            title="A randomised comparison of two dosing schedules of astelvia in "
                  "post-operative prophylaxis",
            authors="A. Osei-Bonsu, H. Prasetyo, V. Lund",
            journal="Annals of Fictional Clinical Medicine",
            year=2026, doi="10.9999/afcm.2026.0164",
            keywords=["astelvia", "randomised trial", "prophylaxis", "aggregate data"],
            cases=[],
            is_case_report=False,
            article_kind="CLINICAL_TRIAL",
            abstract=(
                "In this randomised, open-label trial, 412 participants undergoing elective "
                "surgery received astelvia 1 g either three times daily or twice daily. The "
                "primary endpoint was surgical site infection at 30 days. Adverse events were "
                "collected as aggregate counts. Rash occurred in 18 of 206 participants in the "
                "three-times-daily arm and 11 of 206 in the twice-daily arm. No individual "
                "patient is identifiable from the data presented."),
            introduction=(
                "Optimal dosing frequency for post-operative prophylaxis remains uncertain. "
                "This trial compared two schedules already in common use. The protocol was "
                "registered before recruitment began."),
            discussion=(
                "The absolute difference in the primary endpoint was small and the confidence "
                "interval crossed the null. The adverse event counts are reported as aggregate "
                "frequencies only; no individual case narratives were collected, and these "
                "counts should not be treated as individual case safety reports."),
            conclusion=(
                "Neither schedule was clearly superior. Aggregate safety data from this trial "
                "do not constitute reportable individual cases."),
        ),
        ArticleSpec(
            article_id="A06",
            title="Fatal ventricular arrhythmia after initiation of cardexatine: a case report",
            authors="R. Ashgrove, G. Lindqvist",
            journal="Journal of Synthetic Pharmacovigilance",
            year=2026, doi="10.9999/jsp.2026.0233",
            keywords=["cardexatine", "arrhythmia", "fatal outcome", "case report"],
            cases=[cases["C03"]],
            is_case_report=True,
            article_kind="CASE_REPORT",
            abstract=(
                "We report a fatal ventricular arrhythmia occurring approximately four weeks "
                "after initiation of cardexatine in a patient with stage 3 chronic kidney "
                "disease. Renal impairment may prolong exposure and is a plausible "
                "contributory factor. The case is reported because of the outcome and the "
                "short interval from initiation."),
            introduction=(
                "Cardexatine is licensed for chronic heart failure. Its elimination is partly "
                "renal, and the summary of product characteristics advises caution in "
                "impairment without specifying a dose adjustment. Fatal arrhythmia has not "
                "previously been described in the published literature."),
            discussion=(
                "Causality cannot be established from a single fatal case without post-mortem "
                "toxicology, which was not available at the time of writing. The temporal "
                "association and the absence of an alternative precipitant nonetheless justify "
                "reporting. Prescribers should consider closer monitoring during the first "
                "month of therapy in patients with reduced renal clearance."),
            conclusion=(
                "A fatal outcome in temporal association with a recently initiated medicine "
                "warrants expedited reporting regardless of the strength of the causality "
                "assessment."),
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Smart Inbox synthetic corpus")
    parser.add_argument("--clean", action="store_true", help="wipe the corpus before building")
    args = parser.parse_args()

    if args.clean:
        for d in (CORPUS_DIR, GOLDEN_DIR):
            if d.exists():
                shutil.rmtree(d)

    for d in (CORPUS_DIR, PDF_DIR, ASSET_DIR, GOLDEN_DIR):
        d.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    cases = build_cases()

    # Deferred import: keeps this module importable for tests that only need the case data.
    from .corpus_messages import build_corpus

    manifest = build_corpus(
        cases=cases,
        articles=build_articles(cases),
        rng=rng,
        corpus_dir=CORPUS_DIR,
        pdf_dir=PDF_DIR,
        asset_dir=ASSET_DIR,
        golden_dir=GOLDEN_DIR,
    )

    (CORPUS_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"corpus written to {CORPUS_DIR}")
    print(f"  emails    : {manifest['counts']['emails']}")
    print(f"  documents : {manifest['counts']['documents']}")
    print(f"  pdfs      : {manifest['counts']['pdfs']}")
    print(f"  goldens   : {manifest['counts']['goldens']}")
    print(f"  edge cases covered: {', '.join(manifest['edge_cases_covered'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
