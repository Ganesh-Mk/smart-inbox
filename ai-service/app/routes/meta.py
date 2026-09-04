"""Health and introspection endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from fastapi import Response

from app.settings import api_key_problem, get_settings

router = APIRouter(tags=["meta"])


@router.get("/health")
def health(response: Response) -> dict[str, object]:
    """Liveness probe used by docker-compose and by the Spring Boot client.

    A service that cannot call a model is not healthy, whatever its process state. It used to
    answer `UP` with `api_key_configured: false` beside it — true, and useless, because nothing
    reads a subordinate field to decide whether a dependency is usable. Now the status says so
    and the response carries 503, so compose and the Spring client both see it.
    """
    settings = get_settings()
    problem = api_key_problem(settings)
    if problem:
        response.status_code = 503
        return {"status": "DOWN", "model": settings.ai_model,
                "api_key_configured": False, "reason": problem}
    return {
        "status": "UP",
        "model": settings.ai_model,
        "api_key_configured": True,
    }
