# syntax=docker/dockerfile:1
# Local builds use scholar-base:latest; Bake supplies the shared base target.
ARG BASE_IMAGE=scholar-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

# gunicorn is already in requirements.txt; keep a no-op-safe install for stale base images.
RUN pip install --no-cache-dir --root-user-action=ignore "gunicorn>=26.0.0"

COPY --chown=app_user:app_user . .

USER app_user

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://127.0.0.1:5000/health || exit 1

CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-", "server:app"]
