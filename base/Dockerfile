# syntax=docker/dockerfile:1
# Shared runtime for web + cron: Scrapling/Playwright needs Debian slim (not Alpine).
# The local Compose override tags this as scholar-base:latest; Bake links it directly.
# Debian 13 (trixie); Python 3.10.21 — multi-arch index digest (amd64 + arm64).
FROM python:3.10.21-slim-trixie@sha256:9d53d8d4c0e882f61913025db53b3aec4ef74336082a9ab47d8a14e9e8329b00

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# curl: compose / Dockerfile web healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# -l avoids oversized sparse lastlog files with high UIDs
RUN useradd -l -m -s /bin/bash app_user

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv
COPY requirements.lock .

# Scrapling StealthyFetcher uses Playwright Chromium; --with-deps installs OS libs.
RUN mkdir -p /ms-playwright \
    && uv pip install --system --require-hashes --exclude-newer 3d -r requirements.lock \
    && python -m playwright install --with-deps chromium \
    && chown -R app_user:app_user /ms-playwright \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN mkdir -p /app/scholar_data /app/cache /var/log \
    && chown -R app_user:app_user /app /var/log

# Compose `base` service uses command: ['true']; keep a harmless default.
CMD ["true"]
