"""Cross-source conflict detection (E33).

The corpus case this models: `adv-07-body-form-conflict`, where the email body says the patient
is 58 and the attached form says 71. The correct behaviour is to keep both, cap both, and ask
the reviewer — not to quietly prefer one.

Half these tests are about *not* raising a conflict. A flag that fires on "58" against
"58 years" is worse than no flag, because reviewers learn to ignore it and then miss the real one.
"""

from __future__ import annotations

import pytest

from app.pipeline.extract import Evidence, ExtractedField
from app.pipeline.merge import (
    SourcedField,
    merge_fields,
    normalise_value,
    summarise_verification,
    values_agree,
)


def make_field(path, value, *, group="PATIENT", status="STATED", confidence=0.9,
               verified=True, quote="q") -> ExtractedField:
    return ExtractedField(
        field_group=group, field_path=path, field_index=0,
        value_text=value, value_json=None, unit=None, raw_text=None,
        status=status, confidence=confidence, confidence_pre_adjust=confidence,
        adjust_reason="",
        evidence=[Evidence(
            source_type="PDF_PAGE", document_id=1, page_no=1, quote=quote,
            char_start=0, char_end=len(quote), bbox=None,
            verified="Y" if verified else "N",
            verify_method="EXACT" if verified else "FAILED",
            match_score=100.0 if verified else 12.0)])


class TestValueNormalisation:
    @pytest.mark.parametrize("a,b", [
        ("58", "58 years"),
        ("58", "58 y.o."),
        ("Female", "female"),
        ("71", "71 yrs"),
        ("Dr Aoife Whitfield", "Aoife Whitfield"),
        ("68 kg", "68"),
    ])
    def test_the_same_answer_written_differently_is_not_a_conflict(self, a, b):
        assert values_agree(a, b)

    @pytest.mark.parametrize("a,b", [
        ("58", "71"),
        ("Female", "Male"),
        ("Velmoradine", "Cardexatine"),
        ("VLM-4471B", "FNQ-2210A"),
    ])
    def test_genuinely_different_values_do_conflict(self, a, b):
        assert not values_agree(a, b)

    def test_an_absent_value_is_not_a_disagreement(self):
        assert values_agree("58", "")
        assert values_agree("", "71")


class TestConflictDetection:
    def test_the_corpus_age_conflict_is_surfaced_not_resolved(self):
        merged = merge_fields([
            SourcedField("email body", make_field("patient.age", "58")),
            SourcedField("AER-2026-00244.pdf", make_field("patient.age", "71")),
        ])

        assert merged.conflict_count == 1
        conflict = merged.conflicts[0]
        assert conflict["field_path"] == "patient.age"
        assert {v["value"] for v in conflict["values"]} == {"58", "71"}

        # Both values survive. Neither source is authoritative, so neither is discarded.
        ages = [f for f in merged.fields if f.field_path == "patient.age"]
        assert len(ages) == 2
        assert {f.value_text for f in ages} == {"58", "71"}

    def test_both_sides_are_marked_conflict_and_capped(self):
        merged = merge_fields([
            SourcedField("body", make_field("patient.age", "58", confidence=0.95)),
            SourcedField("form", make_field("patient.age", "71", confidence=0.92)),
        ])
        for item in merged.fields:
            assert item.status == "CONFLICT"
            assert item.confidence <= 0.50
            assert "disagree" in item.adjust_reason

    def test_agreement_collapses_to_one_row(self):
        merged = merge_fields([
            SourcedField("body", make_field("patient.sex", "Female")),
            SourcedField("form", make_field("patient.sex", "female")),
        ])
        assert merged.conflict_count == 0
        assert len(merged.fields) == 1
        assert merged.fields[0].status == "STATED"

    def test_verified_evidence_wins_when_the_sources_agree(self):
        merged = merge_fields([
            SourcedField("body", make_field(
                "patient.sex", "Female", confidence=0.99, verified=False)),
            SourcedField("form", make_field(
                "patient.sex", "Female", confidence=0.80, verified=True)),
        ])
        # A proven quote at 0.80 beats an unproven one at 0.99 — that is the whole premise.
        assert merged.fields[0].confidence == pytest.approx(0.80)

    def test_narrative_differences_are_not_treated_as_conflicts(self):
        # Two sources will always word a narrative differently. Flagging that would bury the
        # real conflicts in noise.
        merged = merge_fields([
            SourcedField("body", make_field(
                "narrative", "She developed a rash.", group="NARRATIVE")),
            SourcedField("form", make_field(
                "narrative", "Patient reported a widespread rash.", group="NARRATIVE")),
        ])
        assert merged.conflict_count == 0

    def test_a_field_only_one_source_mentions_is_kept_as_is(self):
        merged = merge_fields([
            SourcedField("body", make_field("patient.age", "58")),
            SourcedField("form", make_field(
                "patient.age", "", status="NOT_STATED", confidence=0.0)),
        ])
        assert merged.conflict_count == 0
        assert len(merged.fields) == 1
        assert merged.fields[0].value_text == "58"

    def test_a_field_nobody_stated_survives_as_not_stated(self):
        merged = merge_fields([
            SourcedField("body", make_field(
                "patient.weight", "", status="NOT_STATED", confidence=0.0)),
            SourcedField("form", make_field(
                "patient.weight", "", status="NOT_STATED", confidence=0.0)),
        ])
        assert len(merged.fields) == 1
        assert merged.fields[0].status == "NOT_STATED"

    def test_three_way_disagreement_keeps_all_three(self):
        merged = merge_fields([
            SourcedField("body", make_field("patient.age", "58")),
            SourcedField("form", make_field("patient.age", "71")),
            SourcedField("annex", make_field("patient.age", "85")),
        ])
        assert merged.conflict_count == 1
        assert len(merged.conflicts[0]["values"]) == 3


class TestVerificationSummary:
    def test_the_headline_rate_counts_asserted_fields(self):
        stats = summarise_verification([
            make_field("a", "x", verified=True),
            make_field("b", "y", verified=True),
            make_field("c", "z", verified=False),
            make_field("d", "", status="NOT_STATED", confidence=0.0),
        ])
        # Abstentions are not failures to verify — there is nothing to verify.
        assert stats["asserted_fields"] == 3
        assert stats["verified_fields"] == 2
        assert stats["unverified"] == 1
        assert stats["verification_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_an_empty_set_does_not_divide_by_zero(self):
        assert summarise_verification([])["verification_rate"] == 0.0
