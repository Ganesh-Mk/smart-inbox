"""Language detection at block level, rolled up to the page (E17).

Why block level rather than document level: a very common real shape is an English form
wrapping a German narrative — the labels say "Patient initials" and the free text says
"Der Patient wurde stationär behandelt". Ask a document-level detector and it answers
"English", because the labels outnumber the prose. Extraction then runs with the wrong
language assumption, translation is skipped, and the actual clinical content is the part that
was misjudged.

So detection runs per block, and the page's `primary_language` is the modal language weighted
by **character count**, not block count — the narrative is what matters, and it is one long
block against a dozen short labels.

`lingua-py` rather than `langdetect`: it is materially better on short strings (form labels are
one or two words) and it returns a real confidence, which lets us decline to assert a language
rather than guessing one. Below the confidence threshold we say `None` and defer to the model's
own reported language — an honest "unknown" beats a confident mislabel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Sequence

from lingua import Language, LanguageDetectorBuilder

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.lang")

# Restricting the candidate set sharply improves accuracy on short text. These are the
# languages the corpus uses plus the European languages a real safety mailbox actually sees.
CANDIDATE_LANGUAGES = (
    Language.ENGLISH,
    Language.GERMAN,
    Language.FRENCH,
    Language.SPANISH,
    Language.ITALIAN,
    Language.DUTCH,
    Language.PORTUGUESE,
    Language.SWEDISH,
    Language.DANISH,
    Language.POLISH,
    Language.JAPANESE,
    Language.CHINESE,
)

ISO_639_1 = {
    Language.ENGLISH: "en",
    Language.GERMAN: "de",
    Language.FRENCH: "fr",
    Language.SPANISH: "es",
    Language.ITALIAN: "it",
    Language.DUTCH: "nl",
    Language.PORTUGUESE: "pt",
    Language.SWEDISH: "sv",
    Language.DANISH: "da",
    Language.POLISH: "pl",
    Language.JAPANESE: "ja",
    Language.CHINESE: "zh",
}


@dataclass
class BlockLanguage:
    """One block's verdict."""

    text_length: int
    language: str | None
    confidence: float


@dataclass
class PageLanguage:
    """The page roll-up."""

    primary_language: str | None
    confidence: float
    languages: list[str]
    is_mixed: bool
    blocks: list[BlockLanguage]

    def to_dict(self) -> dict[str, object]:
        return {
            "primary_language": self.primary_language,
            "lang_confidence": round(self.confidence, 4),
            "languages": self.languages,
            "is_mixed": self.is_mixed,
        }


@lru_cache(maxsize=1)
def _detector():
    # Building the detector loads language models; it is expensive and thread-safe, so it is
    # built once and reused for the life of the process.
    return (
        LanguageDetectorBuilder.from_languages(*CANDIDATE_LANGUAGES)
        .with_preloaded_language_models()
        .build()
    )


def detect_text(text: str, settings: Settings | None = None) -> tuple[str | None, float]:
    """`(iso code, confidence)` for one string, or `(None, 0.0)` when we decline to say."""
    settings = settings or get_settings()
    stripped = (text or "").strip()
    if len(stripped) < settings.lang_min_chars:
        # Too short to be reliable. Saying nothing is the correct answer; a guess here is
        # what produces a document labelled Danish because someone wrote "Dato".
        return None, 0.0

    values = _detector().compute_language_confidence_values(stripped)
    if not values:
        return None, 0.0

    best = values[0]
    confidence = float(best.value)
    if confidence < settings.lang_min_confidence:
        return None, confidence
    return ISO_639_1.get(best.language), confidence


def detect_page(
    block_texts: Sequence[str],
    settings: Settings | None = None,
) -> PageLanguage:
    """Detect per block, then roll up to the page weighted by character count."""
    settings = settings or get_settings()

    blocks: list[BlockLanguage] = []
    weights: dict[str, int] = {}
    weighted_confidence: dict[str, float] = {}

    for text in block_texts:
        language, confidence = detect_text(text, settings)
        length = len((text or "").strip())
        blocks.append(BlockLanguage(length, language, confidence))
        if language:
            weights[language] = weights.get(language, 0) + length
            weighted_confidence[language] = weighted_confidence.get(language, 0.0) \
                + confidence * length

    if not weights:
        # Nothing was long enough or confident enough. Fall back to the whole page as one
        # string, which at least has the length to be judged.
        joined = "\n".join(t for t in block_texts if t)
        language, confidence = detect_text(joined, settings)
        return PageLanguage(
            primary_language=language,
            confidence=confidence,
            languages=[language] if language else [],
            is_mixed=False,
            blocks=blocks,
        )

    total_chars = sum(weights.values())
    primary = max(weights, key=lambda k: weights[k])
    confidence = weighted_confidence[primary] / max(weights[primary], 1)

    # A language is worth listing only if it carries a real share of the page — otherwise a
    # single mis-detected label makes every document look multilingual.
    languages = sorted(
        (lang for lang, chars in weights.items() if chars / total_chars >= 0.15),
        key=lambda lang: -weights[lang],
    )
    if primary not in languages:
        languages.insert(0, primary)

    return PageLanguage(
        primary_language=primary,
        confidence=confidence,
        languages=languages,
        is_mixed=len(languages) > 1,
        blocks=blocks,
    )


def roll_up_document(page_languages: Iterable[PageLanguage]) -> tuple[str | None, list[str]]:
    """Document `primary_language` and the full set present, weighted by page content."""
    weights: dict[str, int] = {}
    for page in page_languages:
        for block in page.blocks:
            if block.language:
                weights[block.language] = weights.get(block.language, 0) + block.text_length
    if not weights:
        return None, []
    primary = max(weights, key=lambda k: weights[k])
    total = sum(weights.values())
    present = sorted(
        (lang for lang, chars in weights.items() if chars / total >= 0.10),
        key=lambda lang: -weights[lang],
    )
    if primary not in present:
        present.insert(0, primary)
    return primary, present
