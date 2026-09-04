"""Vision transcription of pages with no text layer (E13, E34).

This is where the single-model constraint is honoured in the most concrete way. A scanned page
has nothing to extract, so it must be *read*. We render it ourselves with PyMuPDF and send a PNG
to Claude's own vision, rather than enabling OpenRouter's PDF plugin — whose default engine is
another vendor's OCR model.

The output carries a `legibility` score, and that score is not decoration. It becomes a ceiling
on the confidence of every fact later drawn from the page:

    final_field_confidence = min(model_field_confidence, page_legibility)

That is E34: uncertainty about the handwriting has to flow all the way down to the field, not
stop at the OCR step. A reviewer looking at a value taken from a barely readable form should see
a low number next to it, and they do.
"""

from __future__ import annotations

import logging

from app.llm.client import LlmCall, LlmClient, image_part, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import PageTranscription
from app.pdf.render import RenderedPage

log = logging.getLogger("smartinbox.ai.pipeline.transcribe")

PROMPT_ID = "P5_transcribe"


def transcribe_page(
    client: LlmClient,
    rendered: RenderedPage,
) -> tuple[PageTranscription, LlmCall]:
    """Read one rendered page image."""
    prompt = load(PROMPT_ID)

    call = client.complete_json(
        purpose=PROMPT_ID,
        system_prompt=system_prompt().text,
        user_content=[
            text_part(prompt.text),
            image_part(rendered.as_base64()),
        ],
        schema_model=PageTranscription,
        prompt_version=prompt.label,
        max_tokens=6000,
    )
    result = PageTranscription.model_validate(call.parsed)

    log.info(
        "Transcribed page %d: %d chars, legibility %.2f, %d uncertain segment(s)",
        rendered.page_no, len(result.text), result.legibility,
        sum(1 for s in result.segments if s.uncertain))

    return result, call


def legibility_ceiling(transcription: PageTranscription) -> float:
    """The confidence cap this page imposes on anything extracted from it (E34).

    A blank page gets 1.0 rather than 0.0 — there is nothing on it to be uncertain about, and
    capping every field at zero because a page happened to be empty would be nonsense.
    """
    if transcription.is_blank:
        return 1.0
    return max(0.0, min(1.0, transcription.legibility))
