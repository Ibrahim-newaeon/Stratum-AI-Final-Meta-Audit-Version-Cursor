# =============================================================================
# Stratum AI - Worker image (repo-root build context)
# =============================================================================
# The Railway `worker` service builds from the repository root and runs the
# same backend image as `api` with SERVICE_ROLE=worker (Celery worker + beat).
# It mirrors backend/Dockerfile; keep the two in sync. The api service itself
# builds from backend/ using backend/Dockerfile.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SERVICE_ROLE=worker

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libxml2-dev \
    libxslt-dev \
    zlib1g-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appgroup && \
    useradd --uid 1000 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

COPY --chown=appuser:appgroup backend/ .

RUN mkdir -p /app/ml_service/training_data /app/uploads /app/temp && \
    chown -R appuser:appgroup /app/ml_service /app/uploads /app/temp && \
    chmod +x /app/docker-entrypoint.sh

USER appuser

CMD ["/app/docker-entrypoint.sh"]
