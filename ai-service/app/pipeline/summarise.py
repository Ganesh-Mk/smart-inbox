"""Document summaries with a relevance verdict (R7), and map-reduce for long documents (E20).

A 200-page PDF does not fit in a prompt, and stuffing as much as fits and calling it a summary
is quietly dishonest — the result reads complete while silently omitting everything past the
cut. So long documents are summarised in groups and the group summaries are summarised again.

The sentence count is checked rather than trusted. The brief asks for 10 to 15 sentences and
the model drifts, so the count is measured and recorded; the evaluation harness reports how
often it was met.
"""

from __future__ import annotations

import logging
import re

from app.llm.client import LlmCall, LlmClient, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import DocumentSummary
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pipeline.summarise")

PROMPT_ID = "P6_summarise"

# Abbreviations that would otherwise be counted as sentence ends.
_ABBREVIATIONS = re.compile(
    r"\b(Dr|Mr|Mrs|Ms|Prof|St|approx|e\.g|i\.e|etc|vs|no|Fig|Ref|mg|ml)\.\s*$",
    re.IGNORECASE)


def count_sentences(text: str) -> int:
    """Count sentences without splitting on "Dr." or "500 mg." and inflating the number."""
    if not text or not text.strip():
        return 0
    pieces = re.split(r"(?<=[.!?])\s+", text.strip())
    count = 0
    buffer = ""
    for piece in pieces:
        buffer = f"{buffer} {piece}".strip() if buffer else piece
        if _ABBREVIATIONS.search(buffer):
            continue
        if buffer:
            count += 1
            buffer = ""
    if buffer:
        count += 1
    return count


def summarise_text(
    client: LlmClient,
    source_text: str,
    settings: Settings | None = None,
) -> tuple[DocumentSummary, list[LlmCall]]:
    """Summarise a document, chunking it first when it is too long to send in one call."""
    settings = settings or get_settings()
    prompt = load(PROMPT_ID)
    calls: list[LlmCall] = []

    def one_call(text: str) -> DocumentSummary:
        call = client.complete_json(
            purpose=PROMPT_ID,
            system_prompt=system_prompt().text,
            user_content=[text_part(prompt.render(source_text=text))],
            schema_model=DocumentSummary,
            prompt_version=prompt.label,
            max_tokens=3000,
        )
        calls.append(call)
        return DocumentSummary.model_validate(call.parsed)

    if len(source_text) <= settings.summary_chunk_chars:
        summary = one_call(source_text)
    else:
        # --- map-reduce (E20) ---
        chunks = _chunk(source_text, settings.summary_chunk_chars)
        log.info("Document is %d chars; summarising in %d chunks then reducing",
                 len(source_text), len(chunks))
        partials = [one_call(chunk) for chunk in chunks]
        combined = "\n\n".join(
            f"[Part {i + 1} of {len(partials)}]\n{p.summary}" for i, p in enumerate(partials))
        summary = one_call(combined)
        # The reduce step only sees the partial summaries, so the relevance verdict is taken
        # from the most confident part rather than from a summary of summaries.
        strongest = max(partials, key=lambda p: 0 if p.relevance.value == "NOT_RELEVANT" else 1)
        summary.relevance = strongest.relevance
        summary.relevance_reason = strongest.relevance_reason

    actual = count_sentences(summary.summary)
    if not (settings.summary_min_sentences <= actual <= settings.summary_max_sentences):
        log.info("Summary has %d sentences, outside the requested %d-%d band",
                 actual, settings.summary_min_sentences, settings.summary_max_sentences)
    summary.sentence_count = actual

    return summary, calls


def _chunk(text: str, size: int) -> list[str]:
    """Split on paragraph boundaries, never mid-sentence."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for paragraph in paragraphs:
        if length + len(paragraph) > size and current:
            chunks.append("\n\n".join(current))
            current, length = [], 0
        current.append(paragraph)
        length += len(paragraph) + 2

    if current:
        chunks.append("\n\n".join(current))
    return chunks
