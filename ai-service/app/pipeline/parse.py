"""The document understanding pass: bytes in, structured document out.

Orchestrates the whole of P3. For each document:

1. open it, with a typed reason if it will not open (E7)
2. normalise rotation, apply the page cap (E8)
3. per page: reading order + span index (E14), flavour (E12, E13), language (E17)
4. scanned pages go to Claude vision for transcription, which yields a legibility score (E34)
5. non-English pages get a translation alongside — never instead of — the original (E16)
6. tables (E18) and meaningful images (E19)
7. sections, with references and their kin excluded from case extraction (E15)

Everything is cached by content hash, so the same PDF attached to two emails is parsed once and
the second costs nothing (E9). That is also what makes a demo replayable with no network.

The response shape is PROJECT_PLAN §10.7 — always with `timings` and `usage`, so the Java side
can write `AI_CALL_LOG` and `PROCESSING_METRIC` rows without guessing at anything.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pymupdf

from app.lang.detect import normalise_language, roll_up_document
from app.llm.client import LlmCall, LlmClient
from app.pdf import flavour as flavour_mod
from app.pdf.images import collect_candidates
from app.pdf.layout import PageLayout, layout_page
from app.pdf.loader import DocumentLoadError, LoadedDocument, load
from app.pdf.render import render_page
from app.pdf.sections import segment_document
from app.pdf.tables import ExtractedTable, extract_page_tables, merge_continuations
from app.pipeline.describe import describe_embedded
from app.pipeline.transcribe import legibility_ceiling, transcribe_page
from app.pipeline.translate import translate_page
from app.settings import Settings, get_settings
from app.telemetry import Telemetry

log = logging.getLogger("smartinbox.ai.pipeline.parse")


@dataclass
class ParsedPage:
    page_no: int
    rendering: str
    genre: str
    language: str | None
    lang_confidence: float
    column_count: int
    char_count: int
    has_text_layer: bool
    legibility: float
    width: float
    height: float
    rotation: int
    text_original: str
    text_english: str | None
    render_path: str
    span_index: list[dict[str, Any]]
    scanned_reason: str | None = None
    genre_reason: str = ""
    uncertain_segments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "rendering": self.rendering,
            "genre": self.genre,
            "language": self.language,
            "lang_confidence": round(self.lang_confidence, 4),
            "column_count": self.column_count,
            "char_count": self.char_count,
            "has_text_layer": self.has_text_layer,
            "legibility": round(self.legibility, 4),
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "rotation": self.rotation,
            "text_original": self.text_original,
            "text_english": self.text_english,
            "render_path": self.render_path,
            "span_index_json": json.dumps(self.span_index, separators=(",", ":")),
            "scanned_reason": self.scanned_reason,
            "genre_reason": self.genre_reason,
            "uncertain_segments": self.uncertain_segments,
        }


@dataclass
class ParseResult:
    content_sha256: str
    page_count: int
    total_page_count: int
    truncated: bool
    is_encrypted: bool
    doc_rendering: str
    doc_genre: str
    primary_language: str | None
    languages: list[str]
    parse_status: str
    parse_error: str | None
    pages: list[ParsedPage] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    ai_calls: list[LlmCall] = field(default_factory=list)
    telemetry: Telemetry = field(default_factory=Telemetry)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": {
                "content_sha256": self.content_sha256,
                "page_count": self.page_count,
                "total_page_count": self.total_page_count,
                "truncated": self.truncated,
                "is_encrypted": self.is_encrypted,
                "rendering": self.doc_rendering,
                "genre": self.doc_genre,
                "primary_language": self.primary_language,
                "languages": self.languages,
                "parse_status": self.parse_status,
                "parse_error": self.parse_error,
                "parse_ms": self.telemetry.timings.get("parse_ms", 0),
            },
            "pages": [p.to_dict() for p in self.pages],
            "sections": self.sections,
            "tables": self.tables,
            "images": self.images,
            "ai_calls": [c.to_log_dict() for c in self.ai_calls],
            "timings": self.telemetry.timings_dict(),
            "usage": self.telemetry.usage.to_dict(),
        }


def _failed(reason: str, detail: str, telemetry: Telemetry) -> ParseResult:
    """A document that could not be opened is a *document* failure, not a message failure (E7).

    It still returns a complete envelope so the message keeps moving: the email body is still
    classified, the completion barrier still clears, and the item reaches the reviewer flagged
    rather than vanishing from the queue.
    """
    return ParseResult(
        content_sha256="",
        page_count=0,
        total_page_count=0,
        truncated=False,
        is_encrypted=(reason == "ENCRYPTED"),
        doc_rendering="DIGITAL",
        doc_genre="LETTER",
        primary_language=None,
        languages=[],
        parse_status="PARSE_FAILED",
        parse_error=f"{reason}: {detail}",
        telemetry=telemetry,
    )


def parse_document(
    data: bytes,
    *,
    filename: str | None = None,
    client: LlmClient | None = None,
    settings: Settings | None = None,
    use_vision: bool = True,
) -> ParseResult:
    """Parse one document end to end."""
    settings = settings or get_settings()
    telemetry = Telemetry()
    started = time.perf_counter()

    try:
        with telemetry.stage("load"):
            document = load(data, settings=settings, filename=filename)
    except DocumentLoadError as exc:
        log.warning("Parse failed for %s — %s", filename or "<bytes>", exc)
        telemetry.record_ms("parse", int((time.perf_counter() - started) * 1000))
        return _failed(exc.failure.value, exc.detail, telemetry)

    with document:
        result = _parse_open_document(
            document, telemetry, client=client, settings=settings, use_vision=use_vision)

    telemetry.record_ms("parse", int((time.perf_counter() - started) * 1000))
    return result


def _parse_open_document(
    document: LoadedDocument,
    telemetry: Telemetry,
    *,
    client: LlmClient | None,
    settings: Settings,
    use_vision: bool,
) -> ParseResult:
    layouts: list[tuple[int, PageLayout, str]] = []
    flavours: list[flavour_mod.PageFlavour] = []
    pages: list[ParsedPage] = []
    tables: list[ExtractedTable] = []
    ai_calls: list[LlmCall] = []
    page_languages = []

    for index, page in document.pages():
        page_no = index + 1

        with telemetry.stage("layout"):
            layout = layout_page(page, settings)

        with telemetry.stage("flavour"):
            page_flavour = flavour_mod.detect_page_flavour(page, layout, page_no, settings)

        with telemetry.stage("render"):
            rendered = render_page(page, document.content_sha256, page_no, settings=settings)

        text_original = layout.text
        legibility = 1.0
        uncertain = 0
        language = page_flavour.language
        lang_confidence = page_flavour.lang_confidence

        # --- scanned page: the only way to read it is to look at it (E13, E34) ---
        if page_flavour.rendering == flavour_mod.RENDERING_SCANNED and use_vision and client:
            with telemetry.stage("vision"):
                transcription, call = transcribe_page(client, rendered)
            ai_calls.append(call)
            telemetry.add_usage(call.usage)
            text_original = transcription.text
            legibility = legibility_ceiling(transcription)
            uncertain = sum(1 for s in transcription.segments if s.uncertain)
            # The model returns a language *name* as often as a code; normalise before
            # comparing, or every scanned English page triggers a needless translation.
            reported = normalise_language(transcription.language)
            if reported:
                language, lang_confidence = reported, 0.7

        # --- non-English: translate alongside, never instead of (E16) ---
        text_english = None
        if use_vision and client and language and language != "en":
            with telemetry.stage("translate"):
                if page_flavour.rendering == flavour_mod.RENDERING_SCANNED:
                    text_english, call = _translate_plain(client, text_original, language)
                else:
                    text_english, call = translate_page(client, layout, language)
            if call is not None:
                ai_calls.append(call)
                telemetry.add_usage(call.usage)

        with telemetry.stage("tables"):
            page_tables = extract_page_tables(
                page, layout, page_no, start_index=len(tables), settings=settings)
            tables.extend(page_tables.tables)

        layouts.append((page_no, layout, page_flavour.genre))
        flavours.append(page_flavour)
        if page_flavour.rendering != flavour_mod.RENDERING_SCANNED:
            from app.lang.detect import detect_page
            page_languages.append(detect_page([b.text for b in layout.blocks], settings))

        pages.append(ParsedPage(
            page_no=page_no,
            rendering=page_flavour.rendering,
            genre=page_flavour.genre,
            language=language,
            lang_confidence=lang_confidence,
            column_count=layout.column_count,
            char_count=len(text_original.strip()),
            has_text_layer=page_flavour.has_text_layer,
            legibility=legibility,
            width=layout.width,
            height=layout.height,
            rotation=document.rotations.get(index, 0),
            text_original=text_original,
            text_english=text_english,
            render_path=rendered.relative_to(settings.render_dir.parent),
            span_index=layout.span_index_json(),
            scanned_reason=page_flavour.scanned_reason,
            genre_reason=page_flavour.genre_reason,
            uncertain_segments=uncertain,
        ))

    # --- cross-page table merge on a repeated header row (E18) ---
    with telemetry.stage("tables"):
        tables = merge_continuations(tables)

    # --- sections, carrying an open section across page breaks (E15) ---
    with telemetry.stage("sections"):
        sections = segment_document(layouts)

    # --- meaningful images only (E19) ---
    images: list[dict[str, Any]] = []
    with telemetry.stage("images"):
        candidates = collect_candidates(document.doc, document.page_count, settings)
    for candidate in candidates:
        row = candidate.to_dict()
        if candidate.keep and use_vision and client:
            try:
                with telemetry.stage("vision"):
                    description, call, path = describe_embedded(
                        client, document.doc, candidate, document.content_sha256, settings)
                ai_calls.append(call)
                telemetry.add_usage(call.usage)
                row.update({
                    "category": description.category.value,
                    "description": description.description,
                    "mentions_defect": description.mentions_defect,
                    "mentions_injury": description.mentions_injury,
                    "contains_text": description.contains_text,
                    "confidence": description.confidence,
                    "blob_path": path,
                })
            except Exception as exc:  # one bad image must not fail the document
                log.warning("Could not describe image on page %d: %s", candidate.page_no, exc)
                row["description"] = f"(description unavailable: {exc})"
        # The brief requires human review of image interpretation — not the model's call.
        row["needs_review"] = "Y"
        images.append(row)

    doc_rendering, doc_genre = flavour_mod.roll_up(flavours)
    primary_language, languages = roll_up_document(page_languages)

    # A scanned document has no block-level detection to roll up, so fall back to whatever the
    # transcription reported for its pages.
    if primary_language is None:
        reported = [p.language for p in pages if p.language]
        if reported:
            primary_language = max(set(reported), key=reported.count)
            languages = sorted(set(reported))

    return ParseResult(
        content_sha256=document.content_sha256,
        page_count=document.page_count,
        total_page_count=document.total_page_count,
        truncated=document.truncated,
        is_encrypted=document.is_encrypted,
        doc_rendering=doc_rendering,
        doc_genre=doc_genre,
        primary_language=primary_language,
        languages=languages,
        parse_status="PARSED",
        parse_error=None,
        pages=pages,
        sections=[s.to_dict() for s in sections],
        tables=[t.to_dict() for t in tables],
        images=images,
        ai_calls=ai_calls,
        telemetry=telemetry,
    )


def _translate_plain(client: LlmClient, text: str, language: str):
    """Translate transcribed text, which has no layout blocks to work from."""
    from app.pipeline.translate import translate_blocks

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return None, None
    result, call = translate_blocks(client, paragraphs)
    by_index = {b.index: b.english for b in result.blocks}
    english = "\n\n".join(by_index.get(i, paragraphs[i]) for i in range(len(paragraphs)))
    return english, call


def parse_email_body(text: str, *, html: str | None = None) -> ParseResult:
    """An email body is a document too (E11).

    Given one uniform shape, everything downstream — classification, extraction, evidence
    verification, the review UI — has a single code path instead of a weaker second one for the
    case that actually happens most often.
    """
    telemetry = Telemetry()
    from app.lang.detect import detect_page
    from app.pdf.loader import sha256_bytes

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    page_language = detect_page(paragraphs or [text])

    page = ParsedPage(
        page_no=0,                       # 0 marks the email body, per the field conventions
        rendering="DIGITAL",
        genre="LETTER",
        language=page_language.primary_language,
        lang_confidence=page_language.confidence,
        column_count=1,
        char_count=len(text.strip()),
        has_text_layer=True,
        legibility=1.0,
        width=0.0,
        height=0.0,
        rotation=0,
        text_original=text,
        text_english=None,
        render_path="",
        span_index=[],                   # no geometry: evidence here is offsets only
    )

    return ParseResult(
        content_sha256=sha256_bytes(text.encode("utf-8")),
        page_count=1,
        total_page_count=1,
        truncated=False,
        is_encrypted=False,
        doc_rendering="DIGITAL",
        doc_genre="LETTER",
        primary_language=page_language.primary_language,
        languages=page_language.languages,
        parse_status="PARSED",
        parse_error=None,
        pages=[page],
        telemetry=telemetry,
    )
