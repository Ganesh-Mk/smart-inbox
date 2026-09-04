"""Field extraction, evidence verification and the confidence chain.

Extraction produces a flat list of `ExtractedField` rows rather than a nested object, because
that is what the database stores, what the reviewer edits one at a time, and what the evaluation
harness scores field by field. Nesting is convenient for the model; flat rows are convenient for
everything downstream.

The important part of this module is not the extraction — it is what happens immediately after
it. Every field arrives with a quote the model asserts. Before anything is stored:

1. the quote is **proved** against the source page text (E27);
2. offsets are rewritten to what was actually found, never what was claimed;
3. a bounding box is resolved from the span index, so the UI can highlight it;
4. the confidence is put through the deterministic chain — unverified caps at 0.40, page
   legibility caps it further, a cross-source conflict caps it at 0.50.

Both the model's original confidence and the adjusted one are kept, with the reason, so the
write-up can show exactly how much of a final number is self-report and how much is
verification.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.llm.client import LlmCall, LlmClient, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import (
    Age,
    Fact,
    IcsrCase,
    IcsrParties,
    IcsrProducts,
    IcsrReactions,
    MiCase,
    PartialDate,
    PqcCase,
    Quantity,
)
from app.pipeline.verify import adjust_confidence, verify_against_pages
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pipeline.extract")


@dataclass
class SourcePage:
    """One page of source text available for verification."""

    document_id: int | str
    page_no: int
    text: str
    legibility: float = 1.0
    source_type: str = "PDF_PAGE"
    span_index: list[dict[str, Any]] = field(default_factory=list)

    def bbox_for(self, char_start: int, char_end: int) -> str | None:
        """Union bounding box for a character range, from the parse-time span index."""
        boxes = [
            s["b"] for s in self.span_index
            if s.get("s", 0) < char_end and s.get("e", 0) > char_start and s.get("b")
        ]
        if not boxes:
            return None
        return ",".join(f"{v:.2f}" for v in (
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)))


@dataclass
class Evidence:
    source_type: str
    document_id: int | str | None
    page_no: int | None
    quote: str
    char_start: int | None
    char_end: int | None
    bbox: str | None
    verified: str
    verify_method: str
    match_score: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "document_id": self.document_id,
            "page_no": self.page_no,
            "quote": self.quote,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "bbox": self.bbox,
            "verified": self.verified,
            "verify_method": self.verify_method,
            "match_score": round(self.match_score, 2),
            "note": self.note,
        }


@dataclass
class ExtractedField:
    """One fact, ready to be written to `EXTRACTED_FIELD` with its `FIELD_EVIDENCE` rows."""

    field_group: str
    field_path: str
    field_index: int
    value_text: str
    value_json: str | None
    unit: str | None
    raw_text: str | None
    status: str
    confidence: float
    confidence_pre_adjust: float
    adjust_reason: str
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_group": self.field_group,
            "field_path": self.field_path,
            "field_index": self.field_index,
            "value_text": self.value_text,
            "value_json": self.value_json,
            "unit": self.unit,
            "raw_text": self.raw_text,
            "status": self.status,
            "confidence": round(self.confidence, 4),
            "confidence_pre_adjust": round(self.confidence_pre_adjust, 4),
            "adjust_reason": self.adjust_reason,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @property
    def asserts_something(self) -> bool:
        return self.status in ("STATED", "UNCERTAIN", "CONFLICT")


def _verify_and_adjust(
    quote: str,
    cited_page: int,
    model_confidence: float,
    status: str,
    pages: Sequence[SourcePage],
    settings: Settings,
) -> tuple[float, float, str, list[Evidence]]:
    """Prove the quote, then run the confidence chain. Returns the adjusted confidence."""
    if status == "NOT_STATED":
        # Abstention costs nothing and needs no evidence. Confidence is 0 by definition.
        return 0.0, model_confidence, "", []

    page_texts = [(p.page_no, p.text) for p in pages]
    result = verify_against_pages(
        quote, page_texts, cited_page=cited_page, settings=settings)

    page = next((p for p in pages if p.page_no == result.page_no), None)
    bbox = None
    if page is not None and result.verified and result.char_start is not None:
        bbox = page.bbox_for(result.char_start, result.char_end)

    evidence = Evidence(
        source_type=page.source_type if page else "PDF_PAGE",
        document_id=page.document_id if page else None,
        page_no=result.page_no,
        quote=quote,
        char_start=result.char_start,
        char_end=result.char_end,
        bbox=bbox,
        verified="Y" if result.verified else "N",
        verify_method=result.method,
        match_score=result.match_score,
        note=result.note,
    )

    legibility = page.legibility if page is not None else 1.0
    adjusted, reason = adjust_confidence(
        model_confidence,
        evidence_verified=result.verified,
        page_legibility=legibility,
        settings=settings,
    )
    return adjusted, model_confidence, reason, [evidence]


def _field_from_fact(
    fact: Fact, group: str, path: str, pages: Sequence[SourcePage],
    settings: Settings, index: int = 0,
) -> ExtractedField:
    adjusted, original, reason, evidence = _verify_and_adjust(
        fact.quote, fact.page_no, fact.confidence, fact.status.value, pages, settings)
    return ExtractedField(
        field_group=group, field_path=path, field_index=index,
        value_text=fact.value, value_json=None, unit=None, raw_text=None,
        status=fact.status.value, confidence=adjusted,
        confidence_pre_adjust=original, adjust_reason=reason, evidence=evidence)


def _field_from_date(
    date: PartialDate, group: str, path: str, pages: Sequence[SourcePage],
    settings: Settings, index: int = 0,
) -> ExtractedField:
    adjusted, original, reason, evidence = _verify_and_adjust(
        date.quote, date.page_no, date.confidence, date.status.value, pages, settings)
    # E28: a relative date is never resolved. The typed value records that plainly so the UI
    # can flag it and the reviewer can decide.
    payload = {
        "raw": date.raw, "iso": date.iso,
        "precision": date.precision.value, "is_relative": date.is_relative,
    }
    return ExtractedField(
        field_group=group, field_path=path, field_index=index,
        value_text=date.raw, value_json=json.dumps(payload), unit=None, raw_text=date.raw,
        status=date.status.value, confidence=adjusted,
        confidence_pre_adjust=original,
        adjust_reason="; ".join(filter(None, [
            reason, "relative date, not resolved" if date.is_relative else ""])),
        evidence=evidence)


def _field_from_quantity(
    quantity: Quantity, group: str, path: str, pages: Sequence[SourcePage],
    settings: Settings, index: int = 0,
) -> ExtractedField:
    adjusted, original, reason, evidence = _verify_and_adjust(
        quantity.quote, quantity.page_no, quantity.confidence,
        quantity.status.value, pages, settings)
    return ExtractedField(
        field_group=group, field_path=path, field_index=index,
        value_text=quantity.amount, unit=quantity.unit, raw_text=quantity.raw,
        value_json=json.dumps({"amount": quantity.amount, "unit": quantity.unit,
                               "raw": quantity.raw}),
        status=quantity.status.value, confidence=adjusted,
        confidence_pre_adjust=original, adjust_reason=reason, evidence=evidence)


def _field_from_age(
    age: Age, group: str, path: str, pages: Sequence[SourcePage],
    settings: Settings, index: int = 0,
) -> ExtractedField:
    adjusted, original, reason, evidence = _verify_and_adjust(
        age.quote, age.page_no, age.confidence, age.status.value, pages, settings)
    # E30: the unit is part of the answer. 6 WEEK must never become 0.115 YEAR.
    payload = {
        "value": age.value, "unit": age.unit.value,
        "raw": age.raw, "derived_from_dob": age.derived_from_dob,
    }
    return ExtractedField(
        field_group=group, field_path=path, field_index=index,
        value_text=age.value or age.raw, unit=age.unit.value, raw_text=age.raw,
        value_json=json.dumps(payload),
        status=age.status.value, confidence=adjusted,
        confidence_pre_adjust=original,
        adjust_reason="; ".join(filter(None, [
            reason, "derived from date of birth" if age.derived_from_dob else ""])),
        evidence=evidence)


# =======================================================================================
# ICSR — three calls, assembled (D-013)
# =======================================================================================

def extract_icsr(
    client: LlmClient,
    source_text: str,
    pages: Sequence[SourcePage],
    settings: Settings | None = None,
) -> tuple[IcsrCase, list[ExtractedField], list[LlmCall]]:
    """Run the three ICSR extraction calls and flatten the result into verified fields."""
    settings = settings or get_settings()
    calls: list[LlmCall] = []

    def run(prompt_id: str, model):
        prompt = load(prompt_id)
        call = client.complete_json(
            purpose=prompt_id,
            system_prompt=system_prompt().text,
            user_content=[text_part(prompt.render(source_text=source_text))],
            schema_model=model,
            prompt_version=prompt.label,
            max_tokens=6000,
        )
        calls.append(call)
        return model.model_validate(call.parsed)

    parties: IcsrParties = run("P2a_extract_parties", IcsrParties)
    products: IcsrProducts = run("P2b_extract_products", IcsrProducts)
    reactions: IcsrReactions = run("P2c_extract_reactions", IcsrReactions)

    case = IcsrCase.assemble(parties, products, reactions)
    fields = flatten_icsr(case, pages, settings)
    return case, fields, calls


def flatten_icsr(
    case: IcsrCase, pages: Sequence[SourcePage], settings: Settings | None = None,
) -> list[ExtractedField]:
    """Turn an assembled case into verified, confidence-adjusted rows."""
    settings = settings or get_settings()
    out: list[ExtractedField] = []

    patient = case.patient
    out.append(_field_from_age(patient.age, "PATIENT", "patient.age", pages, settings))
    out.append(_field_from_fact(patient.sex, "PATIENT", "patient.sex", pages, settings))
    out.append(_field_from_fact(patient.initials, "PATIENT", "patient.initials", pages, settings))
    out.append(_field_from_quantity(patient.weight, "PATIENT", "patient.weight", pages, settings))
    out.append(_field_from_fact(
        patient.medical_history, "PATIENT", "patient.medical_history", pages, settings))

    reporter = case.reporter
    out.append(_field_from_fact(reporter.name, "REPORTER", "reporter.name", pages, settings))
    out.append(_field_from_fact(
        reporter.organisation, "REPORTER", "reporter.organisation", pages, settings))
    out.append(_field_from_fact(reporter.country, "REPORTER", "reporter.country", pages, settings))

    role_adjusted, role_original, role_reason, role_evidence = _verify_and_adjust(
        reporter.role_quote, 0, reporter.role_confidence,
        "STATED" if reporter.role.value != "UNKNOWN" else "NOT_STATED", pages, settings)
    out.append(ExtractedField(
        field_group="REPORTER", field_path="reporter.role", field_index=0,
        value_text=reporter.role.value, value_json=None, unit=None, raw_text=None,
        status="STATED" if reporter.role.value != "UNKNOWN" else "NOT_STATED",
        confidence=role_adjusted, confidence_pre_adjust=role_original,
        adjust_reason=role_reason, evidence=role_evidence))

    for index, product in enumerate(case.products):
        prefix = f"product[{index}]"
        out.append(_field_from_fact(product.name, "PRODUCT", f"{prefix}.name", pages, settings, index))
        # Verified like every other assertion. This used to inherit the product *name's*
        # confidence and carry no evidence at all, so a role the model had guessed displayed at
        # whatever the name scored — the one place in the system where a fact was asserted with
        # no way to check it (E27).
        role_adj, role_orig, role_why, role_ev = _verify_and_adjust(
            product.product_role_quote, 0, product.product_role_confidence,
            "STATED", pages, settings)
        out.append(ExtractedField(
            field_group="PRODUCT", field_path=f"{prefix}.role", field_index=index,
            value_text=product.product_role.value, value_json=None, unit=None, raw_text=None,
            status="STATED", confidence=role_adj,
            confidence_pre_adjust=role_orig, adjust_reason=role_why, evidence=role_ev))
        out.append(_field_from_fact(
            product.dose_amount, "PRODUCT", f"{prefix}.dose.amount", pages, settings, index))
        out.append(_field_from_fact(
            product.dose_unit, "PRODUCT", f"{prefix}.dose.unit", pages, settings, index))
        out.append(_field_from_fact(
            product.frequency, "PRODUCT", f"{prefix}.dose.frequency", pages, settings, index))
        out.append(_field_from_fact(product.batch, "PRODUCT", f"{prefix}.batch", pages, settings, index))
        out.append(_field_from_date(
            product.start_date, "PRODUCT", f"{prefix}.start_date", pages, settings, index))
        route_status = "STATED" if product.route.value != "UNKNOWN" else "NOT_STATED"
        route_adj, route_orig, route_why, route_ev = _verify_and_adjust(
            product.route_quote, 0, product.route_confidence, route_status, pages, settings)
        out.append(ExtractedField(
            field_group="PRODUCT", field_path=f"{prefix}.route", field_index=index,
            value_text=product.route.value, value_json=None, unit=None, raw_text=None,
            status=route_status, confidence=route_adj,
            confidence_pre_adjust=route_orig, adjust_reason=route_why, evidence=route_ev))

    for index, reaction in enumerate(case.reactions):
        prefix = f"reaction[{index}]"
        out.append(_field_from_fact(reaction.term, "REACTION", f"{prefix}.term", pages, settings, index))
        out.append(_field_from_date(reaction.onset, "REACTION", f"{prefix}.onset", pages, settings, index))

        outcome_adjusted, outcome_original, outcome_reason, outcome_evidence = _verify_and_adjust(
            reaction.outcome_quote, 0, reaction.outcome_confidence,
            "STATED" if reaction.outcome.value != "UNKNOWN" else "NOT_STATED", pages, settings)
        out.append(ExtractedField(
            field_group="REACTION", field_path=f"{prefix}.outcome", field_index=index,
            value_text=reaction.outcome.value, value_json=None, unit=None, raw_text=None,
            status="STATED" if reaction.outcome.value != "UNKNOWN" else "NOT_STATED",
            confidence=outcome_adjusted, confidence_pre_adjust=outcome_original,
            adjust_reason=outcome_reason, evidence=outcome_evidence))

        # E35: seriousness is the fixed enum, stored as the set the source supports.
        criteria = [c.value for c in reaction.seriousness_criteria]
        serious_adjusted, serious_original, serious_reason, serious_evidence = _verify_and_adjust(
            reaction.seriousness_quote, 0, 0.9 if criteria else 0.0,
            "STATED" if criteria else "NOT_STATED", pages, settings)
        out.append(ExtractedField(
            field_group="SEVERITY", field_path=f"{prefix}.seriousness", field_index=index,
            value_text=", ".join(criteria) if criteria else "",
            value_json=json.dumps({"criteria": criteria, "is_serious": bool(criteria)}),
            unit=None, raw_text=None,
            status="STATED" if criteria else "NOT_STATED",
            confidence=serious_adjusted, confidence_pre_adjust=serious_original,
            adjust_reason=serious_reason, evidence=serious_evidence))

    if case.narrative:
        out.append(ExtractedField(
            field_group="NARRATIVE", field_path="narrative", field_index=0,
            value_text=case.narrative[:4000], value_json=None, unit=None, raw_text=None,
            status="STATED", confidence=case.case_confidence,
            confidence_pre_adjust=case.case_confidence, adjust_reason="", evidence=[]))

    return out


# =======================================================================================
# PQC and MI
# =======================================================================================

def extract_pqc(
    client: LlmClient, source_text: str, pages: Sequence[SourcePage],
    settings: Settings | None = None,
) -> tuple[PqcCase, list[ExtractedField], list[LlmCall]]:
    settings = settings or get_settings()
    prompt = load("P3_extract_pqc")
    call = client.complete_json(
        purpose="P3_extract_pqc",
        system_prompt=system_prompt().text,
        user_content=[text_part(prompt.render(source_text=source_text))],
        schema_model=PqcCase, prompt_version=prompt.label, max_tokens=4000)
    case = PqcCase.model_validate(call.parsed)

    fields = [
        _field_from_fact(case.product_name, "PRODUCT", "product.name", pages, settings),
        _field_from_fact(case.batch, "PRODUCT", "product.batch", pages, settings),
        _field_from_fact(case.expiry, "PRODUCT", "product.expiry", pages, settings),
        _field_from_fact(case.defect_description, "DEFECT", "defect.description", pages, settings),
        _field_from_fact(case.defect_category, "DEFECT", "defect.category", pages, settings),
        _field_from_fact(case.quantity_affected, "DEFECT", "defect.quantity", pages, settings),
    ]
    photo_adjusted, photo_original, photo_reason, photo_evidence = _verify_and_adjust(
        case.photo_quote, 0, 0.9 if case.photo_mentioned else 0.0,
        "STATED" if case.photo_mentioned else "NOT_STATED", pages, settings)
    fields.append(ExtractedField(
        field_group="DEFECT", field_path="defect.photo_mentioned", field_index=0,
        value_text=str(case.photo_mentioned).lower(), value_json=None, unit=None, raw_text=None,
        status="STATED" if case.photo_mentioned else "NOT_STATED",
        confidence=photo_adjusted, confidence_pre_adjust=photo_original,
        adjust_reason=photo_reason, evidence=photo_evidence))
    return case, fields, [call]


def extract_mi(
    client: LlmClient, source_text: str, pages: Sequence[SourcePage],
    settings: Settings | None = None,
) -> tuple[MiCase, list[ExtractedField], list[LlmCall]]:
    settings = settings or get_settings()
    prompt = load("P4_extract_mi")
    call = client.complete_json(
        purpose="P4_extract_mi",
        system_prompt=system_prompt().text,
        user_content=[text_part(prompt.render(source_text=source_text))],
        schema_model=MiCase, prompt_version=prompt.label, max_tokens=4000)
    case = MiCase.model_validate(call.parsed)

    fields = [_field_from_fact(case.product_name, "PRODUCT", "product.name", pages, settings)]
    for index, question in enumerate(case.questions):
        fields.append(_field_from_fact(
            question.question, "ENQUIRY", f"enquiry[{index}].question", pages, settings, index))
        fields.append(_field_from_fact(
            question.topic, "ENQUIRY", f"enquiry[{index}].topic", pages, settings, index))
    return case, fields, [call]
