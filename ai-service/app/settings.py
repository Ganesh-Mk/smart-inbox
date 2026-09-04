"""Central configuration for the Smart Inbox AI service.

Every limit, path and model parameter lives here so nothing is hard-coded at a call site.
Values come from the environment (see `.env.example` at the repo root); the defaults are the
ones the demo runs with.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- the single permitted model (CLAUDE.md hard constraint 1) -------------------
    #
    # Defaulted to empty rather than required so `Settings()` can be constructed in tests and
    # tooling that never calls a model. Anything that *does* call one must go through
    # `api_key_problem()` first — see `main.on_startup`.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ai_model: str = "anthropic/claude-haiku-4.5"

    # OpenRouter attribution headers (optional, purely cosmetic on their dashboard)
    openrouter_referer: str = "https://github.com/Ganesh-Mk/smart-inbox"
    openrouter_title: str = "Smart Inbox"

    # ---- LLM call policy -----------------------------------------------------------
    llm_temperature: float = 0.0          # extraction, not generation
    llm_max_tokens: int = 8000
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3              # E36: 429/5xx/timeout -> backoff
    llm_backoff_base_s: float = 1.5
    llm_repair_attempts: int = 1          # E36: exactly one schema-repair round-trip

    # ---- PDF limits (E8) -----------------------------------------------------------
    max_pdf_pages: int = 60
    max_attachment_mb: int = 25
    render_dpi: int = 200                 # page -> PNG for vision
    image_render_dpi: int = 150           # cropped regions (tables, embedded images)

    # ---- flavour detection thresholds (E13) ----------------------------------------
    scanned_min_chars: int = 100
    scanned_image_area_ratio: float = 0.80
    scanned_printable_ratio: float = 0.75

    # ---- meaningful-image filter (E19) ---------------------------------------------
    image_min_area_ratio: float = 0.03
    image_min_stddev: float = 12.0
    image_full_page_ratio: float = 0.80   # above this the image *is* the scanned page

    # ---- layout / columns (E14) ----------------------------------------------------
    max_columns: int = 3
    column_separation_ratio: float = 0.12  # min gap between column centres, page-width fraction

    # ---- language detection (E17) --------------------------------------------------
    lang_min_chars: int = 25
    lang_min_confidence: float = 0.60

    # ---- evidence verification (E27) -----------------------------------------------
    evidence_fuzzy_threshold: float = 90.0
    unverified_confidence_cap: float = 0.40
    conflict_confidence_cap: float = 0.50

    # ---- map-reduce summarisation (E20) --------------------------------------------
    summary_chunk_chars: int = 60_000     # ~15k tokens per group
    summary_min_sentences: int = 10
    summary_max_sentences: int = 15

    # ---- storage -------------------------------------------------------------------
    data_dir: Path = REPO_ROOT / "data"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def render_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "parse-cache"


# Anything that looks like it came from `.env.example` rather than from a real account.
_PLACEHOLDER_MARKERS = ("REPLACE_ME", "YOUR_KEY", "XXXX", "<", "changeme")


def api_key_problem(settings: "Settings") -> str | None:
    """Why the OpenRouter key is unusable, or None if it looks fine.

    Only the *shape* is checked — whether the key actually authenticates is OpenRouter's to say.
    The point is to fail at startup rather than on the first job, because the failure mode
    otherwise is expensive and confusing: the service reports UP, the queue hands it work, every
    call fails, each one burns its retries (E36), and the jobs dead-letter one by one. A missing
    key looked exactly like a broken model.

    The return value is safe to log: it never contains the key, only its length.
    """
    key = (settings.openrouter_api_key or "").strip()
    if not key:
        return ("OPENROUTER_API_KEY is not set. Add it to the repo-root .env "
                "(see .env.example) and restart.")
    if any(marker in key for marker in _PLACEHOLDER_MARKERS):
        return ("OPENROUTER_API_KEY still holds the placeholder from .env.example. "
                "Replace it with a real key from https://openrouter.ai/keys and restart.")
    if not key.startswith("sk-or-"):
        return (f"OPENROUTER_API_KEY does not look like an OpenRouter key: it is {len(key)} "
                "characters and does not start with 'sk-or-'. An OpenAI or Anthropic key will "
                "not work here — this service talks to OpenRouter only "
                "(CLAUDE.md hard constraint 1).")
    return None


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.blob_dir, s.render_dir, s.cache_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
