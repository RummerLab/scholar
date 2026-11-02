#!/usr/bin/env python3
"""
Revalidate DOI metrics cache: refetch missing/blocked every run; revalidate stale weekly.

Run daily via cron. Phase 1 (every run): warm Crossref cache and fetch DOIs with no
cache or with a warning/blocked cache. Phase 2 (cache older than 7 days): refresh
Crossref cache (1-month) and revalidate scholar/altmetric. Crossref data is shared
by the /crossref endpoint and by Altmetric/Google Scholar (author check and title).
Uses force_refresh for Scholar so we actually try the proxy chain each run (otherwise
cached warning returns immediately and SOCKS5 is never tried). Uses TOR_PROXY first
(3 attempts), then SOCKS5_PROXIES (see .env.template).
"""

import json
import logging
import os
import sys

# Show progress immediately when run via docker exec (no TTY) or cron
print("Loading revalidation script...", file=sys.stderr, flush=True)

from common import PROJECT_ROOT, setup_script  # noqa: E402

setup_script()

STALE_SECONDS = 7 * 24 * 60 * 60  # 7 days
# When Scholar returns this warning, skip Scholar for the rest of the run (still do Altmetric)
SCHOLAR_BLOCKED_ALL_PROXIES = "blocked requests on all proxies"

from src.doi_utils import normalize_doi  # noqa: E402
from src.scholar_citations import (  # noqa: E402
    fetch_altmetric_score,
    fetch_crossref_for_api,
    fetch_google_scholar_citations,
    list_cached_dois_with_scholar_cache,
    list_cached_dois_with_warning,
    list_cached_successful_dois,
    list_cached_successful_dois_older_than,
    touch_scholar_cache,
)


# Unbuffered logging so output appears immediately when run via docker exec / cron
class _FlushingHandler(logging.StreamHandler):
    def emit(self, record):
        super().emit(record)
        self.flush()


_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.root.setLevel(getattr(logging, _level, logging.INFO))
logging.root.handlers.clear()
_h = _FlushingHandler(sys.stderr)
_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.root.addHandler(_h)
log = logging.getLogger(__name__)


# Only revalidate DOIs from these authors (matches Crossref allowlist; others get 401)
REVALIDATION_AUTHOR_NAMES = ("Rummer", "Bergseth", "Wu")


def _all_dois_from_scholar_data() -> set[str]:
    """Collect DOIs from scholar_data JSON files for Rummer, Bergseth, Wu only (others 401)."""
    from src.path_safety import safe_path_under

    data_dir = os.environ.get("SCHOLAR_DATA_DIR", "scholar_data")
    if not os.path.isabs(data_dir):
        data_dir = os.path.join(PROJECT_ROOT, data_dir)
    data_dir = os.path.abspath(data_dir)
    dois: set[str] = set()
    if not os.path.isdir(data_dir):
        return dois
    for name in os.listdir(data_dir):
        if not name.endswith(".json"):
            continue
        path = safe_path_under(data_dir, name)
        if path is None:
            continue
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        author_name = (data.get("name") or "").strip()
        if not any(allowed.lower() in author_name.lower() for allowed in REVALIDATION_AUTHOR_NAMES):
            continue
        for pub in data.get("publications") or []:
            doi = (pub.get("doi") or "").strip()
            if doi:
                dois.add(normalize_doi(doi))
    return dois


def main() -> int:
    log.info("Starting revalidation: loading DOIs from scholar_data and cache state")
    all_dois = _all_dois_from_scholar_data()
    successful = list_cached_successful_dois()
    with_warning = list_cached_dois_with_warning()
    with_scholar_cache = list_cached_dois_with_scholar_cache()
    stale = list_cached_successful_dois_older_than(STALE_SECONDS)

    # Phase 1 (every run): no scholar cache yet + blocked/warning — refetch. Skip DOIs that already have scholar cache (e.g. 401) so they are not refetched every run.
    missing = all_dois - successful - with_scholar_cache
    refetch = missing | with_warning

    if not all_dois:
        log.info("No DOIs found in scholar_data (empty or missing directory)")
        return 0

    log.info(
        "DOI counts: %d total in scholar_data, %d missing cache, %d with warning/blocked, %d stale (>7d)",
        len(all_dois),
        len(missing),
        len(with_warning & successful),
        len(stale),
    )

    total_ok = 0
    total_fail = 0
    refetch_list = sorted(refetch)
    stale_list = sorted(stale)
    skip_scholar = False  # set when Scholar is blocked on all proxies; skip Scholar for rest of run

    if refetch_list:
        log.info(
            "Phase 1: refetching %d DOIs (missing: %d, blocked/warning: %d)",
            len(refetch_list),
            len(missing),
            len(with_warning & successful),
        )
        for i, doi in enumerate(refetch_list, 1):
            try:
                # Warm Crossref cache (1-week) so Altmetric/Scholar use it
                fetch_crossref_for_api(doi, force_refresh=False)
                a = fetch_altmetric_score(doi, force_refresh=False)
                if skip_scholar:
                    s_found = False
                    s_warning = None
                    touch_scholar_cache(doi)
                else:
                    # Force refresh so we actually try proxies (otherwise cached warning returns immediately)
                    s = fetch_google_scholar_citations(doi, force_refresh=True)
                    s_found = s.found
                    s_warning = getattr(s, "warning", None)
                    if s_warning and SCHOLAR_BLOCKED_ALL_PROXIES in s_warning:
                        skip_scholar = True
                        log.info(
                            "Google Scholar blocked on all proxies; skipping Scholar for remaining %d DOIs",
                            len(refetch_list) - i,
                        )
                if a.found or s_found:
                    total_ok += 1
                    status = "ok"
                    if skip_scholar and not s_found:
                        status = "ok (Scholar skipped - blocked)"
                    elif s_warning:
                        status = "ok (citations: %s)" % (s_warning or "blocked")
                    log.info("Phase 1 [%d/%d] %s - %s", i, len(refetch_list), doi, status)
                else:
                    total_fail += 1
                    log.info("Phase 1 [%d/%d] %s - fail (no data)", i, len(refetch_list), doi)
            except Exception as e:
                total_fail += 1
                log.warning("Phase 1 [%d/%d] %s - error: %s", i, len(refetch_list), doi, e)
        log.info("Phase 1 done: %d ok, %d failed", total_ok, total_fail)

    phase2_ok = 0
    phase2_fail = 0
    if stale_list:
        log.info("Phase 2: revalidating %d DOIs with cache older than 7 days", len(stale_list))
        for i, doi in enumerate(stale_list, 1):
            try:
                # Refresh Crossref cache (1-week) for stale DOIs
                fetch_crossref_for_api(doi, force_refresh=True)
                a = fetch_altmetric_score(doi, force_refresh=False)
                if skip_scholar:
                    s_found = False
                    s_warning = None
                    touch_scholar_cache(doi)
                else:
                    s = fetch_google_scholar_citations(doi, force_refresh=True)
                    s_found = s.found
                    s_warning = getattr(s, "warning", None)
                    if s_warning and SCHOLAR_BLOCKED_ALL_PROXIES in s_warning:
                        skip_scholar = True
                        log.info(
                            "Google Scholar blocked on all proxies; skipping Scholar for remaining %d DOIs",
                            len(stale_list) - i,
                        )
                if a.found or s_found:
                    phase2_ok += 1
                    total_ok += 1
                    status = "ok"
                    if skip_scholar and not s_found:
                        status = "ok (Scholar skipped - blocked)"
                    elif s_warning:
                        status = "ok (citations: %s)" % (s_warning or "blocked")
                    log.info("Phase 2 [%d/%d] %s - %s", i, len(stale_list), doi, status)
                else:
                    phase2_fail += 1
                    total_fail += 1
                    log.info("Phase 2 [%d/%d] %s - fail (no data)", i, len(stale_list), doi)
            except Exception as e:
                phase2_fail += 1
                total_fail += 1
                log.warning("Phase 2 [%d/%d] %s - error: %s", i, len(stale_list), doi, e)
        log.info("Phase 2 done: %d ok, %d failed", phase2_ok, phase2_fail)

    log.info(
        "Revalidation complete: %d ok, %d failed (refetch: %d, stale: %d)",
        total_ok,
        total_fail,
        len(refetch_list),
        len(stale_list),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
