# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ca-certificates lets httpx verify TLS against the registrar (CATALOG_IGNORE_TLS_ERRORS=false).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Playwright is intentionally not installed: bot/scraper/catalog.py is httpx-only and
# the `playwright` import is a soft optional fallback (see _FETCH_SECTIONS_RETRY_EXCEPTIONS).

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
# Alembic migrations run at startup from bot/main.py; both the config and the
# versions/ directory must be present in the image.
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# Non-root user; /app/data is the mount target for the SQLite file from docker-compose.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app

USER app

CMD ["python", "-m", "bot.main"]
