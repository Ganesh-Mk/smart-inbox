"""Classification, and the rules applied *after* the model has spoken (E21, E22).

Two decisions are deliberately taken away from the model, because on the very first smoke call
it got one of them wrong in exactly the way the plan predicted (DECISIONS D-003): asked to
classify a textbook ICSR, it returned `NOT_RELEVANT` at 0.05 *alongside* `ICSR` at 0.95. It was
treating four independent booleans as a probability distribution.

So:

* **E21 — `NOT_RELEVANT` exclusivity is enforced in code.** It is assigned if and only if the
  other three sets are empty. Not requested in the prompt, not hoped for: a rule with a test.

* **E22 — the ICSR label is decided by rule from the element checklist.** The model reports the
  four minimum criteria independently, each with its own evidence and confidence. Code then
  counts them: four present is a valid `ICSR` with confidence equal to the *minimum* of the
  four; two or three is `ICSR_INCOMPLETE` with the missing elements named for the reviewer.

That second one is the bigger win. It converts a fuzzy regulatory judgement into an auditable
decision a reviewer can check line by line — and when the system says "ICSR_INCOMPLETE: no
identifiable reporter", that is a statement anyone can verify against the source.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.llm.client import LlmCall, LlmClient, text_part
from app.llm.prompts import load, system_prompt
from app.lang.detect import normalise_language
from app.llm.schemas import Category, ClassificationResult, IcsrElements

log = logging.getLogger("smartinbox.ai.pipeline.classify")

PROMPT_ID = "P1_classify"

LABEL_ICSR = "ICSR"
LABEL_ICSR_INCOMPLETE = "ICSR_INCOMPLETE"
LABEL_PQC = "PQC"
LABEL_MI = "MI"
LABEL_NOT_RELEVANT = "NOT_RELEVANT"

ELEMENT_NAMES = {
    "has_identifiable_patient": "an identifiable patient",
    "has_identifiable_reporter": "an identifiable reporter",
    "has_suspect_product": "a suspect product",
    "has_adverse_event": "an adverse event",
}


@dataclass
class Label:
    """One final label with the confidence and reasoning behind it."""

    category: str
    confidence: float
    reason: str
    decided_by: str  # AI | RULE
    evidence_quote: str = ""
    evidence_page: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "decided_by": self.decided_by,
            "evidence_quote": self.evidence_quote,
            "evidence_page": self.evidence_page,
        }


@dataclass
class ClassificationOutcome:
    labels: list[Label] = field(default_factory=list)
    elements: dict[str, Any] = field(default_factory=dict)
    elements_present: int = 0
    missing_elements: list[str] = field(default_factory=list)
    primary_language: str | None = None
    summary_line: str = ""
    raw_verdicts: list[dict[str, Any]] = field(default_factory=list)

    def categories(self) -> list[str]:
        return [label.category for label in self.labels]

    def to_dict(self) -> dict[str, Any]:
        return {
            "labels": [label.to_dict() for label in self.labels],
            "categories": self.categories(),
            "icsr_elements": self.elements,
            "elements_present": self.elements_present,
            "missing_elements": self.missing_elements,
            "primary_language": self.primary_language,
            "summary_line": self.summary_line,
            "raw_verdicts": self.raw_verdicts,
        }


def _elements_dict(elements: IcsrElements) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ELEMENT_NAMES:
        check = getattr(elements, name)
        out[name] = {
            "present": check.present,
            "confidence": round(check.confidence, 4),
            "quote": check.quote,
            "page_no": check.page_no,
        }
    return out


def apply_rules(result: ClassificationResult) -> ClassificationOutcome:
    """Turn the model's independent verdicts into the final label set.

    Pure and synchronous: given the same model output it always produces the same labels, which
    is what makes the decision auditable and testable without a network call.
    """
    outcome = ClassificationOutcome(
        primary_language=normalise_language(result.primary_language),
        summary_line=result.summary_line,
        raw_verdicts=[
            {"category": v.category.value, "applies": v.applies,
             "confidence": round(v.confidence, 4), "reason": v.reason}
            for v in result.verdicts
        ],
    )

    by_category = {v.category: v for v in result.verdicts}

    # ---- E22: ICSR decided by rule over the element checklist ----
    elements = result.icsr_elements
    outcome.elements = _elements_dict(elements)
    checks = {name: getattr(elements, name) for name in ELEMENT_NAMES}
    present = [name for name, check in checks.items() if check.present]
    missing = [name for name, check in checks.items() if not check.present]
    outcome.elements_present = len(present)
    outcome.missing_elements = missing

    if len(present) == 4:
        # The case is only as strong as its weakest element — a report resting on a barely
        # identifiable patient is a weak report, whatever the other three score.
        weakest = min(checks.values(), key=lambda c: c.confidence)
        outcome.labels.append(Label(
            category=LABEL_ICSR,
            confidence=weakest.confidence,
            reason=("All four ICSR minimum criteria are present; confidence is the weakest of "
                    "the four."),
            decided_by="RULE",
            evidence_quote=weakest.quote,
            evidence_page=weakest.page_no,
        ))
    elif len(present) >= 2 and checks["has_adverse_event"].present:
        # An adverse event is *necessary*, not merely one of four interchangeable boxes.
        # Without one there is no safety case to be incomplete about.
        #
        # This matters in practice rather than in theory: a product quality complaint always
        # names a reporter and a product, so it always scores 2 of 4 and, under a plain
        # "two or more" rule, every single PQC in the corpus was also labelled
        # ICSR_INCOMPLETE. That is noise on the one screen where noise is most expensive —
        # a reviewer who learns that the incomplete-ICSR flag is usually meaningless will
        # miss the case where it is not.
        strongest_missing = ", ".join(ELEMENT_NAMES[name] for name in missing)
        outcome.labels.append(Label(
            category=LABEL_ICSR_INCOMPLETE,
            confidence=round(sum(checks[n].confidence for n in present) / len(present), 4),
            reason=f"{len(present)} of 4 ICSR minimum criteria present; missing {strongest_missing}.",
            decided_by="RULE",
            evidence_quote=checks[present[0]].quote,
            evidence_page=checks[present[0]].page_no,
        ))

    # ---- PQC and MI: the model's own verdicts, taken at face value ----
    pqc = by_category.get(Category.PQC)
    if pqc is not None and pqc.applies and result.has_physical_defect:
        outcome.labels.append(Label(
            category=LABEL_PQC, confidence=pqc.confidence,
            reason=pqc.reason, decided_by="AI"))
    elif pqc is not None and pqc.applies and not result.has_physical_defect:
        # Dissatisfaction with efficacy is not a defect. The checklist boolean is the tiebreak.
        log.info("Dropping PQC: the model applied the label but reported no physical defect")

    mi = by_category.get(Category.MI)
    if mi is not None and mi.applies and result.has_genuine_question:
        outcome.labels.append(Label(
            category=LABEL_MI, confidence=mi.confidence,
            reason=mi.reason, decided_by="AI"))
    elif mi is not None and mi.applies and not result.has_genuine_question:
        log.info("Dropping MI: the model applied the label but reported no genuine question")

    # ---- E21: NOT_RELEVANT if and only if nothing else applies ----
    if not outcome.labels:
        not_relevant = by_category.get(Category.NOT_RELEVANT)
        outcome.labels.append(Label(
            category=LABEL_NOT_RELEVANT,
            confidence=not_relevant.confidence if not_relevant else 0.5,
            reason=(not_relevant.reason if not_relevant
                    else "No safety, quality or information-request content was identified."),
            decided_by="RULE" if not_relevant is None else "AI",
        ))
    else:
        dropped = by_category.get(Category.NOT_RELEVANT)
        if dropped is not None and dropped.applies:
            # The exact failure D-003 observed live, now neutralised by construction.
            log.info(
                "Dropped NOT_RELEVANT (model applied it at %.2f) because %s also apply — "
                "NOT_RELEVANT is exclusive by rule (E21)",
                dropped.confidence, [label.category for label in outcome.labels])

    return outcome


def classify(
    client: LlmClient,
    source_text: str,
    *,
    source_description: str = "",
) -> tuple[ClassificationOutcome, LlmCall]:
    """Classify one source unit — an email body, a document, or a message roll-up."""
    prompt = load(PROMPT_ID)
    call = client.complete_json(
        purpose=PROMPT_ID,
        system_prompt=system_prompt().text,
        user_content=[text_part(prompt.render(
            source_text=source_text, source_description=source_description))],
        schema_model=ClassificationResult,
        prompt_version=prompt.label,
        max_tokens=4000,
    )
    result = ClassificationResult.model_validate(call.parsed)
    outcome = apply_rules(result)

    log.info("Classified as %s (%d/4 ICSR elements)",
             outcome.categories(), outcome.elements_present)
    return outcome, call


def roll_up_message(unit_outcomes: list[tuple[str, ClassificationOutcome]]) -> list[Label]:
    """Message-level labels are the union of its units' labels (E25).

    A bland covering email with a completed ICSR form attached must not come out NOT_RELEVANT.
    Each unit is classified on its own and the message takes the union — so the label survives
    even when the unit that carries it is an attachment, and every label names the unit that
    triggered it.
    """
    best: dict[str, Label] = {}
    for unit_name, outcome in unit_outcomes:
        for label in outcome.labels:
            existing = best.get(label.category)
            if existing is None or label.confidence > existing.confidence:
                best[label.category] = Label(
                    category=label.category,
                    confidence=label.confidence,
                    reason=f"{label.reason} (from {unit_name})",
                    decided_by=label.decided_by,
                    evidence_quote=label.evidence_quote,
                    evidence_page=label.evidence_page,
                )

    labels = list(best.values())

    # The exclusivity rule applies again at message level: a unit judged NOT_RELEVANT on its own
    # must not drag the label onto a message that has real content elsewhere.
    real = [label for label in labels if label.category != LABEL_NOT_RELEVANT]
    if real:
        return sorted(real, key=lambda label: -label.confidence)

    return labels or [Label(LABEL_NOT_RELEVANT, 0.5, "No relevant content in any unit.", "RULE")]
