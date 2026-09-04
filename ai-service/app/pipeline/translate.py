"""Block-wise translation to English (E16).

The decision this module exists to enforce: **the original text is canonical and the translation
never becomes evidence.**

A non-English document is translated for the reviewer's benefit, and `text_english` is stored
alongside `text_original`. But extraction runs on the original, every evidence quote is in the
source language, and every character offset indexes the original. Translating first and
extracting from the translation would be easier and is quietly wrong: the audit trail would then
point at a machine-generated English paraphrase rather than at what the reporter actually wrote,
and in a regulated context the source document is the record.

Translation is done block by block, keeping block indices, so the alignment between original and
English survives. Translating the whole page as one blob loses the correspondence and makes the
side-by-side view in the UI impossible.
"""

from __future__ import annotations

import logging

from app.llm.client import LlmCall, LlmClient, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import TranslationResult
from app.pdf.layout import PageLayout

log = logging.getLogger("smartinbox.ai.pipeline.translate")

PROMPT_ID = "P8_translate"

# Below this, translating costs more than it is worth — a page of form labels adds nothing for
# a reviewer who can see the original next to it.
MIN_CHARS_TO_TRANSLATE = 120


def needs_translation(language: str | None) -> bool:
    """English and unknown-language pages are left alone.

    Unknown is deliberate: if language detection declined to assert a language (E17), guessing
    that it is foreign and translating it would be acting on an assumption we explicitly refused
    to make one step earlier.
    """
    return bool(language) and language != "en"


def translate_blocks(
    client: LlmClient,
    blocks: list[str],
) -> tuple[TranslationResult, LlmCall]:
    """Translate numbered blocks, preserving their indices."""
    prompt = load(PROMPT_ID)
    numbered = "\n\n".join(f"[{i}] {text}" for i, text in enumerate(blocks) if text.strip())

    call = client.complete_json(
        purpose=PROMPT_ID,
        system_prompt=system_prompt().text,
        user_content=[text_part(prompt.render(blocks=numbered))],
        schema_model=TranslationResult,
        prompt_version=prompt.label,
        max_tokens=8000,
    )
    return TranslationResult.model_validate(call.parsed), call


def translate_page(
    client: LlmClient,
    layout: PageLayout,
    language: str | None,
) -> tuple[str | None, LlmCall | None]:
    """Return `text_english` for a page, or `(None, None)` when translation is not warranted."""
    if not needs_translation(language):
        return None, None
    if len(layout.text.strip()) < MIN_CHARS_TO_TRANSLATE:
        return None, None

    blocks = [b.text for b in layout.blocks if b.text.strip()]
    if not blocks:
        return None, None

    result, call = translate_blocks(client, blocks)

    # Reassemble in the original block order. A block the model skipped falls back to its
    # source text rather than leaving a hole — an untranslated line is far better than a
    # silently missing one.
    by_index = {b.index: b.english for b in result.blocks}
    english = "\n\n".join(
        by_index.get(i, blocks[i]) for i in range(len(blocks)))

    log.info("Translated %d block(s) from %s to English", len(blocks), language)
    return english, call
