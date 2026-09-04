"""Language detection guards (E17).

Every case here is one that was observed producing a confident wrong answer on the real corpus
before the guard existed. They are regression tests, not hypotheticals.
"""

from __future__ import annotations

import pytest

from app.lang.detect import detect_page, detect_text, roll_up_document

GERMAN_NARRATIVE = (
    "Ein 34-jähriger Mann, der Domitrelle 2 mg zweimal täglich einnahm, stellte sich acht "
    "Wochen nach Therapiebeginn mit Fieber und Mundschleimhautulzerationen vor. Die "
    "Neutrophilenzahl betrug 0,3 x 10^9/L."
)

ENGLISH_NARRATIVE = (
    "A 34-year-old man taking Domitrelle 2 mg twice daily presented with fever and mouth "
    "ulceration eight weeks into treatment. The neutrophil count was 0.3 x 10^9/L."
)

# Verbatim from testdata/generator/corpus_messages.py, so this test measures the same
# proportion of Japanese to Latin text that form_ja.pdf actually contains.
JAPANESE_NARRATIVE = (
    "生後6週の女児に対し、胸部感染症の治療としてNuvexoral経口懸濁液125 mgを1日2回投与した。"
    "投与開始から2日以内に、1日10回を超える嘔吐と強い悪心が出現し、哺乳が困難となった。"
    "本剤の投与を中止し、経口補水療法を行ったところ、約36時間で回復した。"
    "併用薬はなく、既往歴に特記すべき事項はない。再投与は行っていない。"
)

LAB_TABLE_CELLS = [
    "TestResultUnitsReference rangeFlag",
    "Alanine aminotransferase640U/L10 – 40H",
    "Aspartate aminotransferase412U/L10 – 35H",
    "Total bilirubin78umol/L3 – 21H",
    "Alkaline phosphatase196U/L30 – 130H",
    "Serum creatinine94umol/L60 – 110",
    "Neutrophil count3.8x10^9/L2.0 – 7.5",
    "Haemoglobin131g/L120 – 160",
    "C-reactive protein42mg/L< 5H",
]

ENGLISH_FORM_LABELS = [
    "Adverse Event Report Form",
    "Patient initials",
    "Age at onset",
    "Reporter name",
    "Product name",
    "Route of administration",
]


class TestSingleTokens:
    def test_a_long_single_token_is_not_evidence_of_a_language(self):
        # 31 characters, and lingua reports French at 0.82. It is one enum value.
        language, _ = detect_text("HOSPITALISATION_OR_PROLONGATION")
        assert language is None

    def test_underscores_are_not_word_separators(self):
        # Splitting on underscores would turn this into three "words" and defeat the guard.
        assert detect_text("DISABILITY_OR_INCAPACITY_LONG")[0] is None

    def test_a_short_label_is_not_judged(self):
        assert detect_text("Dato")[0] is None
        assert detect_text("Datum")[0] is None


class TestTabularData:
    @pytest.mark.parametrize("cell", LAB_TABLE_CELLS)
    def test_a_lab_cell_has_no_language(self, cell):
        assert detect_text(cell)[0] is None

    def test_a_page_of_only_lab_values_has_no_language(self):
        # Before the prose guard this page came back as German at 1.00 confidence, which would
        # have triggered a translation pass over a table of numbers.
        page = detect_page(LAB_TABLE_CELLS)
        assert page.primary_language is None
        assert page.languages == []

    def test_prose_containing_some_numbers_is_still_prose(self):
        # The guard must not reject a real narrative just because it cites doses and counts.
        language, confidence = detect_text(ENGLISH_NARRATIVE)
        assert language == "en"
        assert confidence > 0.6


class TestPageRollUp:
    def test_english_labels_around_a_german_narrative_report_german(self):
        # E17, and the whole reason detection is per block: the labels outnumber the prose,
        # but the prose is the content.
        page = detect_page(ENGLISH_FORM_LABELS + [GERMAN_NARRATIVE])
        assert page.primary_language == "de"

    def test_english_labels_around_an_english_narrative_report_english(self):
        page = detect_page(ENGLISH_FORM_LABELS + [ENGLISH_NARRATIVE])
        assert page.primary_language == "en"

    def test_japanese_narrative_beats_latin_furniture(self):
        # Product names, addresses and footers are Latin script even in a Japanese document.
        # By raw character count they win; by content they should not.
        latin_furniture = [
            "製造販売業者: Coreline Pharmaceuticals Ltd, Unit 7 Fenway Court, Bramwich",
            "SYNTHETIC TEST DOCUMENT — generated for the Smart Inbox prototype. "
            "No real patient, reporter, product or event is described.",
            "born at 38 weeks, otherwise well",
        ]
        page = detect_page(latin_furniture + [JAPANESE_NARRATIVE])
        assert page.primary_language == "ja"

    def test_a_mixed_page_lists_both_languages(self):
        page = detect_page([GERMAN_NARRATIVE, ENGLISH_NARRATIVE])
        assert page.is_mixed
        assert set(page.languages) == {"de", "en"}


class TestDocumentRollUp:
    def test_document_roll_up_agrees_with_its_pages(self):
        pages = [
            detect_page(ENGLISH_FORM_LABELS + [GERMAN_NARRATIVE]),
            detect_page(LAB_TABLE_CELLS),
        ]
        primary, present = roll_up_document(pages)
        # The lab page contributes nothing, so it cannot dilute the verdict.
        assert primary == "de"
        assert "de" in present

    def test_a_document_with_no_prose_has_no_language(self):
        primary, present = roll_up_document([detect_page(LAB_TABLE_CELLS)])
        assert primary is None
        assert present == []
