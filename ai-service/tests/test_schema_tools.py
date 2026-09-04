"""Guards on the JSON Schema we transmit.

The point of these tests is not schema hygiene for its own sake. A schema over roughly 4 KB is
**silently discarded** by OpenRouter — no error, an ordinary `stop` finish reason, and
unconstrained output that reads like a model quality problem (DECISIONS D-013). The only
defence is to refuse to build one, and to notice at test time if a schema grows toward the
cliff.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from app.llm.schema_tools import (
    MAX_SCHEMA_BYTES,
    WARN_SCHEMA_BYTES,
    SchemaTooLarge,
    response_format_for,
    schema_size,
    strict_schema,
)
from app.llm.schemas import SCHEMA_REGISTRY, IcsrCase, IcsrParties, IcsrProducts, IcsrReactions


class TestStrictNormalisation:
    def test_every_object_forbids_extra_properties(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object" or "properties" in node:
                    if isinstance(node.get("properties"), dict):
                        assert node.get("additionalProperties") is False
                        assert set(node["required"]) == set(node["properties"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        for model in SCHEMA_REGISTRY.values():
            walk(strict_schema(model))

    def test_optional_fields_are_still_required_because_abstention_is_a_value(self):
        # Nothing is optional in these schemas. "Not stated" is expressed by the status enum,
        # never by leaving a key out — that is what makes abstention free for the model.
        schema = strict_schema(IcsrParties)
        patient = schema["$defs"]["PatientBlock"]
        assert set(patient["required"]) == set(patient["properties"])

    def test_descriptions_are_stripped_from_what_we_transmit(self):
        # They are ~35% of the bytes and are paid for on every call, since response_format is
        # not covered by the prompt cache. The guidance lives in P0_system instead.
        transmitted = json.dumps(strict_schema(IcsrParties))
        assert "description" not in transmitted

    def test_descriptions_can_still_be_inspected_for_documentation(self):
        assert "description" in json.dumps(
            strict_schema(IcsrParties, check_size=False, include_descriptions=True))


class TestSizeCeiling:
    @pytest.mark.parametrize("name,model", sorted(SCHEMA_REGISTRY.items()))
    def test_every_transmitted_schema_is_under_the_ceiling(self, name, model):
        size = schema_size(model)
        assert size <= MAX_SCHEMA_BYTES, (
            f"{name} is {size} B. Over ~4 KB OpenRouter drops the schema without an error and "
            f"the model answers unconstrained. Split the call rather than raising the limit.")

    @pytest.mark.parametrize("name,model", sorted(SCHEMA_REGISTRY.items()))
    def test_no_schema_is_creeping_toward_the_ceiling(self, name, model):
        size = schema_size(model)
        assert size <= WARN_SCHEMA_BYTES, (
            f"{name} is {size} B, past the {WARN_SCHEMA_BYTES} B warning line. It still works, "
            f"but it is close enough to the cliff that the next field added could cross it.")

    def test_the_combined_icsr_schema_is_refused(self):
        # The regression that started all of this. IcsrCase is assembled in code and must never
        # be handed to the model.
        assert schema_size(IcsrCase) > MAX_SCHEMA_BYTES
        with pytest.raises(SchemaTooLarge) as caught:
            strict_schema(IcsrCase)
        assert "Split the task" in str(caught.value)

    def test_icsr_case_is_not_in_the_registry(self):
        assert IcsrCase not in SCHEMA_REGISTRY.values()

    def test_the_split_parts_together_cover_the_whole_case(self):
        parts = {
            *strict_schema(IcsrParties)["properties"],
            *strict_schema(IcsrProducts)["properties"],
            *strict_schema(IcsrReactions)["properties"],
        }
        whole = set(strict_schema(IcsrCase, check_size=False)["properties"])
        assert whole <= parts, f"the split loses {whole - parts}"


class TestResponseFormat:
    def test_response_format_is_strict_and_named(self):
        rf = response_format_for(IcsrProducts)
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True
        assert rf["json_schema"]["name"] == "IcsrProducts"

    def test_an_oversized_model_cannot_become_a_response_format(self):
        # Many fields rather than long descriptions: descriptions are stripped before the size
        # is measured, so padding them would not exercise the guard at all.
        names = [f"field_number_{i:03d}_with_a_reasonably_long_name" for i in range(120)]
        big = type("Bloated", (BaseModel,), {
            "__annotations__": {name: str for name in names},
        })
        assert schema_size(big) > MAX_SCHEMA_BYTES
        with pytest.raises(SchemaTooLarge):
            response_format_for(big)
