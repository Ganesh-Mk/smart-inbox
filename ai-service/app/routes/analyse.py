"""Classification, extraction and summarisation endpoints.

These take *already parsed* text rather than raw bytes. That split matters: parsing is
deterministic and cacheable by content hash, while analysis depends on which pages triage
selected and on how the message's units combine. Keeping them apart means re-running the AI
pipeline never re-parses, and re-parsing never re-runs the AI.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.llm.client import LlmClient, LlmError, SchemaRepairFailed
from app.pipeline.classify import classify, roll_up_message
from app.pipeline.extract import (
    SourcePage,
    extract_icsr,
    extract_mi,
    extract_pqc,
)
from app.pipeline.merge import SourcedField, merge_fields, summarise_verification
from app.pipeline.summarise import summarise_text
from app.telemetry import Telemetry, Usage

log = logging.getLogger("smartinbox.ai.routes.analyse")

router = APIRouter(prefix="/v1", tags=["analyse"])


class PageInput(BaseModel):
    """One page of already-parsed source, as the verifier needs to see it."""

    document_id: int | str = 0
    page_no: int = 0
    text: str
    legibility: float = 1.0
    source_type: str = "PDF_PAGE"
    span_index: list[dict[str, Any]] = Field(default_factory=list)

    def to_source_page(self) -> SourcePage:
        return SourcePage(
            document_id=self.document_id, page_no=self.page_no, text=self.text,
            legibility=self.legibility, source_type=self.source_type,
            span_index=self.span_index)


class UnitInput(BaseModel):
    """One source unit — the email body, or one document."""

    name: str
    text: str
    pages: list[PageInput] = Field(default_factory=list)


class ClassifyRequest(BaseModel):
    units: list[UnitInput]
    source_description: str = ""


class ExtractRequest(BaseModel):
    categories: list[str]
    units: list[UnitInput]


class SummariseRequest(BaseModel):
    text: str


def _client() -> LlmClient:
    try:
        return LlmClient()
    except LlmError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/classify")
def classify_message(request: ClassifyRequest) -> dict:
    """Classify each unit, then roll up to the message as the union (E25)."""
    if not request.units:
        raise HTTPException(status_code=400, detail="no units supplied")

    client = _client()
    telemetry = Telemetry()
    outcomes = []
    unit_results = []

    for unit in request.units:
        if not unit.text.strip():
            continue
        try:
            outcome, call = classify(
                client, unit.text, source_description=request.source_description)
        except SchemaRepairFailed as exc:
            # E36: never a silent partial write. The job fails and the item is surfaced.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        telemetry.add_usage(call.usage)
        telemetry.record_ms("classify", call.latency_ms)
        outcomes.append((unit.name, outcome))
        unit_results.append({"unit": unit.name, **outcome.to_dict(),
                             "ai_call": call.to_log_dict()})

    if not outcomes:
        raise HTTPException(status_code=400, detail="every unit was empty")

    message_labels = roll_up_message(outcomes)

    return {
        "message_labels": [label.to_dict() for label in message_labels],
        "message_categories": [label.category for label in message_labels],
        "units": unit_results,
        "timings": telemetry.timings_dict(),
        "usage": telemetry.usage.to_dict(),
    }


@router.post("/extract")
def extract_cases(request: ExtractRequest) -> dict:
    """Extract fields for each matched category, verify every quote, then merge across units."""
    if not request.units:
        raise HTTPException(status_code=400, detail="no units supplied")

    client = _client()
    telemetry = Telemetry()
    sourced: list[SourcedField] = []
    cases: list[dict[str, Any]] = []
    ai_calls: list[dict[str, Any]] = []

    for unit in request.units:
        if not unit.text.strip():
            continue
        pages = [p.to_source_page() for p in unit.pages] or [
            SourcePage(document_id=0, page_no=0, text=unit.text, source_type="EMAIL_BODY")]

        for category in request.categories:
            try:
                if category in ("ICSR", "ICSR_INCOMPLETE"):
                    case, fields, calls = extract_icsr(client, unit.text, pages)
                    payload = {"case_type": "ICSR", "narrative": case.narrative,
                               "confidence": case.case_confidence}
                elif category == "PQC":
                    case, fields, calls = extract_pqc(client, unit.text, pages)
                    payload = {"case_type": "PQC",
                               "narrative": case.defect_description.value,
                               "confidence": case.case_confidence}
                elif category == "MI":
                    case, fields, calls = extract_mi(client, unit.text, pages)
                    payload = {"case_type": "MI",
                               "narrative": "; ".join(q.question.value for q in case.questions),
                               "confidence": case.case_confidence}
                else:
                    continue
            except SchemaRepairFailed as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

            for call in calls:
                telemetry.add_usage(call.usage)
                telemetry.record_ms("extract", call.latency_ms)
                ai_calls.append(call.to_log_dict())

            sourced.extend(SourcedField(unit.name, f) for f in fields)
            payload.update({"unit": unit.name, "field_count": len(fields)})
            cases.append(payload)

    merged = merge_fields(sourced)

    return {
        "cases": cases,
        "fields": [f.to_dict() for f in merged.fields],
        "conflicts": merged.conflicts,
        "verification": summarise_verification(merged.fields),
        "ai_calls": ai_calls,
        "timings": telemetry.timings_dict(),
        "usage": telemetry.usage.to_dict(),
    }


@router.post("/summarise")
def summarise(request: SummariseRequest) -> dict:
    """R7: a 10-15 sentence summary with a relevance verdict and a reason."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="no text supplied")

    client = _client()
    telemetry = Telemetry()
    summary, calls = summarise_text(client, request.text)
    for call in calls:
        telemetry.add_usage(call.usage)
        telemetry.record_ms("summarise", call.latency_ms)

    return {
        "summary": summary.summary,
        "sentence_count": summary.sentence_count,
        "relevance": summary.relevance.value,
        "relevance_reason": summary.relevance_reason,
        "key_points": summary.key_points,
        "ai_calls": [c.to_log_dict() for c in calls],
        "timings": telemetry.timings_dict(),
        "usage": telemetry.usage.to_dict(),
    }
