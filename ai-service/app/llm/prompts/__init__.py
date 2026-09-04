"""Prompt loading and versioning.

Prompts live as markdown files under `app/llm/prompts/<id>/v<N>.md`. They are loaded at
startup, hashed, and the version is recorded on every `AI_CALL_LOG` row — so "which prompt
produced this record?" is answerable from the database, and changing a prompt is a reviewable
diff rather than an invisible edit to a string literal buried in code.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("smartinbox.ai.prompts")

PROMPT_DIR = Path(__file__).resolve().parent
VERSION_PATTERN = re.compile(r"^v(\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    """One versioned prompt template."""

    prompt_id: str
    version: str
    text: str
    sha256: str

    def render(self, **values: object) -> str:
        """Substitute `{{name}}` placeholders.

        Deliberately not `str.format` or Jinja: prompt text is full of braces (JSON examples,
        tables) and a formatter that treats them as syntax turns a prompt edit into a runtime
        error at the worst moment.
        """
        rendered = self.text
        for key, value in values.items():
            rendered = rendered.replace("{{" + key + "}}", str(value))
        return rendered

    @property
    def label(self) -> str:
        return f"{self.prompt_id}@{self.version}"


def _latest_version_file(prompt_id: str) -> Path:
    directory = PROMPT_DIR / prompt_id
    if not directory.is_dir():
        raise FileNotFoundError(f"No prompt directory for {prompt_id!r} at {directory}")

    versions: list[tuple[int, Path]] = []
    for path in directory.iterdir():
        match = VERSION_PATTERN.match(path.name)
        if match:
            versions.append((int(match.group(1)), path))
    if not versions:
        raise FileNotFoundError(f"No v<N>.md files in {directory}")
    return max(versions, key=lambda pair: pair[0])[1]


@lru_cache(maxsize=64)
def load(prompt_id: str, version: str | None = None) -> Prompt:
    """Load a prompt by id, defaulting to its highest version."""
    if version:
        path = PROMPT_DIR / prompt_id / f"{version}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt {prompt_id}@{version} not found at {path}")
    else:
        path = _latest_version_file(prompt_id)

    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return Prompt(
        prompt_id=prompt_id,
        version=path.stem,
        text=text,
        sha256=digest,
    )


def system_prompt() -> Prompt:
    """The large shared preamble sent with every call and marked for prompt caching.

    Its size is functional, not decorative: Anthropic will not cache a prefix below its
    minimum, and a preamble under that threshold is silently not cached at all — measured at
    ~700 tokens giving zero cache hits, and ~4,400 tokens giving an 11.8x cost reduction on the
    cached segment (DECISIONS D-004, D-007).
    """
    return load("P0_system")


def available() -> list[str]:
    return sorted(
        path.name for path in PROMPT_DIR.iterdir()
        if path.is_dir() and not path.name.startswith("__"))
