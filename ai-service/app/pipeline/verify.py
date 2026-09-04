"""Evidence verification — proving the model's citations instead of believing them (E27).

This is the differentiator of the whole submission, so it is worth being precise about what it
does and why.

The brief says traceability is "required, not optional". The obvious implementation makes
`source` a string the model fills in: *"page 2"*, *"the patient section"*. That is worth very
little. Models hallucinate citations as readily as they hallucinate facts, and **a fabricated
citation in a regulated system is worse than no citation at all**, because it manufactures
confidence a reviewer will act on.

So provenance is treated as a machine-verified data type. The model states a quote; this module
searches for that quote in the page it claims to come from. Three outcomes:

* **EXACT** — found after Unicode and whitespace normalisation. Offsets are rewritten to the
  real position and a bounding box is resolved from the span index.
* **FUZZY** — found at a similarity of 90 or better, via `rapidfuzz`. Models routinely
  re-punctuate or drop a word while quoting; that is a citation worth keeping, not a fabrication.
* **FAILED** — not there. `verified='N'`, and the field's confidence is **capped at 0.40**.

The offsets we store are always the ones *we* found, never the ones the model reported. The
model's numbers are a hint about where to look and nothing more.

The consequence a reviewer sees: clicking an evidence chip highlights the exact source text, and
an unverifiable citation shows an amber chip reading "cited but not found in source" — the
system reporting its own hallucination rather than hiding it.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Sequence

from rapidfuzz import fuzz

from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pipeline.verify")

METHOD_EXACT = "EXACT"
METHOD_FUZZY = "FUZZY"
METHOD_FAILED = "FAILED"

# Characters that differ between what a PDF contains and what a model reproduces, without any
# difference in meaning. Normalising these is what makes exact matching work at all.
_QUOTE_CHARS = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "′": "'", "″": '"',
}
_DASH_CHARS = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
}
_SPACE_CHARS = {
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", "﻿": "",
}

_TRANSLATION = str.maketrans({**_QUOTE_CHARS, **_DASH_CHARS, **_SPACE_CHARS})


@dataclass
class NormalisedText:
    """Normalised text plus a map back to offsets in the original.

    The map is the whole point. Matching has to happen on normalised text — otherwise a curly
    apostrophe defeats it — but the offsets we store must index the *original*, because that is
    what the UI highlights and what the reviewer reads.
    """

    text: str
    offsets: list[int]

    def original_span(self, start: int, end: int) -> tuple[int, int]:
        if not self.offsets:
            return start, end
        start = max(0, min(start, len(self.offsets) - 1))
        end = max(0, min(end, len(self.offsets)))
        original_start = self.offsets[start]
        original_end = self.offsets[end - 1] + 1 if end > start else original_start
        return original_start, original_end


def normalise(text: str) -> NormalisedText:
    """NFKC, unify quotes and dashes, collapse whitespace — keeping an offset map."""
    if not text:
        return NormalisedText("", [])

    out_chars: list[str] = []
    out_offsets: list[int] = []
    previous_was_space = True  # strips leading whitespace

    for index, char in enumerate(text):
        folded = unicodedata.normalize("NFKC", char.translate(_TRANSLATION))
        if not folded:
            continue
        for piece in folded:
            if piece.isspace():
                if previous_was_space:
                    continue
                out_chars.append(" ")
                out_offsets.append(index)
                previous_was_space = True
            else:
                out_chars.append(piece.casefold())
                out_offsets.append(index)
                previous_was_space = False

    while out_chars and out_chars[-1] == " ":
        out_chars.pop()
        out_offsets.pop()

    return NormalisedText("".join(out_chars), out_offsets)


@dataclass
class VerificationResult:
    """What verification concluded about one quote."""

    verified: bool
    method: str
    match_score: float
    char_start: int | None
    char_end: int | None
    matched_text: str | None
    page_no: int | None
    bbox: tuple[float, float, float, float] | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": "Y" if self.verified else "N",
            "verify_method": self.method,
            "match_score": round(self.match_score, 2),
            "char_start": self.char_start,
            "char_end": self.char_end,
            "page_no": self.page_no,
            "bbox": ",".join(f"{v:.2f}" for v in self.bbox) if self.bbox else None,
            "note": self.note,
        }


def _fuzzy_find(needle: str, haystack: str, threshold: float) -> tuple[int, int, float] | None:
    """Best fuzzy window for `needle` in `haystack`, or None below `threshold`.

    A sliding window sized to the quote, stepped at a quarter of its length. `partial_ratio`
    alone gives a score but not a position, and the position is what we need in order to rewrite
    the offsets honestly.
    """
    if not needle or not haystack:
        return None

    window = len(needle)
    if window > len(haystack):
        score = fuzz.partial_ratio(needle, haystack)
        return (0, len(haystack), score) if score >= threshold else None

    step = max(1, window // 4)
    best: tuple[int, int, float] | None = None

    for start in range(0, len(haystack) - window + 1, step):
        candidate = haystack[start:start + window]
        score = fuzz.ratio(needle, candidate)
        if best is None or score > best[2]:
            best = (start, start + window, score)

    if best is None or best[2] < threshold:
        return None

    # Refine around the best coarse hit, since the step may have skipped the true alignment.
    coarse_start = best[0]
    refined = best
    for start in range(max(0, coarse_start - step), min(len(haystack) - window, coarse_start + step) + 1):
        candidate = haystack[start:start + window]
        score = fuzz.ratio(needle, candidate)
        if score > refined[2]:
            refined = (start, start + window, score)

    return refined if refined[2] >= threshold else None


def verify_quote(
    quote: str,
    page_text: str,
    *,
    page_no: int | None = None,
    settings: Settings | None = None,
) -> VerificationResult:
    """Prove (or fail to prove) that `quote` occurs in `page_text`."""
    settings = settings or get_settings()

    if not quote or not quote.strip():
        return VerificationResult(
            verified=False, method=METHOD_FAILED, match_score=0.0,
            char_start=None, char_end=None, matched_text=None, page_no=page_no,
            note="no quote supplied")

    if not page_text or not page_text.strip():
        return VerificationResult(
            verified=False, method=METHOD_FAILED, match_score=0.0,
            char_start=None, char_end=None, matched_text=None, page_no=page_no,
            note="source page has no text to search")

    needle = normalise(quote)
    haystack = normalise(page_text)

    if not needle.text:
        return VerificationResult(
            verified=False, method=METHOD_FAILED, match_score=0.0,
            char_start=None, char_end=None, matched_text=None, page_no=page_no,
            note="quote normalised to nothing")

    # --- exact ---
    position = haystack.text.find(needle.text)
    if position >= 0:
        start, end = haystack.original_span(position, position + len(needle.text))
        return VerificationResult(
            verified=True, method=METHOD_EXACT, match_score=100.0,
            char_start=start, char_end=end,
            matched_text=page_text[start:end], page_no=page_no)

    # --- fuzzy ---
    match = _fuzzy_find(needle.text, haystack.text, settings.evidence_fuzzy_threshold)
    if match is not None:
        position, end_position, score = match
        start, end = haystack.original_span(position, end_position)
        return VerificationResult(
            verified=True, method=METHOD_FUZZY, match_score=float(score),
            char_start=start, char_end=end,
            matched_text=page_text[start:end], page_no=page_no,
            note=f"matched at {score:.0f}% similarity, not verbatim")

    # --- failed: report the best score we saw, so a near-miss is distinguishable from nonsense ---
    best_score = float(fuzz.partial_ratio(needle.text, haystack.text))
    return VerificationResult(
        verified=False, method=METHOD_FAILED, match_score=best_score,
        char_start=None, char_end=None, matched_text=None, page_no=page_no,
        note=f"quote not found in the cited source (best similarity {best_score:.0f}%)")


def verify_against_pages(
    quote: str,
    pages: Sequence[tuple[int, str]],
    *,
    cited_page: int | None = None,
    settings: Settings | None = None,
) -> VerificationResult:
    """Verify a quote, trying the cited page first and then every other page.

    Searching the other pages is deliberate. A model that quotes a real sentence but attributes
    it to the wrong page has made a *citation* error, not a factual one, and the honest response
    is to correct the page number rather than to reject a true quote. That distinction is
    recorded in the note, so the reviewer can see the model got the page wrong.
    """
    settings = settings or get_settings()
    by_page = dict(pages)

    if cited_page is not None and cited_page in by_page:
        result = verify_quote(
            quote, by_page[cited_page], page_no=cited_page, settings=settings)
        if result.verified:
            return result

    best_failure: VerificationResult | None = None
    for page_no, text in pages:
        if page_no == cited_page:
            continue
        result = verify_quote(quote, text, page_no=page_no, settings=settings)
        if result.verified:
            if cited_page is not None:
                result.note = (
                    f"found on page {page_no}, but the model cited page {cited_page}"
                    + (f"; {result.note}" if result.note else ""))
            return result
        if best_failure is None or result.match_score > best_failure.match_score:
            best_failure = result

    if best_failure is not None:
        best_failure.page_no = cited_page
        return best_failure

    return VerificationResult(
        verified=False, method=METHOD_FAILED, match_score=0.0,
        char_start=None, char_end=None, matched_text=None, page_no=cited_page,
        note="no source pages available to verify against")


def adjust_confidence(
    model_confidence: float,
    *,
    evidence_verified: bool,
    page_legibility: float = 1.0,
    in_conflict: bool = False,
    settings: Settings | None = None,
) -> tuple[float, str]:
    """The deterministic confidence chain of PROJECT_PLAN §11.5.

    Applied in code, after the call, never asked of the model. Returns the adjusted confidence
    and a human-readable reason, and both are stored — so the write-up can show precisely how
    much of a final score is the model's self-report and how much is system verification.
    """
    settings = settings or get_settings()
    confidence = max(0.0, min(1.0, model_confidence))
    reasons: list[str] = []

    if not evidence_verified and confidence > settings.unverified_confidence_cap:
        confidence = settings.unverified_confidence_cap
        reasons.append(
            f"evidence could not be verified, capped at {settings.unverified_confidence_cap:.2f}")

    # E34: uncertainty about the handwriting flows down to the field.
    if page_legibility < confidence:
        confidence = page_legibility
        reasons.append(f"source page legibility {page_legibility:.2f}")

    # E33: sources disagree, so neither value can be trusted at face value.
    if in_conflict and confidence > settings.conflict_confidence_cap:
        confidence = settings.conflict_confidence_cap
        reasons.append(
            f"sources disagree, capped at {settings.conflict_confidence_cap:.2f}")

    return round(confidence, 4), "; ".join(reasons)
