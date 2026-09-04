"""Merging fields extracted from different sources, and surfacing where they disagree (E33).

A message routinely carries the same fact twice: the email body says the patient is 58, the
attached form says 71. One of them is wrong and neither source is authoritative.

The tempting behaviour is to pick one — prefer the structured form, or prefer the higher
confidence, or prefer the most recent. All three are defensible and all three are **wrong here**,
because they silently discard a real disagreement about a patient's age in a safety report. In a
regulated context, quietly choosing is worse than not choosing: the reviewer never learns there
was a question.

So both values are kept, both keep their own evidence, both are marked `status = CONFLICT`, both
have their confidence capped at 0.50, and the UI stacks them with a control to choose. The
system's job here is to notice and escalate, not to decide.

Values are compared after normalisation, so "58" and "58 years" do not count as a conflict, and
neither does "Female" against "female". A conflict has to be a real difference in what the
sources say.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.pipeline.extract import ExtractedField
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pipeline.merge")

# Fields where two sources disagreeing is worth a reviewer's attention. Narrative prose will
# always differ between sources without that meaning anything, so it is excluded.
CONFLICT_RELEVANT_GROUPS = frozenset({"PATIENT", "REPORTER", "PRODUCT", "REACTION", "SEVERITY"})

_TRAILING_UNITS = re.compile(
    r"\s*(years?|yrs?|y\.?o\.?|months?|weeks?|days?|kg|kgs|lb|lbs|mg|ml)\b\.?", re.IGNORECASE)


def normalise_value(value: str) -> str:
    """Fold away differences that are not disagreements.

    "58", "58 years" and "58 y.o." are the same answer written three ways. Treating them as a
    conflict would flood the reviewer with noise and train them to dismiss the flag — which
    would then hide the real conflicts.
    """
    if value is None:
        return ""
    folded = value.strip().casefold()
    folded = _TRAILING_UNITS.sub("", folded)
    folded = re.sub(r"[^\w\s]", " ", folded)
    return " ".join(folded.split())


def values_agree(left: str, right: str) -> bool:
    a, b = normalise_value(left), normalise_value(right)
    if not a or not b:
        return True          # one side saying nothing is not a disagreement
    if a == b:
        return True

    # One value containing the other counts as agreement — "Dr Aoife Whitfield" against
    # "Aoife Whitfield" is the same reporter, not two of them.
    #
    # Containment is checked on **whole words**, not substrings. Plain `a in b` reports that
    # "male" and "female" agree, which would silently swallow a disagreement about a patient's
    # sex — precisely the kind of conflict this function exists to catch.
    tokens_a, tokens_b = a.split(), b.split()
    shorter, longer = (
        (tokens_a, tokens_b) if len(tokens_a) <= len(tokens_b) else (tokens_b, tokens_a))
    span = len(shorter)
    return any(longer[i:i + span] == shorter for i in range(len(longer) - span + 1))


@dataclass
class SourcedField:
    """One field, tagged with the source unit it came from."""

    unit: str
    field: ExtractedField


@dataclass
class MergeResult:
    fields: list[ExtractedField] = field(default_factory=list)
    conflicts: list[dict[str, object]] = field(default_factory=list)

    @property
    def conflict_count(self) -> int:
        return len(self.conflicts)


def merge_fields(
    sourced: Sequence[SourcedField],
    settings: Settings | None = None,
) -> MergeResult:
    """Merge fields from several source units, flagging genuine disagreements."""
    settings = settings or get_settings()

    grouped: dict[str, list[SourcedField]] = defaultdict(list)
    for item in sourced:
        grouped[item.field.field_path].append(item)

    result = MergeResult()

    for field_path, candidates in grouped.items():
        asserting = [c for c in candidates if c.field.asserts_something and c.field.value_text]

        if not asserting:
            # Everything abstained. Keep one NOT_STATED row so the field is visibly present and
            # visibly unanswered, rather than absent and ambiguous.
            result.fields.append(candidates[0].field)
            continue

        if len(asserting) == 1:
            result.fields.append(asserting[0].field)
            continue

        group = asserting[0].field.field_group
        distinct: list[SourcedField] = []
        for candidate in asserting:
            if not any(values_agree(candidate.field.value_text, seen.field.value_text)
                       for seen in distinct):
                distinct.append(candidate)

        if len(distinct) == 1 or group not in CONFLICT_RELEVANT_GROUPS:
            # Agreement, or a field where difference is not meaningful. Keep the best-evidenced
            # version — verified evidence beats unverified, then higher confidence.
            best = max(asserting, key=lambda c: (
                any(e.verified == "Y" for e in c.field.evidence), c.field.confidence))
            result.fields.append(best.field)
            continue

        # --- a real disagreement (E33) ---
        log.info("Conflict on %s: %s", field_path,
                 [(c.unit, c.field.value_text) for c in distinct])

        for candidate in distinct:
            conflicted = candidate.field
            capped, reason = _cap_for_conflict(conflicted, settings)
            conflicted.status = "CONFLICT"
            conflicted.confidence = capped
            conflicted.adjust_reason = "; ".join(filter(None, [conflicted.adjust_reason, reason]))
            result.fields.append(conflicted)

        result.conflicts.append({
            "field_path": field_path,
            "field_group": group,
            "values": [
                {"unit": c.unit,
                 "value": c.field.value_text,
                 "confidence": c.field.confidence,
                 "verified": any(e.verified == "Y" for e in c.field.evidence),
                 "quote": c.field.evidence[0].quote if c.field.evidence else ""}
                for c in distinct
            ],
        })

    return result


def _cap_for_conflict(item: ExtractedField, settings: Settings) -> tuple[float, str]:
    cap = settings.conflict_confidence_cap
    if item.confidence <= cap:
        return item.confidence, "sources disagree"
    return cap, f"sources disagree, capped at {cap:.2f}"


def summarise_verification(fields: Iterable[ExtractedField]) -> dict[str, object]:
    """Verification statistics for the batch report and `eval/report.md`.

    The headline number of the whole submission: what fraction of asserted facts carry a quote
    that code could actually find in the source.
    """
    asserted = [f for f in fields if f.asserts_something]
    with_evidence = [f for f in asserted if f.evidence]
    verified = [f for f in with_evidence if any(e.verified == "Y" for e in f.evidence)]
    exact = sum(1 for f in with_evidence
                if any(e.verify_method == "EXACT" for e in f.evidence))
    fuzzy = sum(1 for f in with_evidence
                if any(e.verify_method == "FUZZY" for e in f.evidence))

    return {
        "asserted_fields": len(asserted),
        "fields_with_evidence": len(with_evidence),
        "verified_fields": len(verified),
        "exact_matches": exact,
        "fuzzy_matches": fuzzy,
        "unverified": len(with_evidence) - len(verified),
        "verification_rate": round(len(verified) / len(asserted), 4) if asserted else 0.0,
    }
