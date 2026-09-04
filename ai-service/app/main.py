"""Smart Inbox AI service — FastAPI application entry point.

A stateless pure function: (bytes, task, params) -> JSON. No database handle, no queue,
no session state (PROJECT_PLAN §5.1). Spring Boot owns all state.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routes import analyse, literature, meta, parse
from app.settings import api_key_problem, get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
)
log = logging.getLogger("smartinbox.ai")

app = FastAPI(
    title="Smart Inbox AI service",
    description=(
        "PDF understanding and LLM extraction for the Smart Inbox pharmacovigilance triage "
        "prototype. All inference runs on anthropic/claude-haiku-4.5 via OpenRouter."
    ),
    version="1.0.0",
)


@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Attach a correlation id and log wall-clock latency for every request."""
    correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Correlation-Id"] = correlation_id
    response.headers["X-Elapsed-Ms"] = str(elapsed_ms)
    log.info("%s %s -> %s in %d ms", request.method, request.url.path, response.status_code, elapsed_ms)
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak a stack trace to the caller; never return a silent partial result (E36)."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


app.include_router(meta.router)
app.include_router(parse.router)
app.include_router(analyse.router)
app.include_router(literature.router)


@app.on_event("startup")
async def on_startup() -> None:
    """Refuse to start without a usable key.

    This used to log a warning and carry on. The service then came up reporting `UP`, the queue
    handed it work, and every model call failed — each one burning its retry budget (E36) before
    the job dead-lettered. A missing key was indistinguishable from a broken model, and the cost
    of finding out was a corrupted run.

    Failing here is loud and cheap: uvicorn exits non-zero with a message naming the fix.
    """
    settings = get_settings()
    problem = api_key_problem(settings)
    if problem:
        log.error("Refusing to start: %s", problem)
        raise RuntimeError(problem)
    log.info("Smart Inbox AI service starting — model=%s", settings.ai_model)
