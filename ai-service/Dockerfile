# Smart Inbox — AI service (FastAPI; PDF understanding + the one permitted model).
#
# No native OCR stack by design (CLAUDE.md constraint 1): PyMuPDF renders pages locally and
# Claude Haiku 4.5 vision reads them. Every dependency is a pure wheel, so this image needs no
# build toolchain and builds identically on arm64 and amd64.

FROM python:3.12-slim
WORKDIR /srv/ai-service

# app/settings.py resolves REPO_ROOT as parents[2] of itself and points env_file there. Living
# at /srv/ai-service makes that /srv, which holds no .env — configuration comes from the
# environment instead, which is what pydantic-settings prefers anyway.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --system --create-home --uid 10002 inbox && chown -R inbox:inbox /srv
USER inbox

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000
# /health answers 503 when no usable OpenRouter key is present — a service that cannot call a
# model is not healthy. urlopen raises on 503, which exits non-zero. No curl needed.
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health',timeout=5)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
