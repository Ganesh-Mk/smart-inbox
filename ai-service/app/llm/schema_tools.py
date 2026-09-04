"""Turn a pydantic model into a JSON Schema OpenRouter will accept in `strict` mode.

The pydantic model is the *single* definition of every LLM schema (CLAUDE.md conventions) — we
never hand-write a JSON Schema next to a model that already describes the same shape.

Two jobs:

**1. Normalise for strict mode.** Every object needs `additionalProperties: false` and every
declared property listed in `required`. Pydantic emits neither by default. Optionality is
expressed the domain way instead — a `status` enum with `NOT_STATED` — rather than by omitting
keys, which is the affordance that makes abstention free for the model (PROJECT_PLAN §11.4).

**2. Refuse to build a schema the provider will silently drop.** Measured against
`anthropic/claude-haiku-4.5` through OpenRouter on 4 September 2026, a `response_format` schema
above roughly 4 KB of serialised JSON is **discarded without any error**: `prompt_tokens` comes
back identical to a request that sent no schema at all, the response is unconstrained prose or
fenced JSON in whatever shape the model fancied, and `finish_reason` is a perfectly normal
`stop`. Nothing anywhere says the schema was ignored.

    12 inlined fields  3,527 B  ->  prompt_tokens 2,053   schema sent
    16 inlined fields  4,683 B  ->  prompt_tokens    14   schema DROPPED

That is the worst class of failure: silent, and it looks like a model quality problem rather
than a transport problem. So `strict_schema` measures the result and raises. A schema that is
too large is a bug to fix at build time by decomposing the task, not something to discover in
production from oddly-shaped output (DECISIONS D-013).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

# The largest schema observed to be transmitted intact was 3,527 B; the smallest observed to be
# dropped was 4,683 B. 4,000 B leaves a little headroom below the measured cliff.
MAX_SCHEMA_BYTES = 4_000

# Warn before the cliff so a schema drifting toward it is noticed in review. Set just under
# the largest size measured to transmit intact (3,527 B).
WARN_SCHEMA_BYTES = 3_500


class SchemaTooLarge(ValueError):
    """A schema exceeds the size the provider will accept, and would be silently ignored."""

    def __init__(self, model_name: str, size: int) -> None:
        super().__init__(
            f"JSON Schema for {model_name} is {size:,} bytes, over the {MAX_SCHEMA_BYTES:,} "
            f"byte limit. OpenRouter drops schemas this large *without an error*, so the model "
            f"would return unconstrained output that merely looks like a quality problem. "
            f"Split the task into smaller calls rather than raising the limit."
        )
        self.model_name = model_name
        self.size = size


def _normalise(node: Any, *, strip_descriptions: bool) -> None:
    if isinstance(node, dict):
        # Only remove `description` when it is a schema annotation — that is, a string. Inside
        # a `properties` map the same key can be a *field* called "description", whose value is
        # a sub-schema dict. `ImageDescription.description` is exactly that, and popping it
        # blindly deleted the field from the schema while leaving it in `required`.
        if strip_descriptions and isinstance(node.get("description"), str):
            node.pop("description")
        if node.get("type") == "object" or "properties" in node:
            props = node.get("properties")
            if isinstance(props, dict):
                node["additionalProperties"] = False
                node["required"] = list(props.keys())
        for value in node.values():
            _normalise(value, strip_descriptions=strip_descriptions)
    elif isinstance(node, list):
        for item in node:
            _normalise(item, strip_descriptions=strip_descriptions)


def schema_size(model: type[BaseModel], *, include_descriptions: bool = False) -> int:
    """Serialised size of `model`'s strict schema, in bytes."""
    return len(json.dumps(strict_schema(
        model, check_size=False, include_descriptions=include_descriptions)))


def strict_schema(
    model: type[BaseModel],
    *,
    check_size: bool = True,
    include_descriptions: bool = False,
) -> dict[str, Any]:
    """Return `model`'s JSON Schema, normalised for strict structured output.

    **Descriptions are stripped by default.** They are worth roughly 35% of every schema's
    size, and that matters twice over: it is what pushed the extraction schemas past the ~4 KB
    cliff, and — unlike the system prompt — `response_format` is not covered by the prompt
    cache, so every description is paid for in full on every single call.

    The semantics have not been thrown away, they have been moved somewhere better. Field
    conventions live in the `P0_system` preamble (§9, "Field conventions"), which is sent once
    and read from cache thereafter. The pydantic `Field(description=...)` text stays in the
    source as documentation for the humans reading it.

    :raises SchemaTooLarge: when the result would be silently discarded by the provider.
    """
    schema = model.model_json_schema()
    _normalise(schema, strip_descriptions=not include_descriptions)

    if check_size:
        size = len(json.dumps(schema))
        if size > MAX_SCHEMA_BYTES:
            raise SchemaTooLarge(model.__name__, size)

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
