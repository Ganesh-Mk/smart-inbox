"""The classification rules applied in code, not asked of the model (E21, E22, E23, E24).

`apply_rules` is a pure function over the model's output, which is the point: the two decisions
that matter most are testable without a network call and give the same answer every time.

The first test here is a direct regression of DECISIONS D-003 — a failure observed on the very
first live call, not an imagined one.
"""

from __future__ import annotations

import pytest

from app.llm.schemas import (
    Category,
    CategoryVerdict,
    ClassificationResult,
    IcsrElementCheck,
    IcsrElements,
)
from app.pipeline.classify import (
    LABEL_ICSR,
    LABEL_ICSR_INCOMPLETE,
    LABEL_MI,
    LABEL_NOT_RELEVANT,
    LABEL_PQC,
    ClassificationOutcome,
    apply_rules,
    roll_up_message,
)


def check(present: bool, confidence: float = 0.9, quote: str = "q") -> IcsrElementCheck:
    return IcsrElementCheck(
        present=present, confidence=confidence if present else 0.0,
        quote=quote if present else "", page_no=1)


def elements(patient=True, reporter=True, product=True, event=True,
             confidences=(0.9, 0.9, 0.9, 0.9)) -> IcsrElements:
    return IcsrElements(
        has_identifiable_patient=check(patient, confidences[0]),
        has_identifiable_reporter=check(reporter, confidences[1]),
        has_suspect_product=check(product, confidences[2]),
        has_adverse_event=check(event, confidences[3]),
    )


def verdicts(icsr=(False, 0.1), pqc=(False, 0.1), mi=(False, 0.1),
             not_relevant=(False, 0.1)) -> list[CategoryVerdict]:
    return [
        CategoryVerdict(category=Category.ICSR, applies=icsr[0],
                        confidence=icsr[1], reason="icsr reason"),
        CategoryVerdict(category=Category.PQC, applies=pqc[0],
                        confidence=pqc[1], reason="pqc reason"),
        CategoryVerdict(category=Category.MI, applies=mi[0],
                        confidence=mi[1], reason="mi reason"),
        CategoryVerdict(category=Category.NOT_RELEVANT, applies=not_relevant[0],
                        confidence=not_relevant[1], reason="not relevant reason"),
    ]


def result(**kwargs) -> ClassificationResult:
    defaults = dict(
        verdicts=verdicts(),
        icsr_elements=elements(),
        has_genuine_question=False,
        has_physical_defect=False,
        primary_language="en",
        summary_line="a message",
    )
    defaults.update(kwargs)
    return ClassificationResult(**defaults)


class TestNotRelevantExclusivity:
    """E21, and a regression of the failure seen on the first ever live call (D-003)."""

    def test_not_relevant_is_dropped_when_a_real_label_applies(self):
        # The model returned NOT_RELEVANT at 0.05 alongside ICSR at 0.95 on a textbook ICSR.
        outcome = apply_rules(result(
            verdicts=verdicts(icsr=(True, 0.95), not_relevant=(True, 0.05)),
            icsr_elements=elements()))

        assert LABEL_ICSR in outcome.categories()
        assert LABEL_NOT_RELEVANT not in outcome.categories()

    def test_not_relevant_survives_when_nothing_else_applies(self):
        outcome = apply_rules(result(
            verdicts=verdicts(not_relevant=(True, 0.97)),
            icsr_elements=elements(False, False, False, False)))

        assert outcome.categories() == [LABEL_NOT_RELEVANT]
        assert outcome.labels[0].confidence == pytest.approx(0.97)

    def test_a_message_with_no_verdicts_at_all_is_not_relevant(self):
        outcome = apply_rules(result(icsr_elements=elements(False, False, False, False)))
        assert outcome.categories() == [LABEL_NOT_RELEVANT]


class TestIcsrValidityRule:
    """E22: the label is decided by rule over the checklist, never by the model."""

    def test_all_four_elements_give_a_valid_icsr(self):
        outcome = apply_rules(result(icsr_elements=elements()))
        assert outcome.categories() == [LABEL_ICSR]
        assert outcome.elements_present == 4
        assert outcome.missing_elements == []

    def test_icsr_confidence_is_the_weakest_element_not_the_average(self):
        # A case resting on a barely identifiable patient is a weak case, however clear the
        # other three are. Averaging would hide exactly the thing a reviewer needs to see.
        outcome = apply_rules(result(
            icsr_elements=elements(confidences=(0.35, 0.95, 0.95, 0.95))))
        assert outcome.labels[0].confidence == pytest.approx(0.35)

    @pytest.mark.parametrize("missing_kwarg,expected_phrase", [
        ({"reporter": False}, "an identifiable reporter"),
        ({"product": False}, "a suspect product"),
        ({"patient": False}, "an identifiable patient"),
    ])
    def test_three_of_four_gives_icsr_incomplete_naming_what_is_missing(
            self, missing_kwarg, expected_phrase):
        outcome = apply_rules(result(icsr_elements=elements(**missing_kwarg)))

        assert outcome.categories() == [LABEL_ICSR_INCOMPLETE]
        assert outcome.elements_present == 3
        # Naming the gap is what makes the verdict actionable rather than just a lower score.
        assert expected_phrase in outcome.labels[0].reason

    def test_two_of_four_with_an_event_is_icsr_incomplete(self):
        outcome = apply_rules(result(icsr_elements=elements(product=False, reporter=False)))
        assert outcome.categories() == [LABEL_ICSR_INCOMPLETE]

    def test_no_adverse_event_means_no_icsr_label_at_all(self):
        # An adverse event is necessary, not one of four interchangeable boxes. Without one
        # there is no safety case to be incomplete about.
        outcome = apply_rules(result(
            icsr_elements=elements(event=False, patient=False),
            verdicts=verdicts(not_relevant=(True, 0.6))))
        assert LABEL_ICSR_INCOMPLETE not in outcome.categories()
        assert LABEL_ICSR not in outcome.categories()

    def test_a_quality_complaint_is_not_also_an_incomplete_icsr(self):
        # The regression this rule exists for. A PQC always names a reporter and a product, so
        # it always scores 2 of 4; under a plain "two or more" rule every complaint in the
        # corpus was also tagged ICSR_INCOMPLETE, which is noise on the screen where noise
        # costs the most.
        outcome = apply_rules(result(
            verdicts=verdicts(pqc=(True, 0.93)),
            icsr_elements=elements(patient=False, event=False),
            has_physical_defect=True))
        assert outcome.categories() == [LABEL_PQC]

    def test_one_element_is_not_an_icsr_at_all(self):
        outcome = apply_rules(result(
            icsr_elements=elements(False, True, False, False),
            verdicts=verdicts(not_relevant=(True, 0.6))))
        assert LABEL_ICSR not in outcome.categories()
        assert LABEL_ICSR_INCOMPLETE not in outcome.categories()

    def test_the_model_claiming_icsr_cannot_override_the_rule(self):
        # The model says ICSR at 0.99; the elements say otherwise. The rule wins.
        outcome = apply_rules(result(
            verdicts=verdicts(icsr=(True, 0.99)),
            icsr_elements=elements(patient=False, reporter=False, product=False)))
        assert LABEL_ICSR not in outcome.categories()


class TestCombinedCategories:
    def test_icsr_and_pqc_can_both_apply(self):
        # E23: a defective product that also caused a reaction. The brief calls this out.
        outcome = apply_rules(result(
            verdicts=verdicts(icsr=(True, 0.9), pqc=(True, 0.88)),
            icsr_elements=elements(),
            has_physical_defect=True))
        assert set(outcome.categories()) == {LABEL_ICSR, LABEL_PQC}

    def test_icsr_and_mi_can_both_apply(self):
        # E24: "my rash got worse, how should I taper?" is both.
        outcome = apply_rules(result(
            verdicts=verdicts(icsr=(True, 0.9), mi=(True, 0.85)),
            icsr_elements=elements(),
            has_genuine_question=True))
        assert set(outcome.categories()) == {LABEL_ICSR, LABEL_MI}

    def test_pqc_needs_an_actual_physical_defect(self):
        # Dissatisfaction with efficacy is not a quality complaint. The checklist boolean is
        # the tiebreak against a model that reached for the label too readily.
        outcome = apply_rules(result(
            verdicts=verdicts(pqc=(True, 0.8), not_relevant=(False, 0.2)),
            icsr_elements=elements(False, False, False, False),
            has_physical_defect=False))
        assert LABEL_PQC not in outcome.categories()

    def test_mi_needs_an_actual_question(self):
        outcome = apply_rules(result(
            verdicts=verdicts(mi=(True, 0.8)),
            icsr_elements=elements(False, False, False, False),
            has_genuine_question=False))
        assert LABEL_MI not in outcome.categories()


class TestMessageRollUp:
    """E25: a bland covering email with an ICSR form attached is not NOT_RELEVANT."""

    def _outcome(self, res) -> ClassificationOutcome:
        return apply_rules(res)

    def test_a_document_label_survives_a_not_relevant_body(self):
        body = self._outcome(result(
            verdicts=verdicts(not_relevant=(True, 0.9)),
            icsr_elements=elements(False, False, False, False)))
        attachment = self._outcome(result(
            verdicts=verdicts(icsr=(True, 0.93)), icsr_elements=elements()))

        labels = roll_up_message([("email body", body), ("AER-2026-00188.pdf", attachment)])

        assert [label.category for label in labels] == [LABEL_ICSR]
        # The label names the unit that produced it, so a reviewer knows where to look.
        assert "AER-2026-00188.pdf" in labels[0].reason

    def test_the_union_keeps_the_strongest_confidence_per_category(self):
        weak = self._outcome(result(
            verdicts=verdicts(icsr=(True, 0.6)),
            icsr_elements=elements(confidences=(0.6, 0.6, 0.6, 0.6))))
        strong = self._outcome(result(
            verdicts=verdicts(icsr=(True, 0.95)),
            icsr_elements=elements(confidences=(0.95, 0.95, 0.95, 0.95))))

        labels = roll_up_message([("body", weak), ("form.pdf", strong)])
        assert labels[0].confidence == pytest.approx(0.95)

    def test_a_message_whose_units_are_all_irrelevant_stays_not_relevant(self):
        irrelevant = self._outcome(result(
            verdicts=verdicts(not_relevant=(True, 0.95)),
            icsr_elements=elements(False, False, False, False)))
        labels = roll_up_message([("body", irrelevant)])
        assert [label.category for label in labels] == [LABEL_NOT_RELEVANT]
