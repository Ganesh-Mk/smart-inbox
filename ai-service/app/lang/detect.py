"""Language detection at block level, rolled up to the page (E17).

Why block level rather than document level: a very common real shape is an English form
wrapping a German narrative — the labels say "Patient initials" and the free text says
"Der Patient wurde stationär behandelt". Ask a document-level detector and it answers
"English", because the labels outnumber the prose. Extraction then runs with the wrong
language assumption, translation is skipped, and the actual clinical content is the part that
was misjudged.

So detection runs per block, and the page's `primary_language` is the modal language weighted
by **content**, not by block count — the narrative is what matters, and it is one long block
against a dozen short labels.

`lingua-py` rather than `langdetect`: it is materially better on short strings (form labels are
one or two words) and it returns a real confidence, which lets us decline to assert a language
rather than guessing one. Below the confidence threshold we say `None` and defer to the model's
own reported language — an honest "unknown" beats a confident mislabel.

Three guards stop confident nonsense, each added after watching it happen on the corpus
(DECISIONS D-012):

* **at least three words** — "HOSPITALISATION_OR_PROLONGATION" is one 31-character token, and
  lingua calls it French at 0.82;
* **prose, not table data** — a lab cell is mostly letters but is measurements, and a page of
  them joined together reads as German at 0.92;
* **CJK weighted by content** — 163 Japanese characters carry more than 223 Latin ones, and
  without the correction the Japanese form is reported as English.
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
    content_weight: float = 0.0


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


def _is_prose_like(
    text: str,
    minimum_letter_ratio: float = 0.70,
    maximum_digit_ratio: float = 0.12,
) -> bool:
    """True when the text is running prose rather than tabular data.

    Tabular content has no language to detect, and asking anyway yields a confident wrong
    answer instead of an admission of ignorance.

    Two tests, and the digit one does the real work. A letter ratio alone is far too generous:
    the lab cell "Alanine aminotransferase640U/L10 - 40H" is 76% letters and spaces because the
    long analyte name dominates, so it passes a letter test comfortably — and a page of those
    joined together is reported as German at 0.92 confidence. What actually distinguishes it
    from prose is digits fused into words. Running text of any language is around 1-2% digits;
    that cell is 18%.
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    lettery = sum(1 for ch in stripped if ch.isalpha() or ch.isspace())
    digits = sum(1 for ch in stripped if ch.isdigit())
    return (lettery / len(stripped) >= minimum_letter_ratio
            and digits / len(stripped) <= maximum_digit_ratio)


# A CJK character carries roughly as much content as two to three Latin ones, so a raw
# character count systematically under-weights CJK text. Without this correction the Japanese
# corpus form is reported as English: its 163-character Japanese narrative loses to 223
# characters of Latin furniture (the product name, the marketing-authorisation address and the
# synthetic-data footer), even though the narrative is plainly the document's content.
CJK_CONTENT_WEIGHT = 2.5


def _content_weight(text: str) -> float:
    """Character count adjusted so scripts are weighed by content, not by code points."""
    stripped = text.strip()
    if not stripped:
        return 0.0
    cjk = sum(1 for ch in stripped if _is_cjk(ch))
    return (len(stripped) - cjk) + cjk * CJK_CONTENT_WEIGHT


def _is_cjk(text: str) -> bool:
    """CJK scripts do not use spaces, so the word-count rule cannot apply to them."""
    for ch in text:
        code = ord(ch)
        if (0x3040 <= code <= 0x30FF        # hiragana, katakana
                or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
                or 0xAC00 <= code <= 0xD7AF):  # hangul
            return True
    return False


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

    # Length alone is not enough: a single long token is not evidence of a language. The enum
    # value "HOSPITALISATION_OR_PROLONGATION" is 31 characters and lingua reports it as French
    # at 0.82 confidence, which was enough to make an English form claim two languages. A
    # language judgement needs a phrase, so require at least three words.
    # Underscores are deliberately NOT treated as word separators here: the whole point is
    # that HOSPITALISATION_OR_PROLONGATION is one token, not three words.
    if len(stripped.split()) < 3 and not _is_cjk(stripped):
        return None, 0.0

    # Tabular content has no language. A cell like "Alanine aminotransferase640U/L10 - 40H"
    # clears the word count but is measurements, not prose.
    if not _is_prose_like(stripped) and not _is_cjk(stripped):
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
    weights: dict[str, float] = {}
    weighted_confidence: dict[str, float] = {}

    for text in block_texts:
        language, confidence = detect_text(text, settings)
        raw_length = len((text or "").strip())
        length = _content_weight(text or "")
        blocks.append(BlockLanguage(raw_length, language, confidence, length))
        if language:
            weights[language] = weights.get(language, 0) + length
            weighted_confidence[language] = weighted_confidence.get(language, 0.0) \
                + confidence * length

    if not weights:
        # No single block was judgeable. Falling back to the whole page as one string helps a
        # page broken into many short prose blocks — but only if the result is actually prose.
        #
        # Without that guard this fallback is actively harmful. A page holding nothing but a
        # laboratory table joins into
        #   "TestResultUnitsReference rangeFlagAlanine aminotransferase640U/L10 - 40H..."
        # which lingua reports as German at 1.00 confidence. That then becomes the page's
        # primary_language, triggers a translation pass on a table of numbers, and puts the
        # wrong language on every piece of evidence drawn from the page.
        # Join only blocks that are individually prose. Joining everything is what lets a page
        # of lab values clear the prose bar by accident: each cell is 68% letters, but the
        # concatenation reaches 75% and sails past a whole-page threshold. Filtering first
        # means a page with no prose in it produces no language at all, which is the truth.
        joined = "\n".join(t for t in block_texts if t and _is_prose_like(t))
        if not joined.strip():
            return PageLanguage(None, 0.0, [], False, blocks)
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
    confidence = weighted_confidence[primary] / max(weights[primary], 1.0)

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
    weights: dict[str, float] = {}
    for page in page_languages:
        for block in page.blocks:
            if block.language:
                # Same content weighting as detect_page, so a document roll-up cannot
                # contradict the pages it is built from.
                weights[block.language] = weights.get(block.language, 0.0) + block.content_weight
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
