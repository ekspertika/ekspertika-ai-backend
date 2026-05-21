# syntax=docker/dockerfile:1.7

# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Install uv from the official Astral image (no apt-get needed).
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /app

# Resolve and install runtime dependencies into /app/.venv. We copy only the
# lockfiles first so this layer is cached as long as deps don't change.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8000

# Create a non-root user.
RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app

# Bring the resolved virtualenv from the builder.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Copy only the source the API needs at runtime.
COPY --chown=app:app app/                    ./app/
COPY --chown=app:app config/                 ./config/
COPY --chown=app:app compliance.config.json  ./compliance.config.json

USER app

EXPOSE 8000

# Healthcheck — relies on stdlib (no curl in slim base).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request, sys; \
url=f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=3).status == 200 else 1)" || exit 1

# Railway / Render inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
