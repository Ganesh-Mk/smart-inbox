"""Product role and route must be verified like any other assertion.

These two were the only extracted values in the system that nothing checked. `product_role`
inherited the product *name's* confidence and carried no evidence at all, so a role the model had
guessed displayed at whatever the name happened to score; `route` did the same. There was no test
covering product extraction, which is why it survived a full build and two external test passes.

The schema now asks for a quote and a confidence for each (`ProductBlock`), and `flatten_icsr`
runs them through the same verify-then-adjust chain as everything else (E27).
"""

from __future__ import annotations

import pytest

from app.llm.schemas import (
    Age, AgeUnit, DatePrecision, Fact, FieldStatus, IcsrCase, PartialDate, PatientBlock,
    ProductBlock, ProductRole, Quantity, ReporterBlock, ReporterRole, Route,
)
from app.pipeline.extract import SourcePage, flatten_icsr
from app.settings import get_settings

PAGE_TEXT = (
    "ADVERSE EVENT REPORT\n"
    "Suspect medicine: Velmoradine 20 mg, taken by mouth twice daily.\n"
    "The reporter considers this the suspect drug for the reaction.\n"
)


def _fact(value: str, quote: str, confidence: float = 0.9) -> Fact:
    return Fact(value=value, status=FieldStatus.STATED, confidence=confidence, quote=quote,
                page_no=1)


def _absent() -> Fact:
    return Fact(value="", status=FieldStatus.NOT_STATED, confidence=0.0, quote="", page_no=0)


def _date() -> PartialDate:
    return PartialDate(raw="", iso="", precision=DatePrecision.UNKNOWN, is_relative=False,
                       status=FieldStatus.NOT_STATED, confidence=0.0, quote="", page_no=0)


def _age() -> Age:
    return Age(value="", unit=AgeUnit.UNKNOWN, raw="", derived_from_dob=False,
               status=FieldStatus.NOT_STATED, confidence=0.0, quote="", page_no=0)


def _quantity() -> Quantity:
    return Quantity(amount="", unit="", raw="", status=FieldStatus.NOT_STATED,
                    confidence=0.0, quote="", page_no=0)


def _case(role_quote: str, route_quote: str, *, role_conf: float = 0.95,
          route_conf: float = 0.95, route: Route = Route.ORAL) -> IcsrCase:
    product = ProductBlock(
        name=_fact("Velmoradine", "Velmoradine 20 mg"),
        product_role=ProductRole.SUSPECT,
        product_role_confidence=role_conf,
        product_role_quote=role_quote,
        dose_amount=_absent(), dose_unit=_absent(), frequency=_absent(),
        route=route, route_confidence=route_conf, route_quote=route_quote,
        batch=_absent(), start_date=_date(), end_date=_date(), action_taken=_absent(),
    )
    return IcsrCase(
        patient=PatientBlock(
            age=_age(), sex=_absent(), initials=_absent(), weight=_quantity(),
            medical_history=_absent()),
        reporter=ReporterBlock(
            name=_absent(), role=ReporterRole.UNKNOWN, role_confidence=0.0, role_quote="",
            organisation=_absent(), country=_absent()),
        products=[product], reactions=[], narrative="", case_confidence=0.9,
    )


def _field(fields, path):
    return next(f for f in fields if f.field_path == path)


@pytest.fixture
def pages():
    return [SourcePage(document_id=1, page_no=1, text=PAGE_TEXT)]


def test_role_and_route_carry_verified_evidence(pages):
    """A quote that is in the source is proven, and the field keeps its own confidence."""
    fields = flatten_icsr(
        _case(role_quote="the suspect drug for the reaction", route_quote="taken by mouth"),
        pages, get_settings())

    role = _field(fields, "product[0].role")
    route = _field(fields, "product[0].route")

    assert role.evidence and role.evidence[0].verified == "Y"
    assert route.evidence and route.evidence[0].verified == "Y"
    assert role.confidence == pytest.approx(0.95)
    assert route.confidence == pytest.approx(0.95)


def test_a_fabricated_role_quote_is_capped(pages):
    """The differentiator applies here too: an unfindable quote caps confidence at 0.40."""
    fields = flatten_icsr(
        _case(role_quote="the physician named it as the definite cause",
              route_quote="taken by mouth"),
        pages, get_settings())

    role = _field(fields, "product[0].role")
    assert role.evidence[0].verified == "N"
    assert role.confidence <= get_settings().unverified_confidence_cap
    assert role.confidence_pre_adjust == pytest.approx(0.95)


def test_role_confidence_is_no_longer_borrowed_from_the_product_name(pages):
    """The specific defect: role used to display the *name's* confidence.

    The name here is very confident and the role is not. If the two are ever equal again,
    something has gone back to inheriting rather than verifying.
    """
    fields = flatten_icsr(
        _case(role_quote="the suspect drug for the reaction", route_quote="taken by mouth",
              role_conf=0.55),
        pages, get_settings())

    name = _field(fields, "product[0].name")
    role = _field(fields, "product[0].role")

    assert name.confidence == pytest.approx(0.9)
    assert role.confidence == pytest.approx(0.55)


def test_unknown_route_abstains_without_evidence(pages):
    """NOT_STATED is a correct answer and needs no quote (CLAUDE.md constraint 4)."""
    fields = flatten_icsr(
        _case(role_quote="the suspect drug for the reaction", route_quote="",
              route=Route.UNKNOWN),
        pages, get_settings())

    route = _field(fields, "product[0].route")
    assert route.status == "NOT_STATED"
    assert route.evidence == []
    assert route.confidence == 0.0
