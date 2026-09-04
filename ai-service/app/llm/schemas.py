"""Every LLM output schema, defined once as a pydantic model.

The JSON Schema sent to the model is *generated* from these classes (`schema_tools.py`), so
there is no second hand-written copy to drift out of step with the parsing code. Change the
model and the contract, the validation and the repair round-trip all move together.

Three design rules run through all of them, and each is load-bearing:

* **Shallow and flat.** Deeply nested schemas are where `strict` structured output starts
  refusing requests. Arrays of small flat objects, never objects five levels deep.

* **Abstention is a value, not an omission.** Every fact carries a `status` enum with
  `NOT_STATED` in it. The model is never asked to leave a key out or invent a null convention —
  it selects "not stated" the same way it selects any other answer. That is what makes
  "say unknown, never guess" a schema affordance rather than a plea in the prompt (E26).

* **Evidence is required alongside the value.** A `quote` field sits next to every extracted
  value, so producing a value and producing its provenance are the same act. The quote is then
  proven against the source by `pipeline/verify.py` — the model's citation is never trusted.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# =======================================================================================
# Enums — the fixed vocabularies from PROJECT_PLAN §4
# =======================================================================================

class FieldStatus(str, Enum):
    """`NOT_STATED` is a first-class, unpenalised answer (CLAUDE.md constraint 4)."""

    STATED = "STATED"
    NOT_STATED = "NOT_STATED"
    UNCERTAIN = "UNCERTAIN"


class Category(str, Enum):
    ICSR = "ICSR"
    PQC = "PQC"
    MI = "MI"
    NOT_RELEVANT = "NOT_RELEVANT"


class SeriousnessCriterion(str, Enum):
    """The six regulatory criteria. Seriousness is not an adjective or a slider (E35)."""

    DEATH = "DEATH"
    LIFE_THREATENING = "LIFE_THREATENING"
    HOSPITALISATION_OR_PROLONGATION = "HOSPITALISATION_OR_PROLONGATION"
    DISABILITY_OR_INCAPACITY = "DISABILITY_OR_INCAPACITY"
    CONGENITAL_ANOMALY = "CONGENITAL_ANOMALY"
    OTHER_MEDICALLY_IMPORTANT = "OTHER_MEDICALLY_IMPORTANT"


class Outcome(str, Enum):
    RECOVERED = "RECOVERED"
    RECOVERING = "RECOVERING"
    NOT_RECOVERED = "NOT_RECOVERED"
    RECOVERED_WITH_SEQUELAE = "RECOVERED_WITH_SEQUELAE"
    FATAL = "FATAL"
    UNKNOWN = "UNKNOWN"


class ReporterRole(str, Enum):
    PHYSICIAN = "PHYSICIAN"
    PHARMACIST = "PHARMACIST"
    NURSE = "NURSE"
    OTHER_HCP = "OTHER_HCP"
    PATIENT = "PATIENT"
    CONSUMER = "CONSUMER"
    LAWYER = "LAWYER"
    UNKNOWN = "UNKNOWN"


class Route(str, Enum):
    ORAL = "ORAL"
    IV = "IV"
    IM = "IM"
    SUBCUTANEOUS = "SUBCUTANEOUS"
    TOPICAL = "TOPICAL"
    INHALATION = "INHALATION"
    OPHTHALMIC = "OPHTHALMIC"
    RECTAL = "RECTAL"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class ProductRole(str, Enum):
    SUSPECT = "SUSPECT"
    CONCOMITANT = "CONCOMITANT"


class Sex(str, Enum):
    FEMALE = "FEMALE"
    MALE = "MALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class AgeUnit(str, Enum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    WEEK = "WEEK"
    DAY = "DAY"
    UNKNOWN = "UNKNOWN"


class DatePrecision(str, Enum):
    """E28: how much of a date the source actually gave us."""

    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"
    UNKNOWN = "UNKNOWN"


class ImageCategory(str, Enum):
    PRODUCT_DEFECT = "PRODUCT_DEFECT"
    CLINICAL_PHOTO = "CLINICAL_PHOTO"
    FORM_CHECKBOX = "FORM_CHECKBOX"
    CHART = "CHART"
    OTHER = "OTHER"


class Relevance(str, Enum):
    RELEVANT = "RELEVANT"
    POSSIBLY = "POSSIBLY"
    NOT_RELEVANT = "NOT_RELEVANT"


# =======================================================================================
# The building block: a fact with its evidence
# =======================================================================================

class Fact(BaseModel):
    """One extracted value, its status, its confidence and the quote that supports it.

    The quote is what `pipeline/verify.py` searches for in the source page text. It must be
    copied character for character from the source, in the source language — a paraphrase
    cannot be verified, and an unverifiable fact has its confidence capped at 0.40 (E27).
    """

    value: str = Field(
        description="The extracted value as written, or an empty string when NOT_STATED.")
    status: FieldStatus = Field(
        description="STATED only when the source says it. NOT_STATED is always acceptable.")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Use the confidence rubric in the system prompt. 0.0 when NOT_STATED.")
    quote: str = Field(
        description="Verbatim span from the source supporting this value, or empty. "
                    "Copy exactly: do not paraphrase, correct or translate.")
    page_no: int = Field(
        description="1-based page the quote is on. 0 for the email body or when NOT_STATED.")


class PartialDate(BaseModel):
    """E28: a date is rarely fully known, and a relative date is never resolved for the user."""

    raw: str = Field(description="The date exactly as the source writes it, e.g. 'last March'.")
    iso: str = Field(
        description="ISO-8601 to the precision available (2026-03 for a month), or empty.")
    precision: DatePrecision
    is_relative: bool = Field(
        description="True for 'last March', 'two weeks ago'. Never resolve these against "
                    "today's date — a reviewer must do it.")
    status: FieldStatus
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str
    page_no: int


class Quantity(BaseModel):
    """E29: normalise, but never destroy the source string."""

    amount: str = Field(description="Numeric part as written, e.g. '154', '2'.")
    unit: str = Field(description="Unit as written, e.g. 'lb', 'mg', 'puffs'.")
    raw: str = Field(description="The whole original string, e.g. '154 lb'.")
    status: FieldStatus
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str
    page_no: int


class Age(BaseModel):
    """E30: 'elderly', '3 y.o.', '6-week-old' and a date of birth are all different answers."""

    value: str = Field(description="Numeric age, or empty for a descriptor like 'elderly'.")
    unit: AgeUnit
    raw: str = Field(description="Exactly as the source writes it.")
    derived_from_dob: bool = Field(
        description="True when the age was computed from a date of birth rather than stated.")
    status: FieldStatus = Field(
        description="UNCERTAIN for a free-text descriptor such as 'elderly' — do not coerce "
                    "it to a number.")
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str
    page_no: int


# =======================================================================================
# P1 — classification
# =======================================================================================

class IcsrElementCheck(BaseModel):
    """One of the four ICSR minimum criteria, reported independently (E22)."""

    present: bool
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = Field(description="Verbatim support, or empty when absent.")
    page_no: int


class IcsrElements(BaseModel):
    """The checklist. The ICSR label itself is decided **by rule in code**, not here (E22)."""

    has_identifiable_patient: IcsrElementCheck
    has_identifiable_reporter: IcsrElementCheck
    has_suspect_product: IcsrElementCheck
    has_adverse_event: IcsrElementCheck


class CategoryVerdict(BaseModel):
    """One label with its own independent confidence — never a probability distribution."""

    category: Category
    applies: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence. Say what in the source decided it.")


class ClassificationResult(BaseModel):
    """P1 output.

    The model reports each of the four labels independently and fills in the ICSR checklist.
    It is deliberately **not** asked for a final label set: `NOT_RELEVANT` exclusivity (E21)
    and ICSR validity (E22) are applied afterwards in code, because on the very first smoke
    test the model returned `NOT_RELEVANT` at 0.05 alongside `ICSR` at 0.95 (DECISIONS D-003).
    """

    verdicts: list[CategoryVerdict] = Field(
        description="Exactly one entry per category: ICSR, PQC, MI, NOT_RELEVANT.")
    icsr_elements: IcsrElements
    has_genuine_question: bool = Field(
        description="True when the sender asks a real question about a product (E24).")
    has_physical_defect: bool = Field(
        description="True only for a defect in the product itself, not dissatisfaction with "
                    "how well it worked.")
    primary_language: str = Field(
        description="ISO 639-1 code of the source, or empty if unsure.")
    summary_line: str = Field(description="One sentence describing what this message is.")


# =======================================================================================
# P2/P3/P4 — extraction
# =======================================================================================

class PatientBlock(BaseModel):
    age: Age
    sex: Fact
    initials: Fact
    weight: Quantity
    medical_history: Fact


class ReporterBlock(BaseModel):
    name: Fact
    role: ReporterRole
    role_confidence: float = Field(ge=0.0, le=1.0)
    role_quote: str
    organisation: Fact
    country: Fact


class ProductBlock(BaseModel):
    """E31: cases routinely involve several products."""

    name: Fact
    product_role: ProductRole
    dose_amount: Fact
    dose_unit: Fact
    frequency: Fact
    route: Route
    batch: Fact
    start_date: PartialDate
    end_date: PartialDate
    action_taken: Fact


class ReactionBlock(BaseModel):
    """E31/E35: one reaction, its own outcome, its own seriousness criteria."""

    term: Fact
    onset: PartialDate
    outcome: Outcome
    outcome_confidence: float = Field(ge=0.0, le=1.0)
    outcome_quote: str
    seriousness_criteria: list[SeriousnessCriterion] = Field(
        description="Only criteria the source supports. Empty for a non-serious reaction.")
    seriousness_quote: str


class IcsrParties(BaseModel):
    """P2a — who the case is about and who reported it."""

    patient: PatientBlock
    reporter: ReporterBlock
    case_confidence: float = Field(ge=0.0, le=1.0)


class IcsrProducts(BaseModel):
    """P2b — the medicines involved (E31: usually more than one)."""

    products: list[ProductBlock]
    case_confidence: float = Field(ge=0.0, le=1.0)


class IcsrReactions(BaseModel):
    """P2c — what happened, with seriousness per reaction (E31, E35)."""

    reactions: list[ReactionBlock]
    narrative: str = Field(description="A factual paragraph in the source language.")
    case_confidence: float = Field(ge=0.0, le=1.0)


class IcsrCase(BaseModel):
    """The assembled case.

    **Not sent to the model.** It is the union of the three calls above, assembled in
    `pipeline/extract.py`. A single combined schema is 8,807 bytes — more than twice the
    ~4 KB ceiling above which OpenRouter silently discards the schema and returns
    unconstrained output (DECISIONS D-013). Splitting the extraction into parties, products
    and reactions keeps every schema comfortably transmittable, and is the "decompose the task
    rather than reach for a bigger model" answer the plan anticipated in §19. Each call also
    gets a shorter, more focused instruction, which is worth something on its own.
    """

    patient: PatientBlock
    reporter: ReporterBlock
    products: list[ProductBlock]
    reactions: list[ReactionBlock]
    narrative: str = Field(description="A factual paragraph in the source language.")
    case_confidence: float = Field(ge=0.0, le=1.0)

    @classmethod
    def assemble(
        cls,
        parties: "IcsrParties",
        products: "IcsrProducts",
        reactions: "IcsrReactions",
    ) -> "IcsrCase":
        return cls(
            patient=parties.patient,
            reporter=parties.reporter,
            products=products.products,
            reactions=reactions.reactions,
            narrative=reactions.narrative,
            # The case is only as trustworthy as its weakest part.
            case_confidence=min(
                parties.case_confidence,
                products.case_confidence,
                reactions.case_confidence),
        )


class PqcCase(BaseModel):
    """P3 output — a product quality complaint."""

    product_name: Fact
    batch: Fact
    expiry: Fact
    defect_description: Fact
    defect_category: Fact = Field(
        description="One of PACKAGING, CONTAMINATION, APPEARANCE, COUNT, OTHER.")
    quantity_affected: Fact
    photo_mentioned: bool
    photo_quote: str
    patient_harmed: bool = Field(
        description="True only if someone was harmed — that also makes it an ICSR (E23).")
    case_confidence: float = Field(ge=0.0, le=1.0)


class MiQuestion(BaseModel):
    question: Fact
    topic: Fact


class MiCase(BaseModel):
    """P4 output — a medical information request."""

    questions: list[MiQuestion]
    product_name: Fact
    enquirer_role: ReporterRole
    enquirer_role_quote: str
    mentions_reaction: bool = Field(
        description="True if a reaction is also described — that makes it an ICSR too (E24).")
    case_confidence: float = Field(ge=0.0, le=1.0)


# =======================================================================================
# P5–P10 — supporting calls
# =======================================================================================

class TranscribedSegment(BaseModel):
    text: str
    uncertain: bool = Field(
        description="True where the handwriting could not be read with confidence.")


class PageTranscription(BaseModel):
    """P5 output. `legibility` propagates downstream and caps field confidence (E34)."""

    text: str = Field(description="Everything readable on the page, in reading order.")
    legibility: float = Field(
        ge=0.0, le=1.0,
        description="1.0 for clean print. Lower for poor handwriting or a bad scan. Be "
                    "honest: this caps the confidence of every fact taken from this page.")
    language: str = Field(description="ISO 639-1 code, or empty if unsure.")
    segments: list[TranscribedSegment]
    is_blank: bool


class TranslatedBlock(BaseModel):
    index: int
    english: str


class TranslationResult(BaseModel):
    """P8 output. The original stays canonical; this never becomes evidence (E16)."""

    blocks: list[TranslatedBlock]
    source_language: str


class DocumentSummary(BaseModel):
    """P6 output — R7's 10–15 sentence summary with a relevance verdict."""

    summary: str = Field(description="10 to 15 sentences of plain factual prose, in English.")
    sentence_count: int
    relevance: Relevance
    relevance_reason: str = Field(description="One sentence explaining the verdict.")
    key_points: list[str]


class ImageDescription(BaseModel):
    """P7 output (E19)."""

    description: str = Field(description="One short paragraph of what is visible.")
    category: ImageCategory
    mentions_defect: bool
    mentions_injury: bool
    contains_text: str = Field(description="Any legible text in the image, or empty.")
    confidence: float = Field(ge=0.0, le=1.0)


class TableResult(BaseModel):
    """P10 output — the vision fallback for a borderless table (E18)."""

    headers: list[str]
    rows: list[list[str]]
    caption: str
    confidence: float = Field(ge=0.0, le=1.0)


# =======================================================================================
# P9 — literature screening (bonus)
# =======================================================================================

class ScreenedCase(BaseModel):
    """One patient found inside an article. `case_index` is why the bonus needs no schema
    change — `CASE_RECORD.case_index` existed from day one (E32)."""

    case_index: int
    patient_descriptor: str = Field(description="e.g. '62-year-old male'.")
    summary: str
    page_from: int
    page_to: int
    icsr_elements: IcsrElements
    evidence_quote: str


class ScreeningResult(BaseModel):
    """P9 output — is this a case report, and how many distinct patients does it describe?"""

    is_case_report: bool
    confidence: float = Field(ge=0.0, le=1.0)
    relevance_reason: str
    article_kind: str = Field(
        description="CASE_REPORT, CASE_SERIES, REVIEW, CLINICAL_TRIAL or OTHER.")
    cases: list[ScreenedCase] = Field(
        description="One entry per distinct patient. Empty when not a case report.")
    excluded_sections: list[str]


# Only schemas actually sent to the model. IcsrCase is absent on purpose: it is assembled in
# code from the three P2 calls and is never a response_format (see IcsrCase.assemble).
SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "P1_classify": ClassificationResult,
    "P2a_extract_parties": IcsrParties,
    "P2b_extract_products": IcsrProducts,
    "P2c_extract_reactions": IcsrReactions,
    "P3_extract_pqc": PqcCase,
    "P4_extract_mi": MiCase,
    "P5_transcribe": PageTranscription,
    "P6_summarise": DocumentSummary,
    "P7_describe_image": ImageDescription,
    "P8_translate": TranslationResult,
    "P9_screen_article": ScreeningResult,
    "P10_table_to_json": TableResult,
}
