"""POST /v1/parse — document understanding.

Takes the bytes of a PDF (or an email body) and returns the full envelope of PROJECT_PLAN
§10.7: per-page flavour and text, sections, tables, images, plus `timings` and `usage` so the
Java side can write `AI_CALL_LOG` and `PROCESSING_METRIC` rows without inventing anything.

Results are cached by content hash, so the same PDF attached to two emails parses once (E9).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from app.llm.client import LlmClient, LlmError
from app.pdf.loader import sha256_bytes
from app.pipeline.parse import parse_document, parse_email_body
from app.settings import get_settings

log = logging.getLogger("smartinbox.ai.routes.parse")

router = APIRouter(prefix="/v1", tags=["parse"])


def _cache_path(content_sha256: str):
    return get_settings().cache_dir / f"{content_sha256}.json"


def _cached(content_sha256: str) -> dict | None:
    path = _cache_path(content_sha256)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["document"]["from_cache"] = True
        # A cache hit costs nothing, and saying so is what makes the E9 saving visible in the
        # batch report rather than merely asserted.
        payload["usage"] = {**payload.get("usage", {}), "cost_usd": 0.0, "llm_calls": 0}
        return payload
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _store(content_sha256: str, payload: dict) -> None:
    try:
        _cache_path(content_sha256).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not cache parse result for %s: %s", content_sha256[:12], exc)


@router.post("/parse")
async def parse(
    file: Annotated[UploadFile, File(description="The PDF to parse")],
    use_vision: Annotated[bool, Form()] = True,
    force: Annotated[bool, Form(description="Ignore the parse cache")] = False,
) -> dict:
    """Parse an uploaded document."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty upload")

    content_sha256 = sha256_bytes(data)

    if not force:
        hit = _cached(content_sha256)
        if hit is not None:
            log.info("Parse cache hit for %s (%s) — no LLM calls (E9)",
                     file.filename, content_sha256[:12])
            return hit

    client = None
    if use_vision:
        try:
            client = LlmClient()
        except LlmError as exc:
            # Without a key we can still do everything that does not need the model. Better a
            # partial parse with the reason recorded than a hard failure.
            log.warning("Vision disabled: %s", exc)

    result = parse_document(
        data, filename=file.filename, client=client, use_vision=use_vision and client is not None)
    payload = result.to_dict()
    payload["document"]["from_cache"] = False

    if result.parse_status == "PARSED":
        _store(content_sha256, payload)

    return payload


@router.post("/parse/email-body")
async def parse_body(
    text: Annotated[str, Body(embed=True)],
    html: Annotated[str | None, Body(embed=True)] = None,
) -> dict:
    """Parse an email body (E11).

    The body is a document like any other, so it gets the same envelope, the same evidence model
    and the same review path as a PDF. No LLM call is needed — the text is already text.
    """
    result = parse_email_body(text, html=html)
    payload = result.to_dict()
    payload["document"]["from_cache"] = False
    return payload
