# Scholar API

This project uses a home server cronjob to scrape [Google Scholar](https://scholar.google.com.au/) data via the [scholarly](https://github.com/scholarly-python-package/scholarly) Python package and the Google Sheets to get the journal's impact factor (IF) and the publication's [DOI](https://doi.org/).

The JSON can then be used, for example, by uploading the data to a publicly accessible server via Secure Copy (SCP) or rsync, which serves the JSON data via a Flask application.

News and media coverage for the lab site lives in [rummerlab-website `data/media.ts`](https://github.com/RummerLab/rummerlab-website/blob/main/data/media.ts) — this API no longer scrapes or serves news/media.

See also [Zotero](https://www.zotero.org/), an [open source citation manager](https://github.com/zotero/zotero).

`python -m venv scholar && source scholar/bin/activate`

## Installation

### Prerequisites

- Python 3.10+ (required by Scrapling)
- Flask
- scholarly
- Wikipedia

### Setup

1. Clone this repository to your local machine.
2. Install the required packages.
3. Setup.

    ```bash
    git clone https://github.com/Luen/scholarly-api
    python -m venv scholar
    source scholar/bin/activate
    pip install -r requirements.txt
    # Optional: lint + test tools
    pip install -r requirements-dev.txt
    ```

4. Test run.

    ```bash
    python main.py ynWS968AAAAJ
    ```

## Docker

Images are based on `python:3.10-slim-trixie` (Debian 13). The stack includes:

- **web** – Gunicorn + Flask API serving scholar data
- **cron** – Runs the main scraper and DOI metrics revalidation on a schedule

**Cron schedule** (in `cron/Dockerfile`): full scholar pipeline (`python -u main.py <id>`) at **00:00 daily**; DOI metrics revalidation at **02:00 daily** — each for the three scholar IDs in that file.

Build the base image first (web and cron use it; the base container exits immediately). The base image installs Playwright Chromium (for Scrapling browser DOI fetches):

```bash
docker compose build base
```

The **web** image installs Gunicorn again in its own layer and starts the app with `python -m gunicorn`, so the API starts even if `scholar-base:latest` was built earlier without Gunicorn (e.g. stale cache or compose building web before base finished).

Then start all services (or build and start in one go):

```bash
docker compose up -d
# or, to (re)build everything: docker compose build base && docker compose up -d --build
```

**Typical home-server update** after pulling code (rebuild containers, then follow logs; `Ctrl+C` stops log follow only):

```bash
git pull; docker compose up -d --build; docker compose logs -f
```

For local (non-Docker) browser-based DOI fetching on sites that block plain HTTP, install [Scrapling](https://github.com/D4Vinci/Scrapling) browser deps with `scrapling install` after `pip install -r requirements.txt`.

### Caching

HTTP responses are cached with [requests-cache](https://requests-cache.readthedocs.io/) in `cache/` (SQLite). This includes:

- Scholarly (Google Scholar) requests
- DOI API requests (doi.org, shortdoi.org)
- Scrapling (browser-fetched HTML for DOI extraction)
- Web page fetches for DOI extraction

Set `CACHE_DIR` to change the cache location; `CACHE_EXPIRE_SECONDS` (default: 30 days) to control expiry.

Wait for containers to be ready (check status with `docker compose ps`). Then to manually run the script:

```bash
# First check if containers are ready
docker compose ps

# If containers are running, execute the script
docker compose exec cron python main.py ynWS968AAAAJ

# If you get a "container is restarting" error, check logs
docker compose logs web
```

### Testing SOCKS5 proxies

If you use `SOCKS5_PROXIES` (see `.env.template`), you can test each proxy from inside the web container:

```bash
docker exec scholar_web python -c "
import os, requests
from urllib.parse import quote
raw = os.environ.get('SOCKS5_PROXIES', '').strip()
if not raw:
    print('No SOCKS5_PROXIES set'); exit(0)
for i, entry in enumerate([p.strip() for p in raw.replace(';', chr(10)).splitlines() if p.strip()]):
    parts = entry.split('|', 2)
    if len(parts) < 3:
        print(f'Proxy {i+1}: invalid format'); continue
    host_port, user, passw = parts[0].strip(), parts[1].strip(), parts[2].strip()
    url = 'socks5://' + quote(user, safe='') + ':' + quote(passw, safe='') + '@' + host_port
    try:
        r = requests.get('https://api.altmetric.com/v1/doi/10.1038/nature.2014.14950', proxies={'http': url, 'https': url}, timeout=15)
        print(f'Proxy {i+1} ({host_port}): OK')
    except Exception as e:
        print(f'Proxy {i+1} ({host_port}): FAIL - {e}')
"
```

```bash
docker exec scholar_web python -c "
import os, requests
from urllib.parse import quote
raw = os.environ.get('SOCKS5_PROXIES', '').strip()
if not raw:
    print('No SOCKS5_PROXIES set'); exit(0)
for i, entry in enumerate([p.strip() for p in raw.replace(';', chr(10)).splitlines() if p.strip()]):
    parts = entry.split('|', 2)
    if len(parts) < 3:
        print(f'Proxy {i+1}: invalid format'); continue
    host_port, user, passw = parts[0].strip(), parts[1].strip(), parts[2].strip()
    url = 'socks5://' + quote(user, safe='') + ':' + quote(passw, safe='') + '@' + host_port
    try:
        r = requests.get('https://www.altmetric.com/details/doi/10.1038/s41586-025-08665-0', proxies={'http': url, 'https': url}, timeout=30)
        print(f'Proxy {i+1} ({host_port}): OK status={r.status_code}')
    except Exception as e:
        print(f'Proxy {i+1} ({host_port}): FAIL - {e}')
"
```

Test whether each proxy is blocked by Google Scholar (CAPTCHA / "unusual traffic"). This script **only uses SOCKS5_PROXIES** (it does not use Tor). The revalidation app tries **Tor first**, then SOCKS5, so if Tor is blocked you will see "blocked on all proxies" even when SOCKS5 work in this test. To use SOCKS5 only in the app, unset `TOR_PROXY` in `.env`.

```bash
docker exec scholar_web python -c "
import os, requests
from urllib.parse import quote
raw = os.environ.get('SOCKS5_PROXIES', '').strip()
if not raw:
    print('No SOCKS5_PROXIES set'); exit(0)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://scholar.google.com/',
}
block_signals = ('captcha', 'recaptcha', 'unusual traffic', 'automated queries', 'our systems have detected', 'sorry, we have detected')
scholar_url = 'https://scholar.google.com/scholar?hl=en&as_sdt=0%2C5&q=' + quote('10.1111/1365-2435.70147') + '&btnG='
for i, entry in enumerate([p.strip() for p in raw.replace(';', chr(10)).splitlines() if p.strip()]):
    parts = entry.split('|', 2)
    if len(parts) < 3:
        print(f'Proxy {i+1}: invalid format'); continue
    host_port, user, passw = parts[0].strip(), parts[1].strip(), parts[2].strip()
    url = 'socks5://' + quote(user, safe='') + ':' + quote(passw, safe='') + '@' + host_port
    try:
        r = requests.get(scholar_url, headers=headers, proxies={'http': url, 'https': url}, timeout=30)
        lower = r.text.lower()
        blocked = any(s in lower for s in block_signals) or 'scholar.google.com' not in (r.url or '')
        if blocked:
            print(f'Proxy {i+1} ({host_port}): BLOCKED (CAPTCHA or rate limit)')
        else:
            print(f'Proxy {i+1} ({host_port}): OK (not blocked) status={r.status_code}')
    except Exception as e:
        print(f'Proxy {i+1} ({host_port}): FAIL - {e}')
"
```

The app tries **TOR_PROXY first** (up to 3 attempts), then **each SOCKS5 proxy** in order. If Tor is blocked (common), you see 3 blocked warnings then 4 more for SOCKS5; if SOCKS5 also return blocked (e.g. different query or rate limit), you see "blocked on all 7". To try SOCKS5 only: unset `TOR_PROXY` in `.env` and re-run revalidation. To see why a response was treated as blocked, run with `LOG_LEVEL=DEBUG` and check logs for "Blocked response: url=...".

### Revalidating DOI metrics cache

Refreshes Crossref, Altmetric, and Google Scholar cache. Runs daily at 02:00 in the cron container. DOIs are read from `scholar_data` (all publications with a DOI):

```bash
docker exec scholar_web python -u scripts/revalidate_doi_metrics.py
```

- **Phase 1 (every run):** Refetches DOIs with no cache or with a blocked/warning cache (missing or previously failed), so they are retried on each daily run.
- **Phase 2 (only after 7 days):** Revalidates DOIs that have successful cache older than a week.

To **remove DOIs from the data and cache** so they no longer appear or get refetched, run `scripts/remove_dois_from_data_and_cache.py DOI [DOI ...]` (e.g. `docker exec scholar_web python scripts/remove_dois_from_data_and_cache.py 10.1093/conphys/coab030 10.14288/1.0071389`).

Neither phase uses `force_refresh`, so if a request is blocked you keep existing cache. Run manually after fixing proxies to fill in missing DOIs.

## Project structure

- `main.py` – Full Scholar scrape with DOI enrichment and idempotency (`last_fetched`)
- `src/scholar_fetcher.py` – Author, coauthors, publications from scholarly (with retries)
- `src/doi_resolver.py` – DOI lookup and resolution (with retries)
- `src/output.py` – Load/save JSON, `schema_version`, `last_fetched`, resume indices
- `src/config.py` – Config loaded from `.env`
- `src/retry.py` – Retry decorator with exponential backoff
- `src/logging_config.py` – Structured logging (text or JSON)

Output JSON includes `schema_version`, `last_fetched`, and `_last_successful_*_index` for resume support.

## Development

Coding agents should follow [`AGENTS.md`](AGENTS.md) ([format](https://agents.md/)).

### Linting and formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Run after code changes:

```bash
ruff check . --fix && ruff format .
```

### Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests marked `integration` require network access. Tests that need `google-credentials.json` or Scrapling browsers will skip when unavailable. Run lint and format before committing:

```bash
ruff check . && ruff format --check .
```

### Security scanning

GitHub CodeQL default setup is enabled for this repo. Ensure **Python** is included under Settings → Code security → CodeQL analysis (Actions alone will not cover these path-injection findings). Keep secrets out of git: `.env` and `google-credentials.json` are gitignored — use `.env.template` as the template only.

## Starting the API server

**Docker (production-style):** the `web` image starts **[Gunicorn](https://docs.gunicorn.org/)** (`server:app`), not Flask’s development server. Access logs and errors go to stdout/stderr for `docker compose logs`.

```bash
docker compose up web cron -d --build
docker compose down; docker volume rm scholar_cache; docker compose up web cron -d --build
```

The API is available at `http://localhost:8000` (Docker maps 8000→5000).

**Local development:** use Flask’s built-in server (handy for quick runs; do not use as production):

```bash
python server.py
```

### API Endpoints

| URL | Method | Description |
|-----|--------|-------------|
| `/` | GET | Welcome message |
| `/health` | GET | Health check |
| `/scholars` | GET | List available scholar IDs |
| `/scholar/<id>` | GET | Get scholar data by ID (e.g. `/scholar/ynWS968AAAAJ`) |
| `/altmetric/<doi>` | GET | Altmetric score for a DOI (cached 2 weeks). 401 if not Rummer/Bergseth/Wu |
| `/scholar-citations/<doi>` | GET | Google Scholar citation count for a DOI (cached 2 weeks). 401 if not Rummer/Bergseth/Wu |
| `/crossref/<doi>` | GET | Crossref works API data for a DOI (cached 1 month). 404 if not found |

### Environment variables

- `FLASK_HOST` – Bind host (default: `0.0.0.0`)
- `FLASK_PORT` – Bind port (default: `5000`)
- `SCHOLAR_DATA_DIR` – Path to scholar JSON files (default: `scholar_data`)
- `CACHE_DIR` – HTTP cache directory (default: `cache`)
- `CACHE_EXPIRE_SECONDS` – Cache expiry (default: 30 days)
- `FRESH_DATA_SECONDS` – Skip full fetch if data is newer (default: 23 hours)
- `MAX_RETRIES`, `RETRY_BASE_DELAY` – Retry settings for Scholar/DOI APIs
- `COAUTHOR_DELAY`, `PUBLICATION_DELAY` – Rate limiting (seconds)
- `LOG_FORMAT` – Set to `json` for structured JSON logs (e.g. in Docker)
