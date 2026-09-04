"""Per-stage timings and token/cost accounting.

Every AI-service response carries a `timings` and a `usage` block so the Java side can persist
`PROCESSING_METRIC` and `AI_CALL_LOG` rows without guessing (PROJECT_PLAN §10.7).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Usage:
    """Accumulated token counts and dollar cost across every LLM call in one request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0

    def add(self, other: "Usage") -> "Usage":
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cached_tokens += other.cached_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cost_usd += other.cost_usd
        self.llm_calls += other.llm_calls
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "llm_calls": self.llm_calls,
        }


@dataclass
class Telemetry:
    """Collects `<stage>_ms` timings plus a rolled-up `Usage`."""

    timings: dict[str, int] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = int((time.perf_counter() - start) * 1000)
            key = f"{name}_ms"
            self.timings[key] = self.timings.get(key, 0) + elapsed

    def record_ms(self, name: str, ms: int) -> None:
        key = f"{name}_ms"
        self.timings[key] = self.timings.get(key, 0) + ms

    def add_usage(self, usage: Usage) -> None:
        self.usage.add(usage)

    def timings_dict(self) -> dict[str, Any]:
        out = dict(self.timings)
        out["llm_calls"] = self.usage.llm_calls
        return out
