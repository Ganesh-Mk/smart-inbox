"""Turn a pydantic model into a JSON Schema OpenRouter will accept in `strict` mode.

The pydantic model is the *single* definition of every LLM schema (CLAUDE.md conventions) —
we never hand-write a JSON Schema next to a model that already describes the same shape.

Strict structured output requires, for every object in the schema:
  * `additionalProperties: false`
  * every declared property listed in `required`

Pydantic emits neither by default (optional fields are omitted from `required`), so we
normalise the generated schema in place. Optionality is expressed in our models the domain
way instead — a `status` enum with `NOT_STATED` — rather than by omitting keys, which is
exactly the affordance that makes abstention free for the model (PROJECT_PLAN §11.4).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def _normalise(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties")
            if isinstance(props, dict):
                node["additionalProperties"] = False
                node["required"] = list(props.keys())
        for value in node.values():
            _normalise(value)
    elif isinstance(node, list):
        for item in node:
            _normalise(item)


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return `model`'s JSON Schema, normalised for strict structured output."""
    schema = model.model_json_schema()
    _normalise(schema)
    return schema


def response_format_for(model: type[BaseModel]) -> dict[str, Any]:
    """Build the `response_format` argument for a chat completion."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "strict": True,
            "schema": strict_schema(model),
        },
    }
