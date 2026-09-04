"""Evidence verification (E27) — the differentiator, so it gets the most tests.

The claim being defended is narrow and strong: *we never trust the model's citation, we prove
it.* These tests exist to make sure that claim stays true, in both directions — a real quote
must verify, and a fabricated one must fail. The second direction matters more. A verifier that
accepts everything is worse than no verifier, because it launders hallucinations into apparent
provenance.
"""

from __future__ import annotations

import pytest

from app.pipeline.verify import (
    METHOD_EXACT,
    METHOD_FAILED,
    METHOD_FUZZY,
    adjust_confidence,
    normalise,
    verify_against_pages,
    verify_quote,
)

PAGE = (
    "Adverse Event Report Form\n"
    "Patient initials: M.D.   Age at onset: 71-year-old   Sex: Female\n"
    "The patient began Velmoradine 20 mg once daily for essential hypertension on "
    "3 March 2026. Nine days later she developed a widespread itchy maculopapular rash "
    "across the trunk and both forearms. The drug was withdrawn on 13 March and the rash "
    "began to settle within 48 hours.\n"
    "Reported by Dr Aoife Whitfield, General Practitioner, United Kingdom."
)


class TestNormalisation:
    def test_offsets_map_back_to_the_original_text(self):
        source = "The  patient   began\nVelmoradine"
        normalised = normalise(source)
        position = normalised.text.find("velmoradine")
        start, end = normalised.original_span(position, position + len("velmoradine"))
        assert source[start:end] == "Velmoradine"

    @pytest.mark.parametrize("variant", [
        "The drug was withdrawn",          # plain
        "The  drug   was withdrawn",       # collapsed whitespace
        "THE DRUG WAS WITHDRAWN",          # case
        "The drug was withdrawn",          # non-breaking space
    ])
    def test_cosmetic_differences_do_not_defeat_matching(self, variant):
        assert verify_quote(variant, PAGE).verified

    def test_curly_quotes_and_en_dashes_are_unified(self):
        source = "She said “no further treatment” and the range was 10–40."
        assert verify_quote('"no further treatment"', source).verified
        assert verify_quote("10-40", source).verified


class TestExactMatching:
    def test_a_verbatim_quote_verifies_exactly(self):
        result = verify_quote("a widespread itchy maculopapular rash", PAGE)
        assert result.verified
        assert result.method == METHOD_EXACT
        assert result.match_score == 100.0

    def test_offsets_point_at_the_real_text_not_at_what_the_model_claimed(self):
        result = verify_quote("Velmoradine 20 mg once daily", PAGE)
        # The stored offsets must be the ones we found. This is what the UI highlights.
        assert PAGE[result.char_start:result.char_end] == "Velmoradine 20 mg once daily"

    def test_a_short_quote_still_resolves(self):
        result = verify_quote("71-year-old", PAGE)
        assert result.verified
        assert PAGE[result.char_start:result.char_end] == "71-year-old"


class TestFuzzyMatching:
    def test_a_lightly_reworded_quote_is_accepted_with_its_score(self):
        # Models routinely drop a word or re-punctuate while quoting. That is a citation worth
        # keeping — but it is recorded as FUZZY, not passed off as verbatim.
        result = verify_quote("the patient began Velmoradine 20mg once daily", PAGE)
        assert result.verified
        assert result.method == METHOD_FUZZY
        assert 90 <= result.match_score < 100
        assert "not verbatim" in result.note

    def test_fuzzy_offsets_still_land_on_the_right_region(self):
        result = verify_quote("developed a widespread itchy maculopapular rash across", PAGE)
        assert result.verified
        assert "maculopapular" in PAGE[result.char_start:result.char_end]


class TestFabricatedCitations:
    """The direction that actually matters."""

    def test_an_invented_quote_fails(self):
        result = verify_quote(
            "The patient was admitted to intensive care for three weeks", PAGE)
        assert not result.verified
        assert result.method == METHOD_FAILED
        assert result.char_start is None

    def test_a_plausible_but_absent_fact_fails(self):
        # The page says the drug was withdrawn; it never says the patient was rechallenged.
        # This is exactly the sort of fluent, plausible invention the verifier exists to catch.
        assert not verify_quote("the patient was rechallenged without recurrence", PAGE).verified

    def test_the_failure_reports_how_close_it_got(self):
        result = verify_quote("a widespread purpuric rash on the legs", PAGE)
        assert not result.verified
        # A near-miss is distinguishable from nonsense, which helps when tuning the threshold.
        assert 0 < result.match_score < 90
        assert "not found" in result.note

    def test_an_empty_quote_is_a_failure_not_a_pass(self):
        result = verify_quote("", PAGE)
        assert not result.verified
        assert "no quote" in result.note

    def test_a_quote_against_an_empty_page_fails(self):
        result = verify_quote("anything at all", "")
        assert not result.verified
        assert "no text" in result.note


class TestPageAttribution:
    PAGES = [(1, "Patient initials: M.D. Age at onset: 71-year-old."),
             (2, "The drug was withdrawn on 13 March and the rash began to settle.")]

    def test_the_cited_page_is_tried_first(self):
        result = verify_against_pages("71-year-old", self.PAGES, cited_page=1)
        assert result.verified and result.page_no == 1

    def test_a_real_quote_cited_to_the_wrong_page_is_corrected_not_rejected(self):
        # A wrong page number is a citation error, not a fabrication. Rejecting a true quote
        # over it would punish the model for the wrong mistake — and lose real evidence.
        result = verify_against_pages(
            "the rash began to settle", self.PAGES, cited_page=1)
        assert result.verified
        assert result.page_no == 2
        assert "cited page 1" in result.note

    def test_a_quote_on_no_page_still_fails(self):
        result = verify_against_pages(
            "the patient made a full recovery abroad", self.PAGES, cited_page=1)
        assert not result.verified


class TestConfidenceAdjustment:
    def test_unverified_evidence_caps_confidence_at_040(self):
        confidence, reason = adjust_confidence(0.95, evidence_verified=False)
        assert confidence == 0.40
        assert "could not be verified" in reason

    def test_verified_evidence_leaves_confidence_alone(self):
        confidence, reason = adjust_confidence(0.95, evidence_verified=True)
        assert confidence == 0.95
        assert reason == ""

    def test_a_low_confidence_is_not_raised_by_verification(self):
        # The chain only ever caps. Verification is not evidence that a weak inference is strong.
        confidence, _ = adjust_confidence(0.25, evidence_verified=True)
        assert confidence == 0.25

    def test_page_legibility_caps_confidence(self):
        # E34: handwriting uncertainty reaches the field, not just the page.
        confidence, reason = adjust_confidence(
            0.90, evidence_verified=True, page_legibility=0.45)
        assert confidence == 0.45
        assert "legibility" in reason

    def test_a_conflict_caps_confidence_at_050(self):
        confidence, reason = adjust_confidence(
            0.92, evidence_verified=True, in_conflict=True)
        assert confidence == 0.50
        assert "disagree" in reason

    def test_the_strictest_cap_wins_and_every_reason_is_recorded(self):
        confidence, reason = adjust_confidence(
            0.99, evidence_verified=False, page_legibility=0.30, in_conflict=True)
        assert confidence == 0.30
        assert "could not be verified" in reason and "legibility" in reason

    def test_confidence_is_clamped_to_the_unit_interval(self):
        assert adjust_confidence(1.7, evidence_verified=True)[0] == 1.0
        assert adjust_confidence(-0.5, evidence_verified=True)[0] == 0.0
