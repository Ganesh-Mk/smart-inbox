"""POST /v1/literature/screen — the bonus path.

Screening an article asks two questions: is this a case report at all, and how many *distinct*
patients does it describe. The second is the one the brief singles out, because a case series
carrying three patients is three reportable cases, not one.

Almost none of the work here is new. `CASE_RECORD.case_index` has existed since the first
migration (E32), the section segmenter already knows where the case boundaries and the excluded
References section are (E15), and the review UI renders whatever cases it is given. The bonus is
a prompt and an endpoint on top of machinery that was built for the main path — which is exactly
why designing for it on day one made it cheap.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.llm.client import LlmClient, LlmError, SchemaRepairFailed, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import ScreeningResult
from app.pipeline.parse import parse_document
from app.telemetry import Telemetry

log = logging.getLogger("smartinbox.ai.routes.literature")

router = APIRouter(prefix="/v1/literature", tags=["literature"])

PROMPT_ID = "P9_screen_article"


@router.post("/screen")
async def screen(
    file: Annotated[UploadFile, File(description="One article PDF")],
    use_vision: Annotated[bool, Form()] = True,
) -> dict:
    """Parse an article and screen it for individual reportable cases."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")

    try:
        client = LlmClient()
    except LlmError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    telemetry = Telemetry()
    parsed = parse_document(data, filename=file.filename, client=client, use_vision=use_vision)
    if parsed.parse_status != "PARSED":
        raise HTTPException(status_code=422, detail=parsed.parse_error or "could not parse")

    telemetry.usage.add(parsed.telemetry.usage)

    # E15: the References section is withheld. Reference entries describe other authors'
    # patients, and a case extracted from one is a fabricated case.
    excluded = [s["heading"] for s in parsed.sections if s.get("excluded_from_case") == "Y"
                or s.get("excluded_from_case") is True]
    case_headings = [s["heading"] for s in parsed.sections
                     if s.get("section_kind") == "CASE_REPORT"]

    body = "\n\n".join(
        f"[page {p.page_no}]\n{p.text_original}" for p in parsed.pages)

    prompt = load(PROMPT_ID)
    try:
        call = client.complete_json(
            purpose=PROMPT_ID,
            system_prompt=system_prompt().text,
            user_content=[text_part(prompt.render(
                source_text=body,
                section_hints=", ".join(case_headings) or "(none detected)",
                excluded_sections=", ".join(excluded) or "(none)"))],
            schema_model=ScreeningResult,
            prompt_version=prompt.label,
            max_tokens=8000)
    except SchemaRepairFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result = ScreeningResult.model_validate(call.parsed)
    telemetry.add_usage(call.usage)
    telemetry.record_ms("screen", call.latency_ms)

    log.info("Screened %s: %s, %d case(s)",
             file.filename, result.article_kind, len(result.cases))

    return {
        "filename": file.filename,
        "is_case_report": result.is_case_report,
        "confidence": result.confidence,
        "article_kind": result.article_kind,
        "relevance_reason": result.relevance_reason,
        "cases": [
            {
                "case_index": c.case_index,
                "patient_descriptor": c.patient_descriptor,
                "summary": c.summary,
                "page_from": c.page_from,
                "page_to": c.page_to,
                "evidence_quote": c.evidence_quote,
                "icsr_elements": {
                    name: {
                        "present": getattr(c.icsr_elements, name).present,
                        "confidence": getattr(c.icsr_elements, name).confidence,
                        "quote": getattr(c.icsr_elements, name).quote,
                    }
                    for name in ("has_identifiable_patient", "has_identifiable_reporter",
                                 "has_suspect_product", "has_adverse_event")
                },
            }
            for c in result.cases
        ],
        "excluded_sections": result.excluded_sections or excluded,
        "document": parsed.to_dict()["document"],
        "ai_calls": [call.to_log_dict()],
        "timings": telemetry.timings_dict(),
        "usage": telemetry.usage.to_dict(),
    }
