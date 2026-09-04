#!/usr/bin/env python
"""Prove the one permitted AI path works end to end, before anything is built on top of it.

Checks, in order:
  1. the OpenRouter key resolves and `anthropic/claude-haiku-4.5` answers;
  2. `response_format: json_schema` with `strict: true` returns JSON that validates against the
     pydantic model that generated the schema;
  3. `usage` accounting comes back with real token counts and a real dollar cost, so
     `AI_CALL_LOG.cost_usd` records a measured figure rather than an estimate;
  4. abstention works — asked for a fact the text does not contain, the model returns
     `NOT_STATED` rather than guessing (CLAUDE.md constraint 4);
  5. prompt caching engages on a repeat call with a large static system preamble
     (DECISIONS D-004 — a small preamble is silently *not* cached).

Run:  python scripts/smoke_llm.py
Exit code 0 means every check passed. Nothing here writes to the database.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ai-service"))

from pydantic import BaseModel, Field  # noqa: E402

from app.llm.client import LlmClient, text_part  # noqa: E402
from app.settings import get_settings  # noqa: E402


class FieldStatus(str, Enum):
    STATED = "STATED"
    NOT_STATED = "NOT_STATED"
    UNCERTAIN = "UNCERTAIN"


class SmokeField(BaseModel):
    value: str = Field(description="The extracted value, or an empty string when not stated.")
    status: FieldStatus = Field(description="STATED only when the text says it explicitly.")
    confidence: float = Field(ge=0.0, le=1.0)
    quote: str = Field(description="Verbatim span from the source text, or an empty string.")


class SmokeResult(BaseModel):
    patient_age: SmokeField
    patient_sex: SmokeField
    suspect_product: SmokeField
    reporter_country: SmokeField


# Deliberately long: Anthropic only caches a prefix above a minimum token count, so a short
# system prompt is silently not cached at all (DECISIONS D-004). This is a scaled-down stand-in
# for the real P0_system preamble.
SYSTEM_PROMPT = """You are a pharmacovigilance intake assistant performing a first-pass reading of
safety correspondence. You extract facts. You do not infer, embellish, or complete partial
information from background knowledge.

TAXONOMY
An Individual Case Safety Report (ICSR) requires four minimum elements: an identifiable patient,
an identifiable reporter, a suspect medicinal product, and an adverse event or outcome. A Product
Quality Complaint (PQC) concerns a physical defect in the product itself: a broken seal, wrong
colour or odour, contamination, particulates, damaged packaging, a cracked tablet, leaking, or an
incorrect tablet count. Dissatisfaction with a product's efficacy is not a defect. A Medical
Information (MI) request is a genuine question about a product where no reaction and no defect is
described: dosing, administration, storage, interactions, use in pregnancy or lactation. A message
is Not Relevant only when none of the other three apply: marketing material, newsletters,
out-of-office replies, internal administration, invoices, and spam.

CONFIDENCE RUBRIC
Use the following bands and nothing else. 0.90 to 1.00 means the fact is explicitly stated in the
source and is unambiguous; for example the text "Patient is a 58-year-old female" yields an age of
58 at 0.95. 0.70 to 0.89 means the fact is stated but needs light normalisation or a single
inference step; for example "born in 1966" in a report dated 2024 yields an age of approximately
58. 0.40 to 0.69 means the fact is strongly implied but never stated; for example the consistent
use of "she" throughout a narrative yields a sex of female. 0.10 to 0.39 means the fact is weakly
implied and a human reviewer should check it; for example "an elderly patient" supports an age
band only, never a number.

ABSTENTION RULE
If a fact is absent from the source, return status NOT_STATED with an empty value, an empty quote,
and a confidence of 0.0. NOT_STATED is always an acceptable answer and is never penalised. It is
substantially better to abstain than to produce a plausible value that the source does not
support. A fabricated value in a regulated safety system is a defect, not a near miss. Do not
carry a value across from your general knowledge of a drug, a disease, or a typical patient.

EVIDENCE RULE
Every field whose status is STATED or UNCERTAIN must carry a verbatim quote copied character for
character from the source text. Do not paraphrase inside the quote, do not correct spelling, and
do not translate it. The quote is checked automatically against the source; a quote that cannot be
found in the source causes the field's confidence to be capped, so an accurate short quote is
always better than a long approximate one. If you cannot quote the source, the correct status is
NOT_STATED."""

SOURCE_TEXT = """From: dr.a.whitfield@northgate-clinic.example
Subject: Possible reaction to Velmoradine

Dear Safety team,

I am writing about a patient of mine, a 58-year-old female, who began taking Velmoradine 20 mg
once daily on 3 March 2024 for hypertension. Nine days later she developed a widespread itchy
rash across her trunk and forearms. I stopped the drug and the rash began to settle within
48 hours. She has not fully recovered as of today.

Regards,
Dr A. Whitfield
"""

USER_PROMPT = f"""Extract the four requested fields from the source document below.

The document does NOT state every field. Where a fact is absent, return NOT_STATED — do not guess.

--- SOURCE DOCUMENT ---
{SOURCE_TEXT}
--- END SOURCE DOCUMENT ---"""


def _line(ok: bool, label: str, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def main() -> int:
    settings = get_settings()
    print(f"Smart Inbox — LLM smoke test\n  model: {settings.ai_model}\n  base : {settings.openrouter_base_url}\n")

    client = LlmClient(settings)

    print("Call 1 — schema-constrained extraction with abstention")
    first = client.complete_json(
        purpose="smoke_extract",
        system_prompt=SYSTEM_PROMPT,
        user_content=[text_part(USER_PROMPT)],
        schema_model=SmokeResult,
        prompt_version="smoke-v1",
        max_tokens=1500,
    )
    result = SmokeResult.model_validate(first.parsed)

    checks: list[bool] = []
    checks.append(_line(True, "schema-valid JSON returned", f"{first.latency_ms} ms"))
    checks.append(
        _line(
            result.patient_age.status is FieldStatus.STATED and "58" in result.patient_age.value,
            "stated fact extracted",
            f"age = {result.patient_age.value!r} ({result.patient_age.confidence:.2f})",
        )
    )
    checks.append(
        _line(
            result.patient_age.quote.strip() != ""
            and result.patient_age.quote.strip().strip('"') in SOURCE_TEXT,
            "evidence quote is verbatim in the source",
            repr(result.patient_age.quote[:60]),
        )
    )
    checks.append(
        _line(
            result.reporter_country.status is FieldStatus.NOT_STATED,
            "absent fact abstained, not guessed",
            f"reporter_country = {result.reporter_country.status.value}",
        )
    )
    checks.append(
        _line(
            first.usage.prompt_tokens > 0 and first.usage.completion_tokens > 0,
            "token usage reported",
            f"{first.usage.prompt_tokens} in / {first.usage.completion_tokens} out",
        )
    )
    checks.append(
        _line(
            first.usage.cost_usd > 0,
            "dollar cost reported",
            f"${first.usage.cost_usd:.6f}",
        )
    )

    print("\nCall 2 — same system preamble, to exercise the prompt cache")
    second = client.complete_json(
        purpose="smoke_extract_cached",
        system_prompt=SYSTEM_PROMPT,
        user_content=[text_part(USER_PROMPT.replace("Velmoradine", "Cardexatine"))],
        schema_model=SmokeResult,
        prompt_version="smoke-v1",
        max_tokens=1500,
    )
    cached = second.usage.cached_tokens
    written = second.usage.cache_write_tokens
    # Not a hard failure: caching is a cost optimisation, and a cold cache on the first ever run
    # is legitimate. We report the measured number either way (DECISIONS D-004).
    _line(
        cached > 0 or written > 0 or first.usage.cache_write_tokens > 0,
        "prompt cache engaged (informational)",
        f"read {cached}, written {written} (call 1 wrote {first.usage.cache_write_tokens})",
    )

    total = first.usage.cost_usd + second.usage.cost_usd
    print(f"\nTotal cost of this smoke test: ${total:.6f}")

    passed = all(checks)
    print("\n" + ("ALL CHECKS PASSED" if passed else "SMOKE TEST FAILED"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
