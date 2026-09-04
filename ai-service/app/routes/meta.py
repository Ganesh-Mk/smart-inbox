"""Health and introspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.settings import get_settings

router = APIRouter(tags=["meta"])


@router.get("/health")
def health() -> dict[str, object]:
    """Liveness probe used by docker-compose and by the Spring Boot client."""
    settings = get_settings()
    return {
        "status": "UP",
        "model": settings.ai_model,
        "api_key_configured": bool(settings.openrouter_api_key),
    }
