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
    git clone https://github.com/RummerLab/scholar.git
    cd scholar
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

### Odroid / GHCR deployment

The default `docker-compose.yml` pulls release images from GitHub Container Registry:

- `ghcr.io/rummerlab/scholar:latest` — web API
- `ghcr.io/rummerlab/scholar-cron:latest` — scheduled scraper

Both images support **linux/arm64** (including the Odroid-M2 with a 64-bit Ubuntu OS) and **linux/amd64**. Docker selects the correct architecture automatically. Playwright Chromium requires a 64-bit OS; 32-bit ARM is not supported. Images become available after the first successful **Release** workflow containing these changes.

For an existing installation in `~/scholar`, after the changes are merged and the first release images have been published:

```bash
cd ~/scholar
git pull --ff-only
docker compose pull
docker compose up -d --no-build --remove-orphans
docker compose ps
curl --fail http://localhost:8000/health
```

Keep your existing `.env` and `google-credentials.json`. The Compose project name, container names, port **8000**, and named volumes are unchanged, so running these commands from the same directory preserves existing scholar data, caches, and downloads. If you previously used `-p` or `COMPOSE_PROJECT_NAME`, keep that same project name. Do not remove the volumes. `--remove-orphans` removes the obsolete, exited base service. For a new installation, copy `.env.template` to `.env` and supply your Google credentials before starting.

If either image returns an authentication error, the package owner must make **both** packages public under the RummerLab organization’s Packages → package → Package settings → Change visibility. GHCR packages are initially private even for public repositories ([GitHub documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)). Alternatively, use `docker login ghcr.io` with a classic GitHub token with `read:packages`; private pulls by Watchtower also require its registry authentication configuration ([Watchtower documentation](https://containrrr.dev/watchtower/private-registries/)). No registry token belongs in the repository or image.

### Automatic updates with your existing Watchtower

Both services have `com.centurylinklabs.watchtower.enable=true`. Keep your existing Watchtower container: it will monitor these images by default, or when configured with `--label-enable`. If it restricts updates to container names, include **scholar_web** and **scholar_cron**; if it uses `--scope`, add its matching `com.centurylinklabs.watchtower.scope` label to both services. Ensure it is configured to update containers, rather than only monitor them. See [Watchtower container selection](https://containrrr.dev/watchtower/container-selection/).

With `SCHOLAR_IMAGE_TAG=latest` (the default), each successful stable release advances both `latest` tags; Watchtower pulls changed images and recreates the containers on its existing schedule, retaining their volumes and configuration. Each recreation briefly interrupts that service. Watchtower does not apply future Compose or `.env` changes: apply those with `git pull --ff-only` and `docker compose up -d --no-build`.

For a manual update:

```bash
docker compose pull && docker compose up -d --no-build
```

To pin or roll back both services, set `SCHOLAR_IMAGE_TAG` in `.env` to a published version such as `v1.2.3`, then run the same command. A version pin stops following new releases; restore `latest` to resume. Rollback retains the data volumes. The original [containrrr/watchtower project](https://github.com/containrrr/watchtower) is archived; the image labels work with your existing installation.

### Building locally

Local builds remain available through an explicit Compose override. Build the shared runtime first, then build and start web/cron:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml build base
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

The override disables Watchtower updates for the locally built containers. For CI-style builds sharing the base automatically, use `docker buildx bake -f docker-bake.hcl --load` (requires Buildx); this produces `scholar:smoke` and `scholar-cron:smoke` for the host architecture.

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
python -m pytest tests/ -m "not integration and not credentials"
```

This is the same suite used in CI. It includes Flask response contracts and a real local HTTP server for the Rummer, Wu, and Bergseth profiles (`g9B1IoQAAAAJ` for Brock), publications, and DOI metrics. Synthetic data and isolated temporary caches keep these checks independent of production data, credentials, and upstream API availability. Tests cover author validation, encoded DOIs, cache revalidation, pagination, missing/corrupt data, and provider error responses.

Tests marked `integration` contact external services or need browsers. Run them explicitly with `python -m pytest tests/ -m integration`; credential tests require `google-credentials.json`. Run lint and format before committing:

```bash
ruff check . && ruff format --check .
```

### Production smoke test and releases

With Docker running, execute from the repository root (works on Windows and Linux):

```bash
python -m tests.smoke_web
```

This builds the actual base and web Dockerfiles, starts the web image with its production Gunicorn command and non-root user, waits for its Docker health check, and verifies profile, publication, and metric responses over HTTP for all three scholars. It mounts synthetic profiles and fresh DOI caches from a temporary directory and disables external networking. The container is removed after the check, including on failures, and its logs are printed. To check an image already built, use `python -m tests.smoke_web --no-build --image scholar-web:smoke`.

GitHub Actions runs Ruff, the offline suite on Python **3.10** (the Docker runtime) and **3.12**, and production image checks on native **AMD64 and ARM64** runners. Both architectures build the actual web and cron images through `docker-bake.hcl`, verify the web HTTP contracts, launch Chromium without network access, and check the installed scraper schedule. These checks run on pull requests, pushes to `main`/`master`, and `v*` tags. The single **Release checks** status fails if any required job fails, is cancelled, or is skipped. Test result XML is uploaded as workflow artifacts. Configure **Release checks** as a required status in the repository's branch protection/ruleset.

To publish, create and push a new stable `vMAJOR.MINOR.PATCH` tag containing these workflows, then run **Release** against it. For example, choose an unused version and run:

```bash
git switch main
git pull --ff-only
git tag v1.2.3
git push origin v1.2.3
gh workflow run release.yml --ref v1.2.3
```

The workflow rejects branches and prerelease tags, reruns CI, then builds and tests both architectures before pushing architecture-specific GHCR tags. Only after both succeed does it assemble the versioned multi-platform images, create the GitHub release, and advance the `latest` tags for Watchtower. Version tags include the `v` (for example, `ghcr.io/rummerlab/scholar:v1.2.3`). Publication uses the workflow's `GITHUB_TOKEN` with `packages:write`, with no extra registry secret. The two image tags update sequentially; rerun a failed Release workflow to complete a partial publication. Rerunning an older version deliberately moves `latest` back to that version.

Use this workflow for releases; direct publication through the GitHub UI bypasses the checks and image publication. The workflow does not SSH to the Odroid: Watchtower handles container updates once the deployment uses GHCR images. No test suite guarantees upstream services or live websites remain available; these checks protect the repository's API contracts and production images.

### Security scanning

GitHub CodeQL default setup is enabled for this repo. Ensure **Python** is included under Settings → Code security → CodeQL analysis (Actions alone will not cover these path-injection findings). Keep secrets out of git: `.env` and `google-credentials.json` are gitignored — use `.env.template` as the template only.

## Starting the API server

**Docker (production-style):** the `web` image starts **[Gunicorn](https://docs.gunicorn.org/)** (`server:app`), not Flask’s development server. Access logs and errors go to stdout/stderr for `docker compose logs`.

```bash
docker compose pull
docker compose up -d --no-build
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
| `/scholar/<id>/gscholar` | GET | Profile and citation totals without publications; `?include_publications=1` includes them |
| `/scholar/<id>/publications` | GET | Publications page; `?limit=50&offset=0` (maximum limit 200) |
| `/altmetric/<doi>` | GET | Altmetric score for a DOI (cached 2 weeks). 401 if not Rummer/Bergseth/Wu |
| `/google-citations/<doi>` | GET | Google Scholar citation count for a DOI (cached 2 weeks). 401 if not Rummer/Bergseth/Wu |
| `/crossref/<doi>` | GET | Crossref works API data for a DOI (cached 1 month). 404 if not found |

### Environment variables

- `SCHOLAR_IMAGE_TAG` – Compose image tag for both services (default: `latest`; use a published version to pin/roll back)
- `FLASK_HOST` – Bind host (default: `0.0.0.0`)
- `FLASK_PORT` – Bind port (default: `5000`)
- `SCHOLAR_DATA_DIR` – Path to scholar JSON files (default: `scholar_data`)
- `CACHE_DIR` – HTTP cache directory (default: `cache`)
- `CACHE_EXPIRE_SECONDS` – Cache expiry (default: 30 days)
- `FRESH_DATA_SECONDS` – Skip full fetch if data is newer (default: 23 hours)
- `MAX_RETRIES`, `RETRY_BASE_DELAY` – Retry settings for Scholar/DOI APIs
- `COAUTHOR_DELAY`, `PUBLICATION_DELAY` – Rate limiting (seconds)
- `LOG_FORMAT` – Set to `json` for structured JSON logs (e.g. in Docker)

## Container releases and nightly dependency updates

Production Compose pulls GHCR images with native ARM64 (Odroid) and AMD64 support. After merging this change and publishing the first release, run from the existing deployment directory:

```bash
git pull --ff-only
docker compose pull
docker compose up -d --no-build --remove-orphans
docker compose ps
```

Keep the same Compose project name and existing volumes/bind mounts. Keep your `.env` and credentials; do not delete production data. The existing Watchtower installation can follow `latest` using the containers' `com.centurylinklabs.watchtower.enable=true` labels. If Watchtower filters names or scopes, include these containers in that configuration. No second Watchtower instance is needed. Watchtower applies new images, while future Compose and environment changes still need `git pull` and `docker compose up`. GHCR packages must be public for anonymous pulls; alternatively configure registry authentication in Docker and Watchtower.

Use the Compose tag variable `SCHOLAR_IMAGE_TAG` to pin both services to a published `vMAJOR.MINOR.PATCH` tag for rollback. Local builds use `docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build` (Scholar must first build its base service with the same two files).

`docker-release.yml` publishes stable `v*` tag pushes, manual runs with an existing version tag, and calls from the nightly updater. It checks the exact release commit, validates both native architectures without secrets or upstream services, publishes the GitHub release, then updates the multi-platform `latest` tags. To publish manually, push an unused stable version tag on `main`, or rerun the Docker release workflow with that existing tag. The normal `GITHUB_TOKEN` needs contents/packages write permission; no PAT is required. Tag publication created by `GITHUB_TOKEN` does not trigger other workflows, so nightly calls the reusable release workflow explicitly.

Nightly updates run at 00:27 Australia/Brisbane. Python direct constraints live in `requirements.txt` and `requirements-dev.txt`; their generated `requirements.lock` and `requirements-dev.lock` pin the full graph with hashes. uv excludes versions uploaded in the last **three days** and keeps upgrades within each locked package's major (within its minor for 0.x), without downgrading. The age gate reduces exposure to newly compromised releases; it does not guarantee package safety.

Only a changed **production** Python graph creates a patch version, tag, GitHub release, and image. This includes transitive and platform-specific runtime changes. Developer tooling and hash-only lock changes are committed after checks but do not release. With no changes, the run does nothing. Tests, lint, and a local production image smoke test run before a production update is committed; the release then checks both architectures. The bot refuses to rebase if `main` changed during validation, and pushes the tested commit/tag atomically. Branch rules must allow this bot to push dependency commits to `main`; failures remain visible in Actions rather than bypassing protections.

To refresh manually, install `uv==0.13.7`, then run `python scripts/dependency_release.py update`. Do not edit generated lockfiles by hand. Nightly uses existing stable tags when choosing the next patch version, even if project metadata previously lagged behind a release.
