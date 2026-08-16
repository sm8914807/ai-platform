# AI Platform API
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt README.md ./
COPY ai_platform ./ai_platform
COPY schemas ./schemas
COPY migrations ./migrations

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e ".[postgres,redis]"

ENV PLATFORM_DB_PATH=/data/registry.db \
    PLATFORM_API_HOST=0.0.0.0 \
    PLATFORM_API_PORT=8080 \
    PYTHONUNBUFFERED=1

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["platform-api"]
