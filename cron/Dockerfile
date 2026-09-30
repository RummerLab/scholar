# syntax=docker/dockerfile:1
# Requires scholar-base:latest (build base/Dockerfile first — see docker-compose.yml).
# Cron daemon runs as root (needs privileges); jobs still run as app_user via crontab -u.
FROM scholar-base:latest

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        cron \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/run/crond

COPY --chown=app_user:app_user . .

RUN touch /var/log/app.log \
    && chown app_user:app_user /var/log/app.log \
    && mkdir -p /app/logs \
    && chown -R app_user:app_user /app/logs \
    && printf '%s\n%s\n' \
        "0 0 * * * cd /app && python -u main.py ynWS968AAAAJ >> /var/log/app.log 2>&1 && python -u main.py g9B1IoQAAAAJ >> /var/log/app.log 2>&1 && python -u main.py cXDRggIAAAAJ >> /var/log/app.log 2>&1" \
        "0 2 * * * cd /app && python -u scripts/revalidate_doi_metrics.py >> /var/log/app.log 2>&1" \
        > /etc/cron.d/scholar_cron \
    && chmod 0644 /etc/cron.d/scholar_cron \
    && crontab -u app_user /etc/cron.d/scholar_cron

CMD ["/usr/sbin/cron", "-f"]
