"""Structured case definitions — the single source that drives both the rendered documents
and the golden labels.

This is the point of the design: a `CaseSpec` is rendered into a PDF or an email body *and*
serialised into `testdata/goldens/*.json`. The ground truth is therefore true **by
construction** rather than by someone reading the generated document afterwards and writing
down what they think it says (PROJECT_PLAN §14.2). If a document says the age is 58, the golden
says 58, because both came from the same field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fixtures import Defect, Patient, Product, Reaction, Reporter


@dataclass
class DrugExposure:
    """One product the case involves, with how it was taken."""

    product: Product
    role: str = "SUSPECT"  # SUSPECT | CONCOMITANT  (E31)
    dose_amount: str | None = None
    dose_unit: str | None = None
    frequency: str | None = None
    route: str | None = None
    batch: str | None = None
    start_date_raw: str | None = None
    start_date_iso: str | None = None
    action_taken: str | None = None

    def dose_phrase(self) -> str:
        bits = [self.product.name]
        if self.dose_amount:
            bits.append(f"{self.dose_amount} {self.dose_unit or ''}".strip())
        if self.frequency:
            bits.append(self.frequency)
        return " ".join(bits)


@dataclass
class ReactionEvent:
    """One adverse event, with its own outcome and seriousness (E31, E35)."""

    reaction: Reaction
    onset_raw: str | None = None
    onset_iso: str | None = None
    onset_precision: str = "DAY"       # DAY | MONTH | YEAR | UNKNOWN  (E28)
    onset_is_relative: bool = False    # E28: never silently resolved against today
    outcome: str | None = None
    serious_criteria: list[str] = field(default_factory=list)

    def is_serious(self) -> bool:
        return bool(self.serious_criteria)


@dataclass
class CaseSpec:
    """One case: who, who reported it, what they took, what happened."""

    case_id: str
    case_type: str                     # ICSR | PQC | MI
    patient: Patient | None = None
    reporter: Reporter | None = None
    drugs: list[DrugExposure] = field(default_factory=list)
    reactions: list[ReactionEvent] = field(default_factory=list)
    defect: Defect | None = None
    defect_batch: str | None = None
    defect_expiry: str | None = None
    photo_mentioned: bool = False
    mi_topic: str | None = None
    mi_question: str | None = None
    narrative: str = ""
    case_index: int = 0

    # ---- the four ICSR minimum criteria, decided by rule and not by the model (E22) ----

    def has_identifiable_patient(self) -> bool:
        """Any descriptor that pins down one person: age, sex, initials, an identifier."""
        p = self.patient
        return p is not None and bool(p.age_raw or p.sex or p.initials)

    def has_identifiable_reporter(self) -> bool:
        return self.reporter is not None and self.reporter.role != "UNKNOWN"

    def has_suspect_product(self) -> bool:
        return any(d.role == "SUSPECT" for d in self.drugs)

    def has_adverse_event(self) -> bool:
        return bool(self.reactions)

    def icsr_elements(self) -> dict[str, bool]:
        return {
            "has_identifiable_patient": self.has_identifiable_patient(),
            "has_identifiable_reporter": self.has_identifiable_reporter(),
            "has_suspect_product": self.has_suspect_product(),
            "has_adverse_event": self.has_adverse_event(),
        }

    def icsr_label(self) -> str | None:
        """ICSR when all four elements are present; ICSR_INCOMPLETE at two or three (E22)."""
        present = sum(self.icsr_elements().values())
        if present == 4:
            return "ICSR"
        if present >= 2:
            return "ICSR_INCOMPLETE"
        return None

    def is_serious(self) -> bool:
        return any(r.is_serious() for r in self.reactions)

    def seriousness_criteria(self) -> list[str]:
        seen: list[str] = []
        for r in self.reactions:
            for c in r.serious_criteria:
                if c not in seen:
                    seen.append(c)
        return seen

    # ---- golden labels ------------------------------------------------------------------

    def golden_fields(self) -> dict[str, Any]:
        """The facts a correct extraction must produce, keyed by field path.

        A key that is absent from this map is a fact the source genuinely does not state, and
        the correct model answer there is `NOT_STATED` — which the evaluation harness scores as
        *abstention correctness*, separately from a miss (PROJECT_PLAN §15).
        """
        out: dict[str, Any] = {}

        if self.patient:
            p = self.patient
            if p.age_value:
                out["patient.age.value"] = p.age_value
                out["patient.age.unit"] = p.age_unit
            if p.age_raw:
                out["patient.age.raw"] = p.age_raw
            if p.sex:
                out["patient.sex"] = p.sex
            if p.initials:
                out["patient.initials"] = p.initials
            if p.weight:
                out["patient.weight.raw"] = p.weight
            if p.medical_history:
                out["patient.medical_history"] = p.medical_history

        if self.reporter:
            r = self.reporter
            out["reporter.name"] = r.name
            out["reporter.role"] = r.role
            if r.country:
                out["reporter.country"] = r.country
            if r.organisation:
                out["reporter.organisation"] = r.organisation

        for i, drug in enumerate(self.drugs):
            out[f"product[{i}].name"] = drug.product.name
            out[f"product[{i}].role"] = drug.role
            if drug.dose_amount:
                out[f"product[{i}].dose.amount"] = drug.dose_amount
                out[f"product[{i}].dose.unit"] = drug.dose_unit
            if drug.frequency:
                out[f"product[{i}].dose.frequency_raw"] = drug.frequency
            if drug.route:
                out[f"product[{i}].route"] = drug.route
            if drug.batch:
                out[f"product[{i}].batch"] = drug.batch
            if drug.start_date_raw:
                out[f"product[{i}].start_date.raw"] = drug.start_date_raw
                if drug.start_date_iso:
                    out[f"product[{i}].start_date.iso"] = drug.start_date_iso

        for i, event in enumerate(self.reactions):
            out[f"reaction[{i}].term"] = event.reaction.term
            if event.outcome:
                out[f"reaction[{i}].outcome"] = event.outcome
            if event.onset_raw:
                out[f"reaction[{i}].onset.raw"] = event.onset_raw
                out[f"reaction[{i}].onset.precision"] = event.onset_precision
                out[f"reaction[{i}].onset.is_relative"] = event.onset_is_relative
                if event.onset_iso:
                    out[f"reaction[{i}].onset.iso"] = event.onset_iso

        if self.reactions:
            out["severity.is_serious"] = self.is_serious()
            out["severity.criteria"] = self.seriousness_criteria()

        if self.defect:
            out["defect.summary"] = self.defect.summary
            out["defect.category"] = self.defect.category
            if self.defect_batch:
                out["product.batch"] = self.defect_batch
            if self.defect_expiry:
                out["product.expiry"] = self.defect_expiry
            out["defect.photo_mentioned"] = self.photo_mentioned

        if self.mi_question:
            out["enquiry.question"] = self.mi_question
            out["enquiry.topic"] = self.mi_topic

        return out
