"""The single LLM path: `openai` SDK -> OpenRouter -> anthropic/claude-haiku-4.5.

Hard constraints this module enforces (CLAUDE.md):
  * one model, one provider — the model id comes from settings and is never overridden per call;
  * `temperature=0` on every call, because this is extraction, not generation;
  * the OpenRouter PDF `file-parser` plugin is never used (its default engine is another
    vendor's OCR model). Scanned pages reach the model as PNG images we render ourselves.

Behaviour specified by the plan:
  * E36 — transport failures (429 / 5xx / timeout) retry with exponential backoff and jitter,
    `llm_max_retries` attempts; schema-invalid JSON gets exactly one repair round-trip carrying
    the validation error; still invalid raises, and the caller fails the job loudly.
  * §11.3 — the large static system preamble is sent with `cache_control: ephemeral` so it is
    written once and read cheaply across a batch. Cache effectiveness is *measured* from
    `usage.prompt_tokens_details.cached_tokens`, never assumed (DECISIONS D-004).
  * §10.7 — every call returns exact token counts and dollar cost for `AI_CALL_LOG`.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.llm.schema_tools import response_format_for
from app.settings import Settings, get_settings
from app.telemetry import Usage

log = logging.getLogger("smartinbox.ai.llm")

# Published anthropic/claude-haiku-4.5 pricing, USD per million tokens (PROJECT_PLAN §6.1).
# Only used as a fallback: OpenRouter reports the true figure in `usage.cost`, which is what
# we store when present.
PRICE_INPUT_PER_MTOK = 1.00
PRICE_OUTPUT_PER_MTOK = 5.00
PRICE_CACHE_READ_PER_MTOK = 0.10
PRICE_CACHE_WRITE_PER_MTOK = 1.25

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class LlmError(RuntimeError):
    """Raised when a call cannot be completed within the retry and repair policy."""


class SchemaRepairFailed(LlmError):
    """The model returned JSON that failed validation twice (E36)."""


@dataclass
class LlmCall:
    """Everything Java needs to write one `AI_CALL_LOG` row, plus the parsed result."""

    purpose: str
    model: str
    prompt_version: str
    parsed: dict[str, Any]
    raw_text: str
    usage: Usage
    latency_ms: int
    http_status: int
    retries: int = 0
    repaired: bool = False
    request_json: dict[str, Any] = field(default_factory=dict)
    response_json: dict[str, Any] = field(default_factory=dict)

    def to_log_dict(self) -> dict[str, Any]:
        return {
            "purpose": self.purpose,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "request_json": self.request_json,
            "response_json": self.response_json,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "cached_tokens": self.usage.cached_tokens,
            "cost_usd": round(self.usage.cost_usd, 6),
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "retries": self.retries,
            "repaired": "Y" if self.repaired else "N",
        }


def text_part(text: str, *, cache: bool = False) -> dict[str, Any]:
    """A text content part, optionally marked as a prompt-cache breakpoint."""
    part: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        part["cache_control"] = {"type": "ephemeral"}
    return part


def image_part(png_bytes_b64: str, *, media_type: str = "image/png") -> dict[str, Any]:
    """An image content part carrying a page or region we rendered locally."""
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{png_bytes_b64}"},
    }


def estimate_cost(usage: Usage) -> float:
    """Fallback cost estimate when OpenRouter does not report `usage.cost`."""
    fresh_input = max(usage.prompt_tokens - usage.cached_tokens - usage.cache_write_tokens, 0)
    return (
        fresh_input * PRICE_INPUT_PER_MTOK
        + usage.cached_tokens * PRICE_CACHE_READ_PER_MTOK
        + usage.cache_write_tokens * PRICE_CACHE_WRITE_PER_MTOK
        + usage.completion_tokens * PRICE_OUTPUT_PER_MTOK
    ) / 1_000_000


class LlmClient:
    """Thin, synchronous wrapper around the one permitted model."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.openrouter_api_key:
            raise LlmError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and fill it in."
            )
        self._client = OpenAI(
            api_key=self.settings.openrouter_api_key,
            base_url=self.settings.openrouter_base_url,
            timeout=self.settings.llm_timeout_s,
            max_retries=0,  # we own the retry policy (E36)
            default_headers={
                "HTTP-Referer": self.settings.openrouter_referer,
                "X-Title": self.settings.openrouter_title,
            },
        )

    # -- public API ------------------------------------------------------------------

    def complete_json(
        self,
        *,
        purpose: str,
        system_prompt: str,
        user_content: str | Sequence[dict[str, Any]],
        schema_model: type[BaseModel],
        prompt_version: str,
        max_tokens: int | None = None,
        cache_system: bool = True,
    ) -> LlmCall:
        """Run one schema-constrained completion and return the validated result.

        `user_content` is either a plain string or a list of OpenAI-style content parts
        (mix text and images freely — the model is multimodal).
        """
        parts: list[dict[str, Any]] = (
            [text_part(user_content)] if isinstance(user_content, str) else list(user_content)
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": [text_part(system_prompt, cache=cache_system)]},
            {"role": "user", "content": parts},
        ]

        call = self._invoke(
            purpose=purpose,
            messages=messages,
            schema_model=schema_model,
            prompt_version=prompt_version,
            max_tokens=max_tokens,
        )

        try:
            schema_model.model_validate(call.parsed)
            return call
        except ValidationError as first_error:
            if self.settings.llm_repair_attempts < 1:
                raise SchemaRepairFailed(str(first_error)) from first_error
            log.warning("[%s] schema validation failed, attempting one repair: %s", purpose, first_error)

        # E36: exactly one repair round-trip, with the validation error in the conversation.
        repair_messages = messages + [
            {"role": "assistant", "content": call.raw_text},
            {
                "role": "user",
                "content": [
                    text_part(
                        "Your previous reply did not satisfy the required JSON schema.\n"
                        f"Validation errors:\n{_short(str(first_error))}\n\n"
                        "Reply again with the corrected JSON object only. Do not add commentary. "
                        "Do not invent values to satisfy the schema — use the documented "
                        "'NOT_STATED' status where a fact is genuinely absent."
                    )
                ],
            },
        ]
        repaired_call = self._invoke(
            purpose=f"{purpose}:repair",
            messages=repair_messages,
            schema_model=schema_model,
            prompt_version=prompt_version,
            max_tokens=max_tokens,
        )
        repaired_call.usage.add(call.usage)
        repaired_call.retries += call.retries
        repaired_call.repaired = True
        repaired_call.purpose = purpose

        try:
            schema_model.model_validate(repaired_call.parsed)
        except ValidationError as second_error:
            raise SchemaRepairFailed(
                f"{purpose}: schema still invalid after one repair round-trip: {second_error}"
            ) from second_error
        return repaired_call

    # -- internals -------------------------------------------------------------------

    def _invoke(
        self,
        *,
        purpose: str,
        messages: list[dict[str, Any]],
        schema_model: type[BaseModel],
        prompt_version: str,
        max_tokens: int | None,
    ) -> LlmCall:
        request: dict[str, Any] = {
            "model": self.settings.ai_model,
            "messages": messages,
            "response_format": response_format_for(schema_model),
            "temperature": self.settings.llm_temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
        }

        attempt = 0
        started = time.perf_counter()
        last_error: Exception | None = None

        while attempt < self.settings.llm_max_retries:
            try:
                completion = self._client.chat.completions.create(
                    **request,
                    extra_body={"usage": {"include": True}},
                )
                latency_ms = int((time.perf_counter() - started) * 1000)
                return self._to_call(
                    purpose=purpose,
                    prompt_version=prompt_version,
                    request=request,
                    completion=completion,
                    latency_ms=latency_ms,
                    retries=attempt,
                )
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code not in RETRYABLE_STATUS:
                    raise LlmError(f"{purpose}: OpenRouter returned {exc.status_code}: {exc}") from exc
                last_error = exc

            attempt += 1
            if attempt >= self.settings.llm_max_retries:
                break
            delay = self.settings.llm_backoff_base_s * (2**(attempt - 1))
            delay += random.uniform(0, delay * 0.25)  # jitter
            log.warning(
                "[%s] transport failure (attempt %d/%d), retrying in %.1fs: %s",
                purpose, attempt, self.settings.llm_max_retries, delay, last_error,
            )
            time.sleep(delay)

        raise LlmError(f"{purpose}: giving up after {attempt} attempts: {last_error}")

    def _to_call(
        self,
        *,
        purpose: str,
        prompt_version: str,
        request: dict[str, Any],
        completion: Any,
        latency_ms: int,
        retries: int,
    ) -> LlmCall:
        raw = completion.choices[0].message.content or ""
        usage = self._usage_of(completion)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        response_json = completion.model_dump(mode="json") if hasattr(completion, "model_dump") else {}
        return LlmCall(
            purpose=purpose,
            model=self.settings.ai_model,
            prompt_version=prompt_version,
            parsed=parsed if isinstance(parsed, dict) else {"value": parsed},
            raw_text=raw,
            usage=usage,
            latency_ms=latency_ms,
            http_status=200,
            retries=retries,
            request_json=_redacted_request(request),
            response_json=response_json,
        )

    @staticmethod
    def _usage_of(completion: Any) -> Usage:
        raw = getattr(completion, "usage", None)
        if raw is None:
            return Usage(llm_calls=1)
        data = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
        details = data.get("prompt_tokens_details") or {}
        usage = Usage(
            prompt_tokens=int(data.get("prompt_tokens") or 0),
            completion_tokens=int(data.get("completion_tokens") or 0),
            # OpenRouter reports both cache counters inside `prompt_tokens_details`, and both
            # are already included in `prompt_tokens` (verified live, 4 Sep 2026 — see D-007).
            cached_tokens=int((details or {}).get("cached_tokens") or 0),
            cache_write_tokens=int((details or {}).get("cache_write_tokens") or 0),
            llm_calls=1,
        )
        reported = data.get("cost")
        usage.cost_usd = float(reported) if reported is not None else estimate_cost(usage)
        return usage


def _short(text: str, limit: int = 1500) -> str:
    return text if len(text) <= limit else text[:limit] + " …(truncated)"


def _redacted_request(request: dict[str, Any]) -> dict[str, Any]:
    """Store the request for audit, but never store base64 image payloads or a key.

    Image parts are replaced by a placeholder naming their size — the rendered PNG is already
    on disk under `data/renders/`, so the audit trail stays complete without bloating the CLOB.
    """
    out = json.loads(json.dumps(request, default=str))
    for message in out.get("messages", []):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if part.get("type") == "image_url":
                url = part.get("image_url", {}).get("url", "")
                part["image_url"] = {"url": f"<image omitted: {len(url)} b64 chars>"}
    return out
